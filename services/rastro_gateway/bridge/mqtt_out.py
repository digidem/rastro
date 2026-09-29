"""Publicador MQTT da ponte: QoS 1, sessão persistente, LWT e replay do spool.

Complemento de saída do ``ingest/mqtt_in.py`` (mesmos nomes de env, TLS
obrigatório, paho v2, MQTT v3.1.1, ``clean_session=False``). Contrato:

- LWT ``<prefix>/status/gateway`` = offline retained (queda anormal);
  no connect bem-sucedido publica ``online`` retained e dispara o replay.
- ``publish()`` rc == 0 → mid → span registrado; ``on_publish`` (PUBACK)
  chama ``spool.advance(span)`` (mínima contígua). rc != 0 (janela inflight
  cheia / desconectado) NÃO avança nada — volta pra fila e o loop tenta de novo.
- Crash entre PUBACK e gravação da watermark ⇒ replay ⇒ dedupe do banco (D6)
  absorve. Reconexão é automática; após reconectar, replay do não-ackado.

NUNCA logar payload — só nó/tópico/contagens (regra de sensibilidade).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from rastro_gateway.bridge.spool import Span, Spool

log = logging.getLogger(__name__)

STATUS_TOPIC = "status/gateway"
ONLINE_PAYLOAD = '{"schema":1,"type":"status","state":"online"}'
OFFLINE_PAYLOAD = '{"schema":1,"type":"status","state":"offline"}'
RETRY_BACKOFF_INIT_SECS = 0.5
RETRY_BACKOFF_MAX_SECS = 5.0
# Janela do replay (gate F3 r3 BLOCKER): o mid do paho volta a 1 após 65535 e a
# fila dele SOBRESCREVE na colisão — despejar o backlog inteiro de uma vez perde
# registros em silêncio (spool de 50 MB ≈ 100 mil registros > 4 dias de outage).
REPLAY_WINDOW = 500


@dataclass(frozen=True)
class MqttConfig:
    """Mesmos envs de ``ingest/mqtt_in.MqttConfig``; client_id próprio do gateway
    (duas sessões persistentes com o MESMO client_id se derrubariam no broker)."""

    host: str
    port: int
    username: str | None
    password: str | None
    ca_cert: str | None
    client_id: str
    topic_prefix: str
    keepalive_secs: int
    # "tcp" (porta 8883 direta) ou "websockets" (MQTT sobre HTTPS/443, para quem só
    # tem a 443 aberta: o proxy do CapRover termina o TLS e repassa ao broker)
    transport: str = "tcp"
    ws_path: str = "/mqtt"

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
            or f"rastro-gateway-{socket.gethostname()}",
            topic_prefix=env.get("RASTRO_MQTT_TOPIC_PREFIX", "rastro"),
            keepalive_secs=int(env.get("RASTRO_MQTT_KEEPALIVE_SECS", "60")),
            transport=_transport(env.get("RASTRO_MQTT_TRANSPORT", "tcp")),
            ws_path=env.get("RASTRO_MQTT_WS_PATH") or "/mqtt",
        )


def _transport(valor: str) -> str:
    v = (valor or "tcp").strip().lower()
    if v not in ("tcp", "websockets"):
        raise RuntimeError("RASTRO_MQTT_TRANSPORT inválida: use 'tcp' ou 'websockets'")
    return v


def build_client(cfg: MqttConfig) -> mqtt.Client:
    """paho v2, MQTT v3.1.1, TLS sempre, verificação de hostname ON."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=cfg.client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
        transport=cfg.transport,
    )
    if cfg.transport == "websockets":
        client.ws_set_options(path=cfg.ws_path)
    if cfg.username is not None:
        client.username_pw_set(cfg.username, cfg.password)
    # TLS sempre — o broker não tem listener em texto plano (plano Fase 1).
    # Sem CA configurada usa a store do sistema; na bancada a CA é a nossa.
    client.max_queued_messages_set(1000)  # rede de segurança: rc QUEUE_SIZE → retry
    client.tls_set(ca_certs=cfg.ca_cert)
    client.enable_logger(log)  # TLS/handshake deixam de ser silenciosos
    return client


