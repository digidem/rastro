"""Ponto de entrada do serviço de envio de chat: ``python -m rastro_gateway.chat``.

Loop com intervalo de 2s processando chat_outbox via MQTT v5 sobre TLS.
Repubblica NodeInfo periodicamente conforme RASTRO_NODEINFO_REPUBLISH_SECS.
Tratamento limpo de SIGTERM/SIGINT.
Chave EVU lida de RASTRO_EVU_PSK_B64 (nunca logada; encerra com código 2 se inválida).
"""
from __future__ import annotations

import base64
import datetime
import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from rastro_gateway.chat.outbox import Outbox
from rastro_gateway.ingest import db
from rastro_gateway.ingest.mqtt_in import _resolver_ca, aguardar_ca
from rastro_gateway.native import crypto

log = logging.getLogger("rastro.chat")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DEPENDENCIA = 3

DEFAULT_REPUBLISH_SECS = 21600.0  # 6 horas
DEFAULT_LOOP_INTERVAL_SECS = 2.0


def _setup_logging() -> None:
    level_name = os.environ.get("RASTRO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def load_psk(env: dict[str, str] | None = None) -> bytes:
    """Carrega e valida a chave EVU a partir de RASTRO_EVU_PSK_B64.

    NUNCA loga a chave ou seu conteúdo em caso de erro.
    Encerra com código 2 em caso de ausência ou formato inválido.
    """
    ambiente = env if env is not None else os.environ
    b64_val = ambiente.get("RASTRO_EVU_PSK_B64")
    if not b64_val or not b64_val.strip():
        log.error("FALHA: RASTRO_EVU_PSK_B64 não definida")
        sys.exit(EXIT_CONFIG)

    try:
        raw_key = base64.b64decode(b64_val.strip(), validate=True)
        # expand_psk valida comprimentos permitidos (1, 16 ou 32) e índice != 0
        crypto.expand_psk(raw_key)
    except Exception:
        log.error("FALHA: RASTRO_EVU_PSK_B64 não é válida ou possui tamanho incorreto")
        sys.exit(EXIT_CONFIG)

    return raw_key


class MqttPublisher:
    """Publicador MQTT v5 com confirmação PUBACK (QoS 1)."""

    def __init__(self, client: mqtt.Client, timeout: float = 10.0) -> None:
        self.client = client
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, bool] = {}

    def is_connected(self) -> bool:
        """Verifica se o cliente MQTT está conectado."""
        if hasattr(self.client, "is_connected"):
            return bool(self.client.is_connected())
        return True

    def on_publish(self, client: mqtt.Client, userdata: Any, mid: int, reason_code: Any, properties: Any) -> None:
        is_failure = (
            getattr(reason_code, "is_failure", False)
            or (isinstance(reason_code, int) and not isinstance(reason_code, bool) and reason_code >= 128)
        )
        with self._lock:
            self._results[mid] = not is_failure
            evt = self._pending.get(mid)
            if evt is not None:
                evt.set()

    def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 1,
        retain: bool = False,
        properties: Properties | None = None,
        *,
        expires_at: float | datetime.datetime | None = None,
        now: float | datetime.datetime | None = None,
        **kwargs: Any,
    ) -> bool:
        if hasattr(self.client, "is_connected") and not self.client.is_connected():
            log.warning("Cliente MQTT desconectado; publicação abortada")
            return False

        # Configura propriedade MQTT v5 Message Expiry Interval (TTL restante em segundos, mín 1s)
        if properties is None and expires_at is not None:
            now_val = time.time() if now is None else now
            if isinstance(now_val, (int, float)):
                now_s = float(now_val)
            elif hasattr(now_val, "timestamp"):
                now_s = now_val.timestamp()
            else:
                now_s = time.time()

            if isinstance(expires_at, (int, float)):
                exp_s = float(expires_at)
            elif hasattr(expires_at, "timestamp"):
                exp_s = expires_at.timestamp()
            elif isinstance(expires_at, str):
                try:
                    exp_s = datetime.datetime.fromisoformat(expires_at).timestamp()
                except Exception:
                    exp_s = now_s + 1.0
            else:
                exp_s = now_s + 1.0

            remaining_ttl = max(1, int(exp_s - now_s))
            properties = Properties(PacketTypes.PUBLISH)
            properties.MessageExpiryInterval = remaining_ttl
        elif properties is None and "expiry_interval" in kwargs:
            remaining_ttl = max(1, int(kwargs["expiry_interval"]))
            properties = Properties(PacketTypes.PUBLISH)
            properties.MessageExpiryInterval = remaining_ttl

        evt = threading.Event()
        try:
            if properties is not None:
                try:
                    info = self.client.publish(topic, payload, qos=qos, retain=retain, properties=properties)
                except TypeError:
                    info = self.client.publish(topic, payload, qos=qos, retain=retain)
            else:
                info = self.client.publish(topic, payload, qos=qos, retain=retain)
        except Exception as exc:
            log.warning("Exceção na chamada de client.publish: %s", type(exc).__name__)
            return False

        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("client.publish retornou código de erro %s", info.rc)
            with self._lock:
                self._results.pop(getattr(info, "mid", None), None)
            return False

        mid = info.mid
        with self._lock:
            if mid in self._results:
                # Early ack: on_publish já disparou antes do registro
                return self._results.pop(mid)
            self._pending[mid] = evt

        if not evt.wait(timeout=self.timeout):
            with self._lock:
                self._pending.pop(mid, None)
                self._results.pop(mid, None)
            log.warning("Timeout aguardando confirmação PUBACK do broker para mid=%d", mid)
            return False

        with self._lock:
            self._pending.pop(mid, None)
            return self._results.pop(mid, False)


