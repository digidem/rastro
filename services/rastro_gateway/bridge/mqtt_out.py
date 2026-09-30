"""Publicador MQTT da ponte: QoS 1, sessão persistente, LWT e replay do spool.

Complemento de saída do ``ingest/mqtt_in.py`` (mesmos nomes de env, TLS
obrigatório, paho v2, MQTT v5, sessão persistente ``clean_start=False``).
Contrato:

- LWT ``<prefix>/status/gateway`` = offline retained (queda anormal);
  no connect bem-sucedido publica ``online`` retained e dispara o replay.
- ``publish()`` rc == 0 → mid → span registrado; ``on_publish`` (PUBACK)
  chama ``spool.advance(span)`` (mínima contígua). rc != 0 (janela inflight
  cheia / desconectado) NÃO avança nada — volta pra fila e o loop tenta de novo.
- PUBACK v5 com reason >= 128 (``is_failure``) = o broker RECUSOU o publish
  (ACL/prefixo/usuário). Em v3.1.1 a recusa chegava como PUBACK comum e o
  registro sumia em silêncio (o bug do TODO 6a, ~195 records perdidos).
  Nesse caso NÃO avança watermark e NÃO conta como entregue: loga FALHA e o
  record volta ao topo da fila com backoff (``_recusa``) — nunca é descartado
  nem republicado em duplicidade (o span segue na FIFO do spool, então o
  replay o pula e a watermark não atravessa o buraco).
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
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from rastro_gateway.bridge.spool import Span, Spool

log = logging.getLogger(__name__)

STATUS_TOPIC = "status/gateway"
ONLINE_PAYLOAD = '{"schema":1,"type":"status","state":"online"}'
OFFLINE_PAYLOAD = '{"schema":1,"type":"status","state":"offline"}'
RETRY_BACKOFF_INIT_SECS = 0.5
RETRY_BACKOFF_MAX_SECS = 5.0
# PUBACK negado (v5, reason >= 128): o broker recusou (ACL/prefixo/usuário) —
# é erro de configuração persistente, não transiente. Re-tentar com backoff
# crescente (5 s → 60 s) para não virar um loop quente martelando o broker;
# volta ao mínimo no primeiro PUBACK aceito.
DENIED_BACKOFF_INIT_SECS = 5.0
DENIED_BACKOFF_MAX_SECS = 60.0
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
    """paho v2, MQTT v5 (PUBACK com reason code — recusa de ACL é detectável),
    TLS sempre, verificação de hostname ON."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=cfg.client_id,
        # v5 não tem clean_session (o paho rejeita a kwarg): a sessão
        # persistente é clean_start=False em connect() — sem ela o paho
        # descartaria os QoS 1 não-ackados ao reconectar e a watermark
        # travaria. O retransmit é client-side (dedupe D6 absorve duplicata).
        protocol=mqtt.MQTTv5,
        transport=cfg.transport,
    )
    # Link de satélite pisca (apagão curto é o normal no GSO): reconectar
    # rápido. O padrão do paho sobe até 120 s — lento demais para um link
    # que volta em segundos; 1–10 s recupera sem castigar o broker.
    client.reconnect_delay_set(min_delay=1, max_delay=10)
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
    """Publica records do spool; PUBACK → avanço da watermark (mínima contígua);
    PUBACK recusado (v5, reason >= 128) → sem avanço + retry com backoff."""

    def __init__(self, cfg: MqttConfig, spool: Spool, on_advance=None) -> None:
        self.cfg = cfg
        self._spool = spool
        self._on_advance = on_advance
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()  # protege _mids/_early_acks/_publishing/_retry_*
        self._mids: dict[int, tuple[Span, str]] = {}  # mid paho → (span, payload)
        self._early_acks: dict[int, object | None] = {}  # mid → reason_code se
        # PUBACK chega antes do registro do mid (None = sucesso; ver _after_publish)
        self._ignore_mids: set[int] = set()  # mids de status (online/offline)
        self._publishing = False  # janela worker: publish→registro (gate F3 B1)
        # Backoff de recusa (PUBACK negado): monotonic até quando o worker
        # espera antes de re-publicar; cresce 5 s → 60 s, zera no 1º ack ok.
        self._retry_backoff = DENIED_BACKOFF_INIT_SECS
        self._retry_after = 0.0
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
            # clean_start=False + SessionExpiryInterval = sessão persistente
            # (sem SessionExpiryInterval > 0, MQTT v5 expira a sessão no disconnect):
            # sem ela o paho descartaria os QoS 1 não-ackados ao reconectar e a
            # watermark travaria. O retransmit é client-side; duplicata absorvida
            # pelo dedupe do banco (D6).
            props = Properties(PacketTypes.CONNECT)
            props.SessionExpiryInterval = 86400  # 24h
            client.connect(
                self.cfg.host,
                self.cfg.port,
                keepalive=self.cfg.keepalive_secs,
                clean_start=False,
                properties=props,
            )
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
        """PUBACK (QoS 1) → avanço da watermark pela mínima contígua.

        MQTT v5: ``reason_code`` >= 128 (``is_failure``) = o broker RECUSOU o
        publish (ACL/prefixo/usuário errado). Em v3.1.1 a recusa chegava como
        PUBACK comum, a watermark avançava e o registro sumia em silêncio
        (~195 perdidos em produção — TODO 6a).
        """
        falha = (
            reason_code
            if (
                getattr(reason_code, "is_failure", False)
                or (
                    isinstance(reason_code, int)
                    and not isinstance(reason_code, bool)
                    and reason_code >= 128
                )
            )
            else None
        )
        a_recusar: tuple[Span, str] | None = None
        a_avancar: Span | None = None
        status_negado = False
        with self._lock:
            if mid in self._ignore_mids:
                self._ignore_mids.discard(mid)  # status não toca a watermark
                status_negado = falha is not None
            else:
                entry = self._mids.pop(mid, None)
                if entry is None:
                    # early-ack VÁLIDO só dentro da janela publish→registro (corrida
                    # rede×worker). Fora dela é mid velho/estrangeiro — o paho REUSA
                    # mids após 65535, e guardá-lo confirmaria registro sem PUBACK
                    # real (perda silênciosa). Descarta (gate F3 B1). O reason_code
                    # (ou None) viaja no dict para _after_publish decidir.
                    if self._publishing:
                        self._early_acks[mid] = falha
                    elif falha is not None:
                        status_negado = True  # recusa sem record conhecido: só loga
                elif falha is not None:
                    a_recusar = entry
                else:
                    # PUBACK aceito → avança (e _advance zera o backoff de recusa)
                    a_avancar = entry[0]
        if status_negado:
            self._log_denied(falha)
        elif a_recusar is not None:
            self._recusa(a_recusar[0], a_recusar[1], falha)
        elif a_avancar is not None:
            self._advance(a_avancar)

    @staticmethod
    def _log_denied(reason_code) -> None:
        log.error(
            "FALHA: publish recusado pelo broker (reason=%s) — verificar "
            "prefixo de tópico/usuário/ACL",
            reason_code,
        )

    def _recusa(self, span: Span, payload: str, reason_code) -> None:
        """PUBACK com reason >= 128: o broker RECUSOU o publish.

        NÃO avança a watermark e NÃO desregistra o span (a posição dele na
        FIFO do spool é o buraco que impede a watermark de atravessar registros
        posteriores ainda ackados). O record volta ao TOPO da fila do worker e
        é re-publicado após o backoff — mantido, não dropado, e no máximo UMA
        cópia na fila (dedupe: se já estiver lá, o replay/recusa não duplica).
        """
        self._log_denied(reason_code)
        with self._lock:
            self._retry_after = time.monotonic() + self._retry_backoff
            self._retry_backoff = min(
                self._retry_backoff * 2, DENIED_BACKOFF_MAX_SECS
            )
        with self._cond:
            # uma cópia por record: recusa cruzando com cópia viva do replay
            # (span recusado segue "inflight", o replay o pula — defesa extra)
            if not any(s == span for s, _pl in self._pending):
                self._pending.appendleft((span, payload))
            self._cond.notify()

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
                # PUBACK negado recentemente (recusa de ACL): espera o backoff
                # com o record AINDA na fila — sem virar loop quente nem remover
                # o record durante o backoff.
                with self._lock:
                    falta = self._retry_after - time.monotonic()
                if falta > 0:
                    self._cond.wait(min(falta, 0.5))
                    continue
                span, payload = self._pending.popleft()
            if self._spool.inflight(span) and self._mid_ativo(span):
                # replay já o publicou e o PUBACK dele ainda NÃO chegou
                # (gate F3 r3 NIT). inflight SEM mid ativo = recusa voltou
                # pra fila → republica (é o retry do _recusa).
                continue
            client = self._client
            if client is not None and not client.is_connected():
                # (gate F3 r2 BLOCKER) desconectado: para registro novo (sem
                # inflight), o replay do próximo on_connect cobre do disco — não
                # engorda a fila do paho. Mas registro RECUSADO já está inflight
                # no spool (o replay o pula para não duplicar): precisa ficar na
                # fila do worker até a reconexão.
                if self._spool.inflight(span):
                    with self._cond:
                        self._pending.appendleft((span, payload))
                    if self._stop.wait(0.2):
                        return
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

    def _mid_ativo(self, span: Span) -> bool:
        """True se o span tem mid publicado aguardando PUBACK (sob _lock)."""
        with self._lock:
            return any(s == span for s, _pl in self._mids.values())

    def _wait_retry_backoff(self) -> bool:
        """Espera até vencer o backoff de recusa (se ativo), re-checando caso
        outra recusa tenha estendido o prazo. Devolve True se _stop disparou."""
        while not self._stop.is_set():
            with self._lock:
                falta = self._retry_after - time.monotonic()
            if falta <= 0:
                return False
            if self._stop.wait(falta):
                return True
        return True

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
        ja_estava_inflight = self._spool.inflight(span)
        while not self._stop.is_set():
            if self._wait_retry_backoff():
                return
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
                # reconexão (sessão persistente) — tratar como falha e retentar
                # duplicaria ~17 mil cópias/dia de outage (gate F3 B2). Janela
                # inflight CHEIA devolve SUCCESS (fica queued), não rc!=0.
                self._after_publish(info.mid, span, payload)
                return
            with self._lock:
                self._publishing = False
            # Erro real de enqueue: se o span é novo, remove da FIFO; se já estava
            # inflight (ex: retentativa de record recusado), PRESERVA na FIFO para
            # não abrir buraco na watermark.
            if not ja_estava_inflight:
                self._spool.unregister(span)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, RETRY_BACKOFF_MAX_SECS)

    def _do_replay(self) -> None:
        enviados = 0
        if self._wait_retry_backoff():
            return
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

    def _after_publish(self, mid: int, span: Span, payload: str) -> None:
        falha: object | None = None
        with self._lock:
            self._publishing = False  # fecha a janela do early-ack (B1)
            if mid in self._early_acks:
                falha = self._early_acks.pop(mid)  # None = sucesso
            elif mid in self._ignore_mids:
                self._ignore_mids.discard(mid)
                return
            else:
                self._mids[mid] = (span, payload)
                return
        if falha is not None:
            # PUBACK NEGADO chegou antes do registro do mid (corrida de rede):
            # mesmo tratamento da via normal — sem avanço, log e retry.
            self._recusa(span, payload, falha)
            return
        self._advance(span)

    def _advance(self, span: Span) -> None:
        with self._lock:
            # PUBACK aceito: zera o backoff de recusa (o broker voltou a aceitar)
            self._retry_backoff = DENIED_BACKOFF_INIT_SECS
            self._retry_after = 0.0
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
