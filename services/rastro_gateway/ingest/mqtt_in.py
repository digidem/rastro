"""Consumidor MQTT → PostgreSQL.

QoS 1, sessão persistente (``clean_session=False``) e disciplina de ack
obrigatória (load-bearing): ``manual_ack_set(True)`` e ``client.ack()``
SÓ depois do commit da transação do lote. Se o commit falha, não acka e
não descarta — o broker redeliverá na reconexão e o dedupe do banco
(``ON CONFLICT DO NOTHING``, decisão D6) absorve a redelivery.

NUNCA logar ``msg.payload`` — coordenadas são sensíveis; logue tópico e contagens.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from rastro_gateway.common import records

log = logging.getLogger(__name__)

BATCH_MAX_RECORDS = 20  # = max_inflight_messages do broker: sem ack, ele entrega 20 por vez (gate F2 R4)
BATCH_MAX_SECS = 5.0
FLUSHER_POLL_SECS = 0.5
ACK_BACKOFF_INIT_SECS = 1.0
ACK_BACKOFF_MAX_SECS = 30.0
TOPIC_SUFFIXES = ("positions/#", "telemetry/#", "status/#")


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    ca_cert: str | None
    client_id: str
    topic_prefix: str
    keepalive_secs: int
    tls_insecure: bool

    @classmethod
    def from_env(cls, env: dict | None = None) -> "MqttConfig":
        env = os.environ if env is None else env
        return cls(
            host=env.get("RASTRO_MQTT_HOST", "localhost"),
            port=int(env.get("RASTRO_MQTT_PORT", "8883")),
            username=env.get("RASTRO_MQTT_USERNAME") or None,
            password=env.get("RASTRO_MQTT_PASSWORD") or None,
            ca_cert=env.get("RASTRO_MQTT_CA_CERT") or None,
            client_id=env.get("RASTRO_MQTT_CLIENT_ID")
            or f"rastro-ingest-{socket.gethostname()}",
            topic_prefix=env.get("RASTRO_MQTT_TOPIC_PREFIX", "rastro"),
            keepalive_secs=int(env.get("RASTRO_MQTT_KEEPALIVE_SECS", "60")),
            tls_insecure=env.get("RASTRO_MQTT_TLS_INSECURE", "")
            .strip()
            .lower()
            in ("1", "true", "yes"),
        )


def build_client(cfg: MqttConfig) -> mqtt.Client:
    """paho v2, MQTT v3.1.1, TLS obrigatório, verificação de hostname ON."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=cfg.client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    if cfg.username is not None:
        client.username_pw_set(cfg.username, cfg.password)
    # TLS sempre — o broker não tem listener em texto plano (plano Fase 1).
    # Sem CA configurada usa-se a store do sistema; na bancada a CA é a nossa.
    client.tls_set(ca_certs=cfg.ca_cert)
    if cfg.tls_insecure:
        client.tls_insecure_set(True)
        log.warning("AVISO: TLS sem verificação — SOMENTE bancada")
    # Ack manual: o PUBACK só sai depois do commit no Postgres (ver Ingester.flush).
    client.manual_ack_set(True)
    client.enable_logger(log)  # falhas de TLS/handshake deixam de ser silenciosas
    return client


