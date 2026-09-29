"""Ponte Rastro: Meshtastic SerialInterface → filtro → spool → MQTT.

``on_packet`` tem try/except em VOLTA de tudo — um packet ruim NUNCA derruba
a captura (receita da skill meshtastic). Logs em PT-BR e SEM coordenadas ou
payload: só nó, contagens e tópicos (regra de sensibilidade do AGENTS.md).
"""
from __future__ import annotations

import logging
import os
import re
import signal
import threading
import time
from pathlib import Path

from meshtastic.serial_interface import SerialInterface
from pubsub import pub

from rastro_gateway.bridge.mqtt_out import MqttConfig, MqttOut
from rastro_gateway.bridge.packet_filter import filter_packet
from rastro_gateway.bridge.spool import Spool
from rastro_gateway.common import fleet_names

log = logging.getLogger("rastro.bridge")

DEFAULT_SPOOL_DIR = "/var/lib/rastro-gateway/spool"
DEFAULT_SPOOL_MAX_BYTES = 52_428_800  # 50 MB
DEFAULT_SPOOL_ROTATE_BYTES = 8_388_608  # 8 MB
DEFAULT_POSITION_INTERVAL_SECS = 300
MIN_POSITION_INTERVAL_SECS = 30  # abaixo disso o watchdog vira ruído inútil
SILENCE_FACTOR = 3  # aviso após ~3× o intervalo assumido sem posição
WATCHDOG_POLL_SECS = 60.0
EXIT_CONFIG = 2
EXIT_SERIAL = 3  # systemd Restart=always retenta; sem loop interno de retry

# nome de nó vem do DISPOSITIVO (texto atacável): no log só entra se não
# parecer coordenada/chave/PSK e não tiver quebra de linha (anti log-injection)
_NOME_COORD_RE = re.compile(r"-?\d{1,2}\.\d{2,}\s*[,;/ ]\s*-?\d{1,3}\.\d{2,}")
_NOME_KEY_RE = re.compile(r"meshtastic\.org/e/#|[A-Za-z0-9+/]{22}==|[A-Za-z0-9+/]{32,}")


def _nome_log(nome: str | None) -> str:
    """Nome do nó sanitizado p/ log — nunca coordenada/chave/PSK/multilinha."""
    if not nome or "\n" in nome or "\r" in nome:
        return "<sem nome>" if not nome else "<nome não-imprimível>"
    if _NOME_COORD_RE.search(nome) or _NOME_KEY_RE.search(nome):
        return "<nome não-imprimível>"
    return nome


