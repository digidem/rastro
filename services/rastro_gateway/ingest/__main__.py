"""Ponto de entrada do ingester: ``python -m rastro_gateway.ingest``.

SIGTERM/SIGINT → flush final + disconnect limpo. Backlog da sessão
persistente (QoS 1) é entregue pelo broker na reconexão.

Antes de conectar no broker, o boot checa o banco (``Db.check_ready``) com
retry: Postgres subindo é normal no compose, schema/privilégio errado NÃO é —
cada um tem seu exit code (3 e 2, respectivamente).
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

import psycopg

from rastro_gateway.common import fleet_names
from rastro_gateway.ingest import db, mqtt_in

log = logging.getLogger("rastro.ingest")

FLUSH_FINAL_ATTEMPTS = 3
FLUSH_FINAL_BACKOFF_SECS = 2.0
DB_BACKOFF_INIT_SECS = 1.0
DB_BACKOFF_MAX_SECS = 30.0

EXIT_OK = 0
EXIT_CONFIG = 2  # configuração/schema errados: insistir não conserta
EXIT_DEPENDENCIA = 3  # dependência (Postgres) indisponível até o prazo


def _setup_logging() -> None:
    level_name = os.environ.get("RASTRO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _resumo(exc: BaseException) -> str:
    """Primeira linha do erro, limitada — diagnóstico basta, sem multiline nem credencial."""
    linhas = str(exc).strip().splitlines()
    return (linhas[0] if linhas else type(exc).__name__)[:160]


def aguardar_banco(database, timeout_secs: float) -> int:
    """check_ready() com backoff exponencial (F3b): 0 pronto, 2 schema/PRIVILÉGIO,
    3 Postgres indisponível até o prazo.

    ``RuntimeError`` do check é problema de CONFIGURAÇÃO do banco (schema no
    schema errado, role sem GRANT): retry só atrasaria o diagnóstico, então sai
    na hora. ``OperationalError`` (e ``PoolTimeout``, que é subclass) é
    disponibilidade — o Postgres ainda está subindo no compose.
    """
    deadline = time.monotonic() + max(0.0, timeout_secs)
    delay = DB_BACKOFF_INIT_SECS
    while True:
        try:
            database.check_ready()
            return EXIT_OK
        except psycopg.OperationalError as exc:  # inclui timeout do pool
            restante = deadline - time.monotonic()
            if restante <= 0:
                log.error(
                    "FALHA: Postgres indisponível após %.0fs de espera: %s",
                    timeout_secs,
                    _resumo(exc),
                )
                return EXIT_DEPENDENCIA
            espera = min(delay, restante)
            log.warning(
                "Postgres indisponível, tentando de novo em %ds: %s",
                int(round(espera)),
                _resumo(exc),
            )
            time.sleep(espera)
            delay = min(delay * 2, DB_BACKOFF_MAX_SECS)
        except psycopg.Error as exc:
            # erro de SQL/objeto (ex.: coluna que não existe) — definitivo
            log.error("FALHA: checagem do banco falhou: %s", _resumo(exc))
            return EXIT_CONFIG
        except RuntimeError as exc:
            log.error("FALHA: %s", exc)
            return EXIT_CONFIG


def main() -> int:
    _setup_logging()
    try:
        mqtt_cfg = mqtt_in.MqttConfig.from_env()
        pg_cfg = db.PgConfig.from_env()
    except (ValueError, RuntimeError) as exc:
        log.error("FALHA: configuração inválida: %s", exc)
        return EXIT_CONFIG

    # Arquivo de nomes da frota (opcional) lido uma vez no boot (é o cache daqui pra frente).
    names = fleet_names.load_fleet_names()
    fleet_ids = fleet_names.load_fleet_ids()
    # Boot log SEM senha — host/porta/db são ok, credenciais nunca.
    log.info(
        "Ingester Rastro: mqtt=%s:%s prefix=%s pg=%s:%s/%s nós_na_frota=%d",
        mqtt_cfg.host,
        mqtt_cfg.port,
        mqtt_cfg.topic_prefix,
        pg_cfg.host,
        pg_cfg.port,
        pg_cfg.dbname,
        len(names),
    )

    try:
        ca_ok = mqtt_in.aguardar_ca(mqtt_cfg.ca_cert)
    except ValueError as exc:
        log.error("FALHA: configuração inválida: %s", exc)
        return EXIT_CONFIG
    if not ca_ok:
        log.error(
            "FALHA: a CA do broker não apareceu em %s após a espera "
            "(RASTRO_MQTT_CA_WAIT_SECS)",
            mqtt_cfg.ca_cert,
        )
        return EXIT_CONFIG

    try:
        client = mqtt_in.build_client(mqtt_cfg)
    except (OSError, ValueError) as exc:
        log.error("FALHA: cliente MQTT inválido (CA/certificado?): %s", exc)
        return EXIT_CONFIG
    database = db.Db(pg_cfg)
    # O banco tem que estar de pé e autorizado ANTES de conectar no broker: com
    # ack manual, mensagens chegariam e não teriam onde commitar (gate F3b).
    codigo = aguardar_banco(database, pg_cfg.startup_timeout_secs)
    if codigo:
        database.close()
        return codigo
    ing = mqtt_in.Ingester(
        client,
        database,
        names,
        fleet_ids,
        cfg=mqtt_cfg,
    )

    shutdown_done = threading.Event()
    stopping = {"asked": False, "flush_ok": True}

    def _shutdown() -> None:
        """Thread dedicada de desligamento (gate F2 R3): flush final COM o socket
        ainda vivo (acks chegam ao broker) e disconnect só depois — nunca dentro
        do handler de sinal (a thread de sinal pode segurar locks do paho)."""
        ing.stop()
        pendentes = ing.pending()
        if pendentes:
            log.info("Flush final: %d msgs pendentes", pendentes)
        for attempt in range(1, FLUSH_FINAL_ATTEMPTS + 1):
            if ing.flush():
                break
            log.warning(
                "FALHA: flush final %d/%d não commitou — não ackado; "
                "o broker redeliverá na próxima sessão",
                attempt,
                FLUSH_FINAL_ATTEMPTS,
            )
            stopping["flush_ok"] = False
            if attempt < FLUSH_FINAL_ATTEMPTS:
                time.sleep(FLUSH_FINAL_BACKOFF_SECS * attempt)
        client.disconnect()  # faz o loop_forever retornar
        shutdown_done.set()

    def _handle_signal(signum, _frame) -> None:
        if stopping["asked"]:
            return
        stopping["asked"] = True
        log.info(
            "Sinal %s recebido — encerrando com flush final", signal.Signals(signum).name
        )
        threading.Thread(
            target=_shutdown, name="rastro-ingest-shutdown", daemon=True
        ).start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    ing.start()
    # connect_async: o retry da PRIMEIRA conexão fica por conta do
    # loop_forever(retry_first_connection=True) — connect síncrono levantaria
    # se o broker ainda não estivesse pronto (depends_on não garante) (gate F2 R2)
    client.connect_async(mqtt_cfg.host, mqtt_cfg.port, keepalive=mqtt_cfg.keepalive_secs)
    # Reconexão automática; a sessão persistente entrega o backlog QoS 1.
    client.loop_forever(retry_first_connection=True)

    # loop saiu (disconnect do _shutdown): espera o flush terminar via Event —
    # não sono fixo (gate F2 r2: 60 s de sono levava ao SIGKILL 137 do docker)
    shutdown_done.wait(timeout=90)
    database.close()
    if not stopping["flush_ok"]:
        return 1
    log.info("Ingester encerrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