class Ingester:
    """Acumula ``(mid, qos, record)``, comitta o lote no DB e só então acka.

    QoS entra no buffer porque o ``ack(mid, qos)`` precisa dela; o lote é
    drenado por tamanho (>= ``batch_max``) ou idade (>= ``batch_secs`` desde
    a primeira mensagem), o que vier primeiro.
    """

    def __init__(
        self,
        client: mqtt.Client,
        database,
        names: dict[int, tuple[str, str]],
        fleet_ids: dict[int, str],
        cfg: MqttConfig,
        batch_max: int = BATCH_MAX_RECORDS,
        batch_secs: float = BATCH_MAX_SECS,
    ) -> None:
        self._client = client
        self._db = database
        self._names = names
        self._fleet_ids = fleet_ids
        self._cfg = cfg
        self._batch_max = batch_max
        self._batch_secs = batch_secs
        self._lock = threading.Lock()  # protege _batch/_batch_started
        self._flush_lock = threading.Lock()  # serializa flushes (rede x timer)
        self._batch: list[tuple[int, int, records.Record]] = []
        self._batch_started: float | None = None
        self._stop = threading.Event()
        self._backoff = ACK_BACKOFF_INIT_SECS
        self._flusher: threading.Thread | None = None
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect

    # --- callbacks paho v2 -------------------------------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if getattr(reason_code, "is_failure", False):
            log.error("FALHA: conexão MQTT recusada pelo broker: %s", reason_code)
            return
        log.info(
            "Conectado ao broker MQTT %s:%s (client_id=%s, sessão persistente)",
            self._cfg.host,
            self._cfg.port,
            self._cfg.client_id,
        )
        # Re-subscribe a cada connect: limpa a sessão antiga no broker e vale
        # para reconexões (o broker retomará a fila QoS 1 da sessão).
        for suffix in TOPIC_SUFFIXES:
            topic = f"{self._cfg.topic_prefix}/{suffix}"
            result, _mid = client.subscribe(topic, qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                log.error("FALHA: subscribe %s recusado (rc=%s)", topic, result)

    def on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        # NUNCA propagar exceção para o paho: o loop_forever morre e o contêiner
        # entra em crash-loop (gate F2 R1). Payload ruim = log + ack e seguir.
        try:
            record = records.parse_mqtt_payload(msg.payload)
            if record is None:
                # Problema do produtor: loga o TÓPICO (nunca o payload) e acka —
                # não travar a fila com mensagem que nunca vai virar dado.
                log.error("FALHA: mensagem sem schema conhecido no tópico %s", msg.topic)
                client.ack(msg.mid, msg.qos)
                return
            if record.TYPE == "status":
                # status/gateway não tem tabela nem dedupe — só log operacional.
                log.info("Status do gateway: %s", record.state)
                client.ack(msg.mid, msg.qos)
                return
            self._enqueue(msg.mid, msg.qos, record)
        except Exception:
            log.exception("FALHA: erro processando mensagem do tópico %s", msg.topic)
            try:
                client.ack(msg.mid, msg.qos)  # não travar a fila por bug nosso
            except Exception:
                pass

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        log.warning(
            "Desconectado do broker MQTT (%s) — reconexão automática; "
            "mensagens não ackadas serão redeliveradas pela sessão persistente",
            reason_code,
        )

    # --- lote ---------------------------------------------------------------

    def _enqueue(self, mid: int, qos: int, record: records.Record) -> None:
        flush_now = False
        with self._lock:
            if not self._batch:
                self._batch_started = time.monotonic()
            self._batch.append((mid, qos, record))
            flush_now = len(self._batch) >= self._batch_max
        if flush_now:
            self.flush()

    def pending(self) -> int:
        with self._lock:
            return len(self._batch)

    def flush(self) -> bool:
        """Comitta o lote atual; acka só após o commit. False se falhou."""
        with self._flush_lock:
            with self._lock:
                batch = self._batch
                self._batch = []
                self._batch_started = None
            if not batch:
                return True
            try:
                novas, dup, veneno = self._db.store_batch(
                    [rec for _, _, rec in batch], self._names, self._fleet_ids
                )
            except Exception:
                # Erro OPERACIONAL (banco fora): sem ack, sem descarte — requeue
                # preservando a ordem relativa (mensagens chegadas durante o flush
                # vão DEPOIS das antigas).
                log.exception(
                    "FALHA: lote não commitado — aguardando redelivery (%d msgs)",
                    len(batch),
                )
                with self._lock:
                    self._batch = batch + self._batch
                    if self._batch_started is None:
                        self._batch_started = time.monotonic()
                return False
            self._backoff = ACK_BACKOFF_INIT_SECS
            try:
                for mid, qos, _rec in batch:
                    self._client.ack(mid, qos)
            except Exception:
                # Ack falhou no meio do lote: os ackados ficam ackados e os
                # restantes são redeliverados pela sessão persistente na
                # reconexão — os dedupe do banco absorve a redelivery.
                log.exception("FALHA: ack de lote commitado interrompido")
            if veneno:
                log.error(
                    "FALHA: %d registro(s) rejeitado(s) pelo banco — ackados e "
                    "descartados (produtor fora do contrato schema 1)",
                    len(veneno),
                )
            log.info("lote: novas=%d duplicadas=%d veneno=%d", novas, dup, len(veneno))
            return True

    # --- thread de timer ----------------------------------------------------

    def start(self) -> None:
        self._flusher = threading.Thread(
            target=self._flush_loop, name="rastro-ingest-flush", daemon=True
        )
        self._flusher.start()

    def stop(self) -> None:
        self._stop.set()
        if self._flusher is not None:
            self._flusher.join(timeout=10)
            self._flusher = None

    def _flush_loop(self) -> None:
        while not self._stop.wait(FLUSHER_POLL_SECS):
            due = False
            with self._lock:
                due = (
                    bool(self._batch)
                    and self._batch_started is not None
                    and (time.monotonic() - self._batch_started) >= self._batch_secs
                )
            if not due:
                continue
            try:
                ok = self.flush()
            except Exception:
                # A thread do timer NUNCA pode morrer em silêncio — se ela morre,
                # lotes ficam sem flush por idade e o broker estoura in-flight.
                log.exception("FALHA: flush periódico")
                ok = False
            if not ok:
                # backoff simples no flush falho — o sono fica nesta thread,
                # nunca na de rede (bloqueá-la mataria o keepalive); sensível ao stop
                self._stop.wait(self._backoff)
                self._backoff = min(self._backoff * 2, ACK_BACKOFF_MAX_SECS)