def _setup_logging() -> None:
    level_name = os.environ.get("RASTRO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # O logger do meshtastic em DEBUG grava o packet INTEIRO (com coordenadas)
    # no journal — regra de sensibilidade proíbe; teto INFO nele (gate F3 R3).
    logging.getLogger("meshtastic").setLevel(max(level, logging.INFO))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if not raw else int(raw)


class Gateway:
    """Liga SerialInterface → filter_packet → Spool → MqttOut."""

    def __init__(self) -> None:
        self._names = fleet_names.load_fleet_names()
        self._serial_port = os.environ.get("RASTRO_GW_SERIAL_PORT") or None
        self._spool_dir = Path(
            os.environ.get("RASTRO_SPOOL_DIR") or DEFAULT_SPOOL_DIR
        )
        self._max_bytes = _env_int("RASTRO_SPOOL_MAX_BYTES", DEFAULT_SPOOL_MAX_BYTES)
        self._rotate_bytes = _env_int(
            "RASTRO_SPOOL_ROTATE_BYTES", DEFAULT_SPOOL_ROTATE_BYTES
        )
        self._pos_interval = _env_int(
            "RASTRO_GW_POSITION_INTERVAL_ASSUMED_SECS",
            DEFAULT_POSITION_INTERVAL_SECS,
        )

        self.spool = Spool(
            self._spool_dir,
            rotate_bytes=self._rotate_bytes,
            max_bytes=self._max_bytes,
        )
        self.mqtt = MqttOut(MqttConfig.from_env(), self.spool)

        self._stop = threading.Event()
        self._pos_lock = threading.Lock()
        self._last_position: dict[int, float] = {}  # node_num → monotonic
        self._labels: dict[int, tuple[str, str]] = {}  # node_num → (friendly, id)
        self._silence_warned: set[int] = set()
        self._watchdog: threading.Thread | None = None
        self.interface: SerialInterface | None = None
        self._exit_code = 0  # pode ser lido antes do run() (on_packet fatal)

    # --- packet ---------------------------------------------------------------

    def on_packet(self, packet, interface=None) -> None:
        try:
            record = filter_packet(packet)
            if record is None:
                return  # silencioso: TEXT_MESSAGE, NODEINFO, lixo...
            # Contrato schema 1 (gate F2 B2/R5): `time` SEMPRE preenchido — sem hora
            # do dispositivo, vale a hora de RECEBIMENTO no gateway com
            # time_source='gateway' (dedupe estável na reentrega). rx_time idem.
            agora = int(time.time())
            record.rx_time = agora
            if record.time is None:
                record.time = agora
                record.time_source = "gateway"
            if record.friendly_name is None:
                record.friendly_name = fleet_names.friendly(
                    record.node_num, record.node_id, self._names
                )
            try:
                span = self.spool.append(record)
            except OSError:
                # disco cheio/perms: perda silenciosa com processo VIVO é o pior
                # estado (R1b-4) — morre p/ o systemd reiniciar e alarmar.
                log.critical(
                    "FALHA: spool não gravável — encerrando p/ reinício (disco/permissão?)"
                )
                self._exit_code = EXIT_SERIAL
                self._stop.set()
                return
            self.mqtt.publish_record(span, record.to_mqtt_payload())
            if record.TYPE == "position":
                with self._pos_lock:
                    self._last_position[record.node_num] = time.monotonic()
                    self._labels[record.node_num] = (
                        record.friendly_name,
                        record.node_id,
                    )
                    self._silence_warned.discard(record.node_num)
                log.info(
                    "posição de %s (%s)", _nome_log(record.friendly_name), record.node_id
                )
            else:
                log.info(
                    "telemetria de %s (%s)",
                    _nome_log(record.friendly_name),
                    record.node_id,
                )
        except Exception:
            log.exception("FALHA: erro processando packet — captura continua")

    # --- watchdog de silêncio ---------------------------------------------------

    def _on_connection_lost(self, interface=None) -> None:
        """USB caiu com o serviço rodando (gate F3 R1): exit 3 p/ o systemd."""
        if self._stop.is_set():
            return  # parada limpa: o close() do reader também publica lost
        log.error(
            "FALHA: conexão com o nó de gateway perdida (serial) — saindo p/ restart"
        )
        self._exit_code = EXIT_SERIAL
        self._stop.set()

    # --- watchdog de silêncio ---------------------------------------------------

    def _watch_loop(self) -> None:
        limite = SILENCE_FACTOR * self._pos_interval
        while not self._stop.wait(WATCHDOG_POLL_SECS):
            agora = time.monotonic()
            silenciosos: list[tuple[int, int]] = []
            with self._pos_lock:
                for num, ultimo in self._last_position.items():
                    idade = agora - ultimo
                    if idade > limite and num not in self._silence_warned:
                        self._silence_warned.add(num)
                        silenciosos.append((num, int(idade)))
            for num, idade in silenciosos:
                friendly, node_id = self._labels.get(
                    num, (f"!{num:08x}", f"!{num:08x}")
                )
                log.warning(
                    "AVISO: %s (%s) sem posição há %ds (esperado ~%ds)",
                    _nome_log(friendly),
                    node_id,
                    idade,
                    self._pos_interval,
                )

    # --- ciclo de vida -----------------------------------------------------------

    def run(self) -> int:
        if self._pos_interval < MIN_POSITION_INTERVAL_SECS:
            log.error(
                "FALHA: RASTRO_GW_POSITION_INTERVAL_ASSUMED_SECS deve ser ≥ %d (recebi %d)",
                MIN_POSITION_INTERVAL_SECS,
                self._pos_interval,
            )
            return EXIT_CONFIG
        # Boot log SEM credenciais — host/porta/prefixo ok, senha nunca.
        log.info(
            "Ponte Rastro: serial=%s spool=%s mqtt=%s:%s prefix=%s nós_na_frota=%d",
            self._serial_port,
            self._spool_dir,
            self.mqtt.cfg.host,
            self.mqtt.cfg.port,
            self.mqtt.cfg.topic_prefix,
            len(self._names),
        )
        try:
            self.mqtt.connect()
        except (OSError, ValueError) as exc:
            log.error("FALHA: cliente MQTT inválido (CA/certificado?): %s", exc)
            return EXIT_CONFIG

        # Subscrições ANTES da SerialInterface (R1b-3): o reader da lib nasce
        # junto com a interface — um connection.lost na janela entre criação e
        # subscribe seria perdido (ponte "viva" sem capturar nada; gate F3 R1).
        pub.subscribe(self.on_packet, "meshtastic.receive")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

        if self._serial_port is None:
            log.error("FALHA: porta serial não configurada (RASTRO_GW_SERIAL_PORT)")
            self.mqtt.stop()
            return EXIT_SERIAL
        try:
            self.interface = SerialInterface(devPath=self._serial_port)
        except Exception as exc:
            log.error("FALHA: porta serial %s indisponível: %s", self._serial_port, exc)
            self.mqtt.stop()
            return EXIT_SERIAL

        self._watchdog = threading.Thread(
            target=self._watch_loop, name="rastro-bridge-watchdog", daemon=True
        )
        self._watchdog.start()

        def _handle_signal(signum, _frame) -> None:
            log.info("Sinal %s recebido — encerrando", signum)
            self._stop.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        try:
            while not self._stop.wait(3600):
                pass
        finally:
            pub.unsubscribe(self.on_packet, "meshtastic.receive")
            pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
            if self.interface is not None:
                try:
                    self.interface.close()
                except Exception:
                    log.exception("FALHA: fechando SerialInterface")
            self.mqtt.stop()
            log.info("Ponte encerrada")
        return self._exit_code


def main() -> int:
    _setup_logging()
    try:
        gateway = Gateway()
    except (ValueError, OSError) as exc:
        log.error("FALHA: configuração inválida: %s", exc)
        return EXIT_CONFIG
    return gateway.run()


if __name__ == "__main__":
    import sys

    sys.exit(main())