class MqttOut:
    """Publica records do spool; PUBACK → avanço da watermark (mínima contígua)."""

    def __init__(self, cfg: MqttConfig, spool: Spool, on_advance=None) -> None:
        self.cfg = cfg
        self._spool = spool
        self._on_advance = on_advance
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()  # protege _mids/_early_acks/_publishing
        self._mids: dict[int, Span] = {}  # mid paho → span do spool
        self._early_acks: set[int] = set()  # PUBACK antes do registro do mid
        self._ignore_mids: set[int] = set()  # mids de status (online/offline)
        self._publishing = False  # janela worker: publish→registro (gate F3 B1)
        self._cond = threading.Condition()
        self._pending: deque[tuple[Span, str]] = deque()  # espera paho aceitar
        self._replay = threading.Event()
        self._stop = threading.Event()
        self._looper: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    # --- ciclo de vida ---------------------------------------------------------

    def connect(self) -> None:
        """Sobe cliente + threads. Broker ausente NÃO levanta: captura segue
        no spool e o loop reconecta (F2). Levanta só erro de configuração."""
        client = build_client(self.cfg)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_publish = self._on_publish
        # LWT: queda anormal do gateway vira status offline retained (QoS 1).
        client.will_set(
            f"{self.cfg.topic_prefix}/{STATUS_TOPIC}",
            OFFLINE_PAYLOAD,
            qos=1,
            retain=True,
        )
        self._client = client
        try:
            client.connect(self.cfg.host, self.cfg.port, keepalive=self.cfg.keepalive_secs)
        except OSError:
            log.warning(
                "AVISO: broker MQTT %s:%s indisponível agora — captura segue no spool",
                self.cfg.host,
                self.cfg.port,
            )
        self._looper = threading.Thread(
            target=self._loop_forever, args=(client,),
            name="rastro-bridge-mqtt", daemon=True,
        )
        self._worker = threading.Thread(
            target=self._work, name="rastro-bridge-out", daemon=True
        )
        self._looper.start()
        self._worker.start()

    def _loop_forever(self, client: mqtt.Client) -> None:
        # Reconexão automática; retry da PRIMEIRA conexão também (broker fora no boot).
        client.loop_forever(retry_first_connection=True)

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        client = self._client
        if client is not None:
            try:
                # offline retained na saída limpa — a LWT só cobre queda anormal
                info = client.publish(
                    f"{self.cfg.topic_prefix}/{STATUS_TOPIC}",
                    OFFLINE_PAYLOAD,
                    qos=1,
                    retain=True,
                )
                with self._lock:
                    self._ignore_mids.add(info.mid)  # PUBACK dele não é de dado
            except Exception:
                pass
            client.disconnect()
        if self._looper is not None:
            self._looper.join(timeout=10)
            self._looper = None
        if self._worker is not None:
            self._worker.join(timeout=10)
            self._worker = None

    # --- callbacks paho v2 ------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if getattr(reason_code, "is_failure", False):
            log.error("FALHA: conexão MQTT recusada pelo broker: %s", reason_code)
            return
        log.info(
            "Conectado ao broker MQTT %s:%s (client_id=%s, sessão persistente)",
            self.cfg.host,
            self.cfg.port,
            self.cfg.client_id,
        )
        info = client.publish(
            f"{self.cfg.topic_prefix}/{STATUS_TOPIC}",
            ONLINE_PAYLOAD,
            qos=1,
            retain=True,
        )
        with self._lock:
            self._ignore_mids.add(info.mid)  # PUBACK de status não é de dado
        # Replay do que ainda não está PUBACKed — idempotente pelo dedupe (D6).
        self._replay.set()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        log.warning(
            "Desconectado do broker MQTT (%s) — reconexão automática; spool segura",
            reason_code,
        )

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        """PUBACK (QoS 1) → avanço da watermark pela mínima contígua."""
        with self._lock:
            if mid in self._ignore_mids:
                self._ignore_mids.discard(mid)  # status não toca a watermark
                return
            span = self._mids.pop(mid, None)
            if span is None:
                # early-ack VÁLIDO só dentro da janela publish→registro (corrida
                # rede×worker). Fora dela é mid velho/estrangeiro — o paho REUSA
                # mids após 65535, e guardá-lo confirmaria registro sem PUBACK
                # real (perda silenciosa). Descarta (gate F3 B1).
                if self._publishing:
                    self._early_acks.add(mid)
                return
        self._advance(span)

    # --- publicação ---------------------------------------------------------------

    def publish_record(self, span: Span, payload: str) -> None:
        """Enfileira para publicação — NUNCA bloqueia a captura ao vivo."""
        with self._cond:
            self._pending.append((span, payload))
            self._cond.notify()

    def _work(self) -> None:
        while True:
            if self._stop.is_set():
                return
            if self._replay.is_set():
                self._replay.clear()
                try:
                    self._do_replay()
                except Exception:
                    log.exception("FALHA: replay do spool")
                continue
            with self._cond:
                while (
                    not self._pending
                    and not self._stop.is_set()
                    and not self._replay.is_set()
                ):
                    self._cond.wait(0.5)
                if self._stop.is_set():
                    return
                if self._replay.is_set():
                    continue
                span, payload = self._pending.popleft()
            if self._spool.inflight(span):
                continue  # replay já o publicou (gate F3 r3 NIT)
            client = self._client
            if client is not None and not client.is_connected():
                # (gate F3 r2 BLOCKER) desconectado: o registro JÁ é durável no
                # spool e o replay do próximo on_connect cobre — publicar agora
                # com NO_CONN só engordaria a fila do paho (RAM sem teto) e
                # duplicaria o backlog a cada reconexão.
                continue
            try:
                self._publish(span, payload)
            except Exception:
                # bug inesperado não pode matar o worker — volta pra fila
                log.exception("FALHA: publicando registro — reenfileirado")
                if self._stop.wait(1.0):
                    return
                with self._cond:
                    self._pending.appendleft((span, payload))
                    self._cond.notify()

    def _publish(self, span: Span, payload: str) -> None:
        topic = self._topic_for(payload)
        if topic is None:
            log.error(
                "FALHA: registro sem tipo/node_id no spool (%s) — atravessando sem publicar",
                span[0],
            )
            self._spool.register(span)
            self._spool.advance(span)
            return
        backoff = RETRY_BACKOFF_INIT_SECS
        while not self._stop.is_set():
            client = self._client
            if client is None:
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, RETRY_BACKOFF_MAX_SECS)
                continue
            # Registra na FIFO ANTES do publish: um PUBACK ultrarrápido não
            # pode achar span desconhecido (viraria buraco eterno na watermark).
            self._spool.register(span)
            with self._lock:
                self._publishing = True  # abre a janela do early-ack (B1)
            info = client.publish(topic, payload, qos=1)
            if info.rc in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
                # NO_CONN: o paho MANTÉM a mensagem na própria fila e reenvia na
                # reconexão (clean_session=False) — tratar como falha e retentar
                # duplicaria ~17 mil cópias/dia de outage (gate F3 B2). Janela
                # inflight CHEIA devolve SUCCESS (fica queued), não rc!=0.
                self._after_publish(info.mid, span)
                return
            with self._lock:
                self._publishing = False
            # Erro real de enqueue: NÃO avança nada — retry.
            self._spool.unregister(span)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, RETRY_BACKOFF_MAX_SECS)

    def _do_replay(self) -> None:
        enviados = 0
        client = self._client
        for span, record in self._spool.iter_from(*self._spool.position()):
            if self._stop.is_set():
                return
            c = self._client
            if c is None or not c.is_connected():
                # (gate F3 r2 BLOCKER) caiu no meio do replay: as cópias NO_CONN
                # já estão na fila do paho e sairão no CONNACK; outro replay na
                # reconexão seguinte multiplicaria o backlog (k×N em link instável)
                log.warning("AVISO: replay interrompido pela desconexão — retoma na reconexão")
                return
            if self._spool.inflight(span):
                # já publicado e aguardando PUBACK (fila do paho) — não duplicar
                continue
            # janela: nunca mais que REPLAY_WINDOW mids em voo — a fila do paho é
            # ilimitada e o mid colide/sobrescreve após 65535 (gate F3 r3)
            while (not self._stop.is_set() and c is not None and c.is_connected()
                   and len(self._mids) >= REPLAY_WINDOW):
                time.sleep(0.05)
                c = self._client
            if c is None or not c.is_connected():
                log.warning("AVISO: replay interrompido pela desconexão — retoma na reconexão")
                return
            if record is None:
                log.warning(
                    "AVISO: registro ilegível no spool (%s) — atravessando sem publicar",
                    span[0],
                )
                self._spool.register(span)
                self._spool.advance(span)
                continue
            self._publish(span, record.to_mqtt_payload())
            enviados += 1
        if enviados:
            log.info("Replay do spool: %d registros (re)enviados", enviados)

    def _after_publish(self, mid: int, span: Span) -> None:
        with self._lock:
            self._publishing = False  # fecha a janela do early-ack (B1)
            if mid in self._early_acks:
                self._early_acks.discard(mid)
            elif mid in self._ignore_mids:
                self._ignore_mids.discard(mid)
                return
            else:
                self._mids[mid] = span
                return
        self._advance(span)

    def _advance(self, span: Span) -> None:
        self._spool.advance(span)
        if self._on_advance is not None:
            try:
                self._on_advance(span)
            except Exception:
                log.exception("FALHA: callback on_advance")

    def _topic_for(self, payload: str) -> str | None:
        try:
            obj = json.loads(payload)
            rtype = obj["type"]
            hexid = str(obj["node_id"]).removeprefix("!").lower()
            if not hexid:
                return None
            if rtype == "position":
                return f"{self.cfg.topic_prefix}/positions/{hexid}"
            if rtype == "telemetry":
                return f"{self.cfg.topic_prefix}/telemetry/{hexid}"
        except (KeyError, TypeError, ValueError):
            return None
        return None
