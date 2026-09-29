"""Ponto de entrada do ingester: ``python -m rastro_gateway.ingest``.

SIGTERM/SIGINT → flush final + disconnect limpo. Backlog da sessão
persistente (QoS 1) é entregue pelo broker na reconexão.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from rastro_gateway.common import fleet_names
from rastro_gateway.ingest import db, mqtt_in

log = logging.getLogger("rastro.ingest")

FLUSH_FINAL_ATTEMPTS = 3
FLUSH_FINAL_BACKOFF_SECS = 2.0


def _setup_logging() -> None:
    level_name = os.environ.get("RASTRO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main() -> int:
    _setup_logging()
    try:
        mqtt_cfg = mqtt_in.MqttConfig.from_env()
        pg_cfg = db.PgConfig.from_env()
    except (ValueError, RuntimeError) as exc:
        log.error("FALHA: configuração inválida: %s", exc)
        return 2

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
        client = mqtt_in.build_client(mqtt_cfg)
    except (OSError, ValueError) as exc:
        log.error("FALHA: cliente MQTT inválido (CA/certificado?): %s", exc)
        return 2
    database = db.Db(pg_cfg)
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