def main() -> int:
    _setup_logging()
    psk = load_psk()

    if not db.aguardar_conn_file():
        log.error("FALHA: arquivo de conexão do Postgres não disponível")
        return EXIT_CONFIG

    try:
        pg_cfg = db.PgConfig.from_env()
    except Exception as exc:
        log.error("FALHA: configuração do banco de dados inválida: %s", exc)
        return EXIT_CONFIG

    database = db.Db(pg_cfg)
    try:
        database.check_ready()
    except Exception as exc:
        log.error("FALHA: banco de dados Postgres indisponível ou schema incorreto: %s", exc)
        database.close()
        return EXIT_DEPENDENCIA

    mqtt_host = os.environ.get("RASTRO_MQTT_HOST", "localhost")
    mqtt_port = int(os.environ.get("RASTRO_MQTT_PORT", "8883"))
    mqtt_user = os.environ.get("RASTRO_MQTT_USERNAME", "outbox")
    mqtt_pass = os.environ.get("RASTRO_MQTT_PASSWORD")
    client_id = os.environ.get("RASTRO_MQTT_CLIENT_ID") or f"rastro-outbox-{socket.gethostname()}"
    keepalive = int(os.environ.get("RASTRO_MQTT_KEEPALIVE_SECS", "60"))
    root = os.environ.get("RASTRO_NATIVE_ROOT", "univaja/mesh")

    republish_env = os.environ.get("RASTRO_NODEINFO_REPUBLISH_SECS", str(DEFAULT_REPUBLISH_SECS)).strip()
    try:
        nodeinfo_interval = float(republish_env)
    except ValueError:
        nodeinfo_interval = DEFAULT_REPUBLISH_SECS

    try:
        ca_cert = _resolver_ca(dict(os.environ))
    except ValueError as exc:
        log.error("FALHA: configuração da CA do broker inválida: %s", exc)
        database.close()
        return EXIT_CONFIG

    if not aguardar_ca(ca_cert):
        log.error("FALHA: CA do broker não encontrada em %s", ca_cert)
        database.close()
        return EXIT_CONFIG

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv5,
    )
    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass)
    client.tls_set(ca_certs=ca_cert)

    publisher = MqttPublisher(client)
    client.on_publish = publisher.on_publish

    log.info("Conectando ao broker MQTT %s:%s (usuário=%s)", mqtt_host, mqtt_port, mqtt_user)
    try:
        client.connect(mqtt_host, mqtt_port, keepalive=keepalive)
        client.loop_start()
    except Exception as exc:
        log.error("FALHA: conexão ao broker MQTT recusada: %s", exc)
        database.close()
        return EXIT_DEPENDENCIA

    outbox = Outbox(
        db=database,
        publish_fn=publisher.publish,
        psk=psk,
        root=root,
        client=client,
    )

    stop_event = threading.Event()

    def _sig_handler(signum: int, _frame: Any) -> None:
        log.info("Sinal %s recebido, finalizando serviço...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    log.info("Serviço Outbox em execução (loop a cada %.1fs, republicação NodeInfo a cada %.0fs)", DEFAULT_LOOP_INTERVAL_SECS, nodeinfo_interval)

    last_republish = 0.0
    try:
        while not stop_event.is_set():
            now = time.time()
            if now - last_republish >= nodeinfo_interval:
                try:
                    qtd = outbox.publish_all_nodeinfos()
                    log.info("NodeInfo publicado para %d nós virtuais ativos", qtd)
                    last_republish = now
                except Exception as exc:
                    log.warning("Falha ao republicar NodeInfos: %s", type(exc).__name__)
            else:
                try:
                    novos = outbox.publish_newly_active_nodeinfos()
                    if novos:
                        log.info("NodeInfo publicado para %d novos gateways virtuais ativos", novos)
                except Exception as exc:
                    log.warning("Falha ao publicar NodeInfo para novos gateways virtuais: %s", type(exc).__name__)

            try:
                outbox.run_once()
            except Exception as exc:
                log.error("Erro na rodada do outbox: %s", type(exc).__name__)

            stop_event.wait(timeout=DEFAULT_LOOP_INTERVAL_SECS)
    finally:
        log.info("Desconectando do broker MQTT e fechando conexões...")
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        database.close()
        log.info("Serviço Outbox encerrado limpo.")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
