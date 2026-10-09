"""Consumidor MQTT → PostgreSQL.

QoS 1, sessão persistente (``clean_session=False``) e disciplina de ack
obrigatória (load-bearing): ``manual_ack_set(True)`` e ``client.ack()``
SÓ depois do commit da transação do lote. Se o commit falha, não acka e
não descarta — o broker redeliverá na reconexão e o dedupe do banco
(``ON CONFLICT DO NOTHING``, decisão D6) absorve a redelivery.

NUNCA logar ``msg.payload`` — coordenadas são sensíveis; logue tópico e contagens.
A CA em base64 (``RASTRO_MQTT_CA_B64``) segue a mesma regra: loga-se o caminho do
arquivo temporário, nunca o conteúdo.
"""
from __future__ import annotations

import atexit
import base64
import binascii
import logging
import os
import shutil
import socket
import tempfile
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

CA_CERT_ENV = "RASTRO_MQTT_CA_CERT"
CA_B64_ENV = "RASTRO_MQTT_CA_B64"
CA_PEM_INICIO = b"-----BEGIN CERTIFICATE-----"


def _remover_ca_temporaria(directory: str, path: str) -> None:
    """atexit: apaga o arquivo da CA e o diretório privado.

    Roda com o interpretador fechando: não pode levantar nada, e tem que ser
    idempotente (o processo pode morrer antes — aí o dono do /tmp limpa).
    """
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.rmdir(directory)
    except OSError:
        shutil.rmtree(directory, ignore_errors=True)


def _escrever_ca_privada(pem: bytes) -> str:
    """PEM → arquivo privado (dir 0700, arquivo 0600) removido no atexit.

    ``O_EXCL`` garante que não escrevemos por cima de nada que já exista no
    diretório (nada legítimo estaria lá, mas /tmp é compartilhado).
    """
    directory = tempfile.mkdtemp(prefix="rastro-ca-")
    os.chmod(directory, 0o700)  # mkdtemp já cria 0700 — garantimos explícito
    path = os.path.join(directory, "ca.pem")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    atexit.register(_remover_ca_temporaria, directory, path)
    return path


def _ca_de_b64(valor: str) -> str:
    """``RASTRO_MQTT_CA_B64`` (base64 de uma linha de um PEM de CA) → caminho.

    Existe porque secret manager montando env nem sempre consegue montar um
    arquivo no contêiner. NUNCA logar ``valor`` nem o PEM: sem encadear a
    exceção original (``from None``) para nada do material vazar por traceback.
    """
    try:
        pem = base64.b64decode(valor, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(
            f"{CA_B64_ENV} não é base64 válido"
        ) from None
    if not pem.startswith(CA_PEM_INICIO):
        raise ValueError(f"{CA_B64_ENV} não é um PEM de certificado")
    path = _escrever_ca_privada(pem)
    log.info("CA do MQTT escrita em arquivo temporário privado: %s", path)
    return path


def _resolver_ca(env: dict) -> str | None:
    """CA do broker: caminho montado (``RASTRO_MQTT_CA_CERT``) OU PEM em base64.

    Os dois juntos é ambiguidade de configuração — errar cedo é mais barato que
    descobrir no handshake qual das duas o processo usou.
    """
    caminho = env.get(CA_CERT_ENV) or None
    b64 = env.get(CA_B64_ENV) or None
    if caminho and b64:
        raise ValueError(f"defina só um: {CA_CERT_ENV} ou {CA_B64_ENV}")
    if b64:
        return _ca_de_b64(b64)
    return caminho


CA_WAIT_ENV = "RASTRO_MQTT_CA_WAIT_SECS"
CA_WAIT_PADRAO_SECS = 180.0
CA_WAIT_POLL_SECS = 5.0


def aguardar_ca(
    caminho: str | None,
    env: dict | None = None,
    *,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> bool:
    """Espera o broker publicar a CA em ``caminho`` (volume compartilhado).

    No modo automático o broker gera a PKI no primeiro boot; o ingest pode subir
    antes. Sem caminho, ou com o arquivo já presente, retorna True na hora. Avisa
    uma vez (só o caminho, nunca conteúdo) e consulta a cada 5 s até
    ``RASTRO_MQTT_CA_WAIT_SECS`` (padrão 180); esgotado → False.
    """
    if not caminho or os.path.isfile(caminho):
        return True
    env = os.environ if env is None else env
    espera = float(env.get(CA_WAIT_ENV) or CA_WAIT_PADRAO_SECS)
    log.info("aguardando o broker publicar a CA em %s", caminho)
    prazo = monotonic() + max(0.0, espera)
    while monotonic() < prazo:
        sleep(CA_WAIT_POLL_SECS)
        if os.path.isfile(caminho):
            return True
    return os.path.isfile(caminho)


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

    @classmethod
    def from_env(cls, env: dict | None = None) -> "MqttConfig":
        env = os.environ if env is None else env
        return cls(
            host=env.get("RASTRO_MQTT_HOST", "localhost"),
            port=int(env.get("RASTRO_MQTT_PORT", "8883")),
            username=env.get("RASTRO_MQTT_USERNAME") or None,
            password=env.get("RASTRO_MQTT_PASSWORD") or None,
            ca_cert=_resolver_ca(env),
            client_id=env.get("RASTRO_MQTT_CLIENT_ID")
            or f"rastro-ingest-{socket.gethostname()}",
            topic_prefix=env.get("RASTRO_MQTT_TOPIC_PREFIX", "rastro"),
            keepalive_secs=int(env.get("RASTRO_MQTT_KEEPALIVE_SECS", "60")),
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
    # Sem CA configurada usa-se a store do sistema; na bancada a CA é a nossa —
    # arquivo montado (RASTRO_MQTT_CA_CERT) ou o temporário privado gerado de
    # RASTRO_MQTT_CA_B64. Verificação de hostname ON (tls_set não a desliga).
    client.tls_set(ca_certs=cfg.ca_cert)
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
        native=None,
    ) -> None:
        self._client = client
        self._db = database
        self._native = native  # NativeIngest (WP-D) ou None — caminho legado intacto
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
        # Ingest nativo (WP-D): assinatura extra no MESMO cliente, só quando
        # ativo. Sessão persistente e manual-ack já vêm do build_client.
        if self._native is not None:
            self._native.on_connect(client)

    def on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        # Ingest nativo (WP-D): tópicos sob o root nativo vão para o
        # NativeIngest; ele mesmo nunca propaga exceção (mesma regra abaixo).
        if self._native is not None and self._native.handles(msg.topic):
            self._native.on_message(client, msg)
            return
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
        except Exception as exc:
            # Só o tipo: traceback/mensagem podem conter coordenadas ou texto.
            log.error("FALHA: erro processando mensagem do tópico %s (%s)", msg.topic, type(exc).__name__)
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
            except Exception as exc:
                # Erro OPERACIONAL (banco fora): sem ack, sem descarte — requeue
                # preservando a ordem relativa (mensagens chegadas durante o flush
                # vão DEPOIS das antigas). Só o tipo é logado.
                log.error(
                    "FALHA: lote não commitado — aguardando redelivery (%d msgs, %s)",
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
