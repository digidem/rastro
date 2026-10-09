"""Ingest nativo Meshtastic (WP-D): roteamento no mesmo cliente paho do legado.

``NativeIngest`` divide o fluxo do cliente MQTT compartilhado: tópicos sob
``<root>/2/e/#`` (``RASTRO_NATIVE_ROOT``, padrão ``univaja/mesh``) vão para o
ingest nativo; todo o resto segue o caminho legado do ``Ingester``. A assinatura
extra entra no ``on_connect`` do ``Ingester`` quando ``RASTRO_NATIVE_ENABLED=1``
— desligado por padrão (feature flag), o caminho legado fica intacto.

Disciplina de ack igual à do legado (load-bearing): lote em memória, commit no
Postgres via ``Db.store_native`` e SÓ ENTÃO ``client.ack(mid, qos)``. Commit
falhou = sem ack e sem descarte — o broker redeliverá na reconexão e o dedupe
(``packet_seen``) absorve a redelivery. Payload malformado NUNCA derruba o
loop: gravado bruto + ackado (problema do produtor, mesma regra do legado).

O ingest NUNCA manda nada ao broker (conta somente leitura): nenhum caminho da
ingestão tem saída para o broker — a publicação é do outbox (WP-E), outro
processo e outra conta. Ver o teste de grep em tests/test_native_integration.py.

NUNCA logar payload, coordenadas ou chave/PSK — só tópico, contagens e kinds.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

from rastro_gateway.native import crypto
from rastro_gateway.native.envelope import decode_envelope, parse_topic
from rastro_gateway.native.model import DecodedEnvelope

log = logging.getLogger(__name__)

ENV_ENABLED = "RASTRO_NATIVE_ENABLED"
ENV_ROOT = "RASTRO_NATIVE_ROOT"
ENV_PSK = "RASTRO_EVU_PSK_B64"

ROOT_PADRAO = "univaja/mesh"
NATIVE_BATCH_MAX_RECORDS = 20  # mesma cota in-flight do broker que o legado (gate F2 R4)
NATIVE_BATCH_MAX_SECS = 5.0
FLUSHER_POLL_SECS = 0.5
ACK_BACKOFF_INIT_SECS = 1.0
ACK_BACKOFF_MAX_SECS = 30.0


@dataclass(frozen=True)
class NativeConfig:
    """Config do ingest nativo; ``psk`` é ``repr=False`` — nunca aparece em log."""

    root: str
    psk: bytes = field(repr=False)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "NativeConfig":
        """Lê e valida a config; ``ValueError`` sem NENHUM material da chave.

        PSK ausente/inválida com a flag ligada → ``ValueError`` (o ``__main__``
        sai com EXIT_CONFIG=2). Validação via ``crypto.expand_psk`` (aceita
        base64 de 1/16/32 bytes; índice 0 = "sem cifra" é rejeitado).
        """
        env = os.environ if env is None else env
        root = (env.get(ENV_ROOT) or ROOT_PADRAO).strip().strip("/")
        b64 = env.get(ENV_PSK, "")
        if not b64:
            raise ValueError(f"{ENV_PSK} não definida (obrigatória com {ENV_ENABLED}=1)")
        try:
            psk = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"{ENV_PSK} não é base64 válido") from None
        try:
            crypto.expand_psk(psk)
        except ValueError as exc:
            raise ValueError(f"{ENV_PSK} rejeitada: {exc}") from None
        return cls(root=root, psk=psk)

    def handles(self, topic: str) -> bool:
        """Tópico pertence ao fluxo nativo? (prefixo ``<root>/``)."""
        return topic.startswith(self.root + "/")


class NativeIngest:
    """Consumidor do fluxo nativo no cliente paho compartilhado.

    Mesma mecânica do ``Ingester``: buffer ``(mid, qos, DecodedEnvelope)``,
    flush por tamanho/idade com ``store_native`` e ack só depois do commit.
    As callbacks não são plugadas direto no paho — o ``Ingester`` roteia (ver
    mqtt_in.on_message/on_connect).
    """

    def __init__(
        self,
        client,
        database,
        cfg: NativeConfig,
        batch_max: int = NATIVE_BATCH_MAX_RECORDS,
        batch_secs: float = NATIVE_BATCH_MAX_SECS,
    ) -> None:
        self._client = client
        self._db = database
        self._cfg = cfg
        self._root = cfg.root
        self._psk = cfg.psk
        self._batch_max = batch_max
        self._batch_secs = batch_secs
        self._lock = threading.Lock()  # protege _batch/_batch_started
        self._flush_lock = threading.Lock()  # serializa flushes (rede x timer)
        self._batch: list[tuple[int, int, DecodedEnvelope]] = []
        self._batch_started: float | None = None
        self._stop = threading.Event()
        self._backoff = ACK_BACKOFF_INIT_SECS
        self._flusher: threading.Thread | None = None

    def handles(self, topic: str) -> bool:
        """Tópico pertence ao fluxo nativo? (delega na config)."""
        return self._cfg.handles(topic)

    # --- callbacks chamadas pelo Ingester (roteamento) -----------------------

    def on_connect(self, client) -> None:
        """Assina o fluxo nativo no MESMO cliente (QoS 1, sessão persistente).

        ``manual_ack_set`` já está ligado no cliente (feito em build_client):
        o PUBACK só sai depois do commit no Postgres, como no legado.
        """
        topic = f"{self._root}/2/e/#"
        result, _mid = client.subscribe(topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            log.error("FALHA: subscribe %s recusado (rc=%s)", topic, result)

    def on_message(self, client, msg) -> None:
        # NUNCA propagar exceção para o paho (o loop_forever morre): payload
        # ruim = grava bruto + acka e segue; bug nosso = loga tópico + acka.
        try:
            parsed = parse_topic(self._root, msg.topic)
            if parsed is None:
                # Tópico sob o root mas fora de <root>/2/e/<canal>/<gw>:
                # grava bruto (reason=topico_invalido) e acka — não travar a fila.
                dec = DecodedEnvelope(kind="malformed", reason="topico_invalido")
                dec.gateway_id = msg.topic.rsplit("/", 1)[-1]
                dec.extra = {"raw": bytes(msg.payload), "topic": msg.topic}
                self._enqueue(msg.mid, msg.qos, dec)
                return
            canal, gw_id = parsed
            dec = decode_envelope(
                msg.payload,
                channel=canal,
                gateway_id_topic=gw_id,
                psk=self._psk,
                now=time.time(),
            )
            dec.extra = {"raw": bytes(msg.payload), "topic": msg.topic}
            self._enqueue(msg.mid, msg.qos, dec)
        except Exception as exc:
            # Só o tipo: a mensagem/traceback pode conter texto de chat ou payload.
            log.error("FALHA: erro processando mensagem nativa do tópico %s (%s)", msg.topic, type(exc).__name__)
            try:
                client.ack(msg.mid, msg.qos)  # não travar a fila por bug nosso
            except Exception:
                pass

    # --- lote ----------------------------------------------------------------

    def _enqueue(self, mid: int, qos: int, dec: DecodedEnvelope) -> None:
        flush_now = False
        with self._lock:
            if not self._batch:
                self._batch_started = time.monotonic()
            self._batch.append((mid, qos, dec))
            flush_now = len(self._batch) >= self._batch_max
        if flush_now:
            self.flush()

    def pending(self) -> int:
        with self._lock:
            return len(self._batch)

    def flush(self) -> bool:
        """Comitta o lote nativo via store_native; acka só após o commit. False se falhou."""
        with self._flush_lock:
            with self._lock:
                batch = self._batch
                self._batch = []
                self._batch_started = None
            if not batch:
                return True
            try:
                counts = self._db.store_native([dec for _, _, dec in batch])
            except Exception as exc:
                # Erro OPERACIONAL (banco fora): sem ack, sem descarte — requeue
                # preservando a ordem relativa (como no legado). Só o tipo é logado.
                log.error(
                    "FALHA: lote nativo não commitado — aguardando redelivery (%d msgs, %s)",
                    len(batch),
                    type(exc).__name__,
                )
                with self._lock:
                    self._batch = batch + self._batch
                    if self._batch_started is None:
                        self._batch_started = time.monotonic()
                return False
            self._backoff = ACK_BACKOFF_INIT_SECS
            try:
                for mid, qos, _dec in batch:
                    self._client.ack(mid, qos)
            except Exception:
                # Ack falhou no meio do lote: ackados ficam ackados; o resto é
                # redeliverado pela sessão persistente e o dedupe absorve.
                log.exception("FALHA: ack de lote nativo commitado interrompido")
            resumo = " ".join(f"{chave}={valor}" for chave, valor in sorted(counts.items()))
            log.info("lote nativo: %d msgs | %s", len(batch), resumo)
            return True

    # --- thread de timer -----------------------------------------------------

    def start(self) -> None:
        self._flusher = threading.Thread(
            target=self._flush_loop, name="rastro-native-flush", daemon=True
        )
        self._flusher.start()

    def stop(self) -> None:
        """Para o flusher e espera o join (mesma disciplina do legado)."""
        self._stop.set()
        if self._flusher is not None:
            self._flusher.join(timeout=10)
            self._flusher = None

    def _flush_loop(self) -> None:
        # A thread do timer NUNCA pode morrer em silêncio (mesma regra do legado):
        # se ela morre, lotes ficam sem flush por idade e o broker estoura in-flight.
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
                log.exception("FALHA: flush periódico nativo")
                ok = False
            if not ok:
                # backoff sensível ao stop (o sono nunca fica na thread de rede).
                self._stop.wait(self._backoff)
                self._backoff = min(self._backoff * 2, ACK_BACKOFF_MAX_SECS)
