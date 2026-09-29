"""Escrita no PostgreSQL: upsert de nós + posições/telemetria idempotentes (D6).

UMA transação por lote: ``ConnectionPool.connection()`` (psycopg_pool) faz
commit ao sair do bloco sem erro e rollback em exceção — é a transação do
lote; nada acka no MQTT antes dela retornar.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import psycopg
import psycopg.errors
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> "PgConfig":
        env = os.environ if env is None else env
        password = env.get("RASTRO_PG_PASSWORD", "")
        if not password:
            raise RuntimeError("RASTRO_PG_PASSWORD não definida")
        return cls(
            host=env.get("RASTRO_PG_HOST", "localhost"),
            port=int(env.get("RASTRO_PG_PORT", "5432")),
            dbname=env.get("RASTRO_PG_DB", "rastro"),
            user=env.get("RASTRO_PG_USER", "rastro"),
            password=password,
        )


class Db:
    def __init__(self, cfg: PgConfig, min_size: int = 1, max_size: int = 4) -> None:
        self._pool = ConnectionPool(
            min_size=min_size,
            max_size=max_size,
            open=True,
            name="rastro-ingest",
            kwargs={
                "host": cfg.host,
                "port": cfg.port,
                "dbname": cfg.dbname,
                "user": cfg.user,
                "password": cfg.password,
            },
        )

    def close(self) -> None:
        self._pool.close()

    def store_batch(
        self,
        batch: list,
        names: dict[int, tuple[str, str]],
        fleet_ids: dict[int, str],
    ) -> tuple[int, int, list]:
        """Lote de records → (novas, duplicadas, veneno). StatusRecord não tem tabela.

        B1 (gate F2): registro que o banco REJEITA (IntegrityError/DataError) não pode
        travar o pipeline. Lote inteiro numa transação; se estourar, refaz registro a
        registro, cada um no seu ``conn.transaction()`` (commit por registro); o rejeitado entra em ``veneno``
        — o chamador loga FALHA (tópico, nunca payload) e ACKA (problema do produtor).
        Só erro OPERACIONAL (OperationalError: banco caiu) volta para a fila.

        B2 (gate F2): ``pos_time`` = ``to_timestamp(time)`` com ``time_source`` do
        PRÓPRIO record — o produtor preenche ``time`` SEMPRE (hora do gateway quando o
        dispositivo não mandou) e marca ``time_source='gateway'``. ``time=None`` só em
        payload antigo: aí ``clock_timestamp()`` + AVISO (nunca ``now()``, que é igual
        para o lote inteiro e colapsaria o dedupe). ``received_at`` = ``rx_time``
        (recebimento no gateway) quando presente.
        """
        if not batch:
            return (0, 0, [])
        with self._pool.connection() as conn:
            try:
                self._upsert_nodes(conn, batch, names, fleet_ids)
                novas = self._insert_positions(conn, batch)
                novas += self._insert_telemetry(conn, batch)
                return (novas, len(batch) - novas, [])
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError):
                # transação do lote morreu; refaz um a um (commit por registro)
                conn.rollback()
                return self._store_one_by_one(conn, batch, names, fleet_ids)
            except psycopg.errors.OperationalError:
                raise  # banco fora: o chamador reenfileira e NÃO acka

    def _store_one_by_one(
        self, conn, batch: list, names: dict, fleet_ids: dict
    ) -> tuple[int, int, list]:
        """Fallback B1: um commit por registro (conn.transaction); veneno isolado."""
        novas = 0
        veneno: list = []
        for rec in batch:
            if rec.TYPE == "status":
                continue
            try:
                with conn.transaction():
                    self._upsert_nodes(conn, [rec], names, fleet_ids)
                    if rec.TYPE == "position":
                        novas += self._insert_positions(conn, [rec])
                    else:
                        novas += self._insert_telemetry(conn, [rec])
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
                veneno.append(rec)
                log.error(
                    "FALHA: registro rejeitado pelo banco (nó %s/%s): %s — "
 "ackado e descartado; produtor fora do contrato?",
                    getattr(rec, "node_num", "?"),
                    getattr(rec, "node_id", "?"),
                    str(exc).splitlines()[0][:120],
                )
        return (novas, len(batch) - novas - len(veneno), veneno)

    @staticmethod
    def _upsert_nodes(conn, batch: list, names: dict, fleet_ids: dict) -> None:
        seen: dict[int, tuple] = {}
        for rec in batch:
            if rec.TYPE == "status":
                continue
            fleet_name = (names.get(rec.node_num) or ("", ""))[0]
            friendly = rec.friendly_name or fleet_name or None
            seen[rec.node_num] = (
                rec.node_num,
                rec.node_id,
                friendly,
                fleet_ids.get(rec.node_num),
            )
        if not seen:
            return
        sql = """
            INSERT INTO nodes (node_num, node_id, friendly_name, fleet_id,
                               last_seen, updated_at)
            VALUES (%s, %s, %s, %s, now(), now())
            ON CONFLICT (node_num) DO UPDATE SET
                node_id = EXCLUDED.node_id,
                friendly_name = COALESCE(EXCLUDED.friendly_name, nodes.friendly_name),
                fleet_id = COALESCE(EXCLUDED.fleet_id, nodes.fleet_id),
                last_seen = now(),
                updated_at = now()
        """
        with conn.cursor() as cur:
            cur.executemany(sql, list(seen.values()))

    _SQL_POSITION = """
        INSERT INTO positions (node_num, pos_time, time_source, lat_i, lon_i,
                               altitude_m, sats_in_view, hop_limit, snr, rssi,
                               received_at)
        VALUES (%s,
                CASE WHEN %s::double precision IS NULL THEN clock_timestamp()
                     ELSE to_timestamp(%s::double precision) END,
                %s, %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s::double precision IS NULL THEN now()
                     ELSE to_timestamp(%s::double precision) END)
        ON CONFLICT (node_num, pos_time) DO NOTHING
    """

    _SQL_TELEMETRY = """
        INSERT INTO device_telemetry (node_num, telem_time, time_source,
                                      battery_level, voltage, channel_util,
                                      air_util_tx, uptime_s, received_at)
        VALUES (%s,
                CASE WHEN %s::double precision IS NULL THEN clock_timestamp()
                     ELSE to_timestamp(%s::double precision) END,
                %s, %s, %s, %s, %s, %s,
                CASE WHEN %s::double precision IS NULL THEN now()
                     ELSE to_timestamp(%s::double precision) END)
        ON CONFLICT (node_num, telem_time) DO NOTHING
    """

    @classmethod
    def _insert_positions(cls, conn, batch: list) -> int:
        records = [r for r in batch if r.TYPE == "position"]
        if not records:
            return 0
        if any(r.time is None for r in records):
            log.warning(
                "AVISO: posição sem `time` no payload — o contrato schema 1 exige que "
                "o produtor preencha (hora do gateway se o dispositivo não mandou); "
                "usando clock_timestamp() como pos_time (reentrega pode duplicar)"
            )
        rows = [
            (
                r.node_num,
                r.time,
                r.time,
                r.time_source if r.time is not None else "gateway",
                r.lat_i,
                r.lon_i,
                r.altitude_m,
                r.sats,
                r.hop_limit,
                r.snr,
                r.rssi,
                r.rx_time,
                r.rx_time,
            )
            for r in records
        ]
        with conn.cursor() as cur:
            cur.executemany(cls._SQL_POSITION, rows)
            return cur.rowcount  # psycopg3: soma dos rowcounts do executemany

    @classmethod
    def _insert_telemetry(cls, conn, batch: list) -> int:
        records = [r for r in batch if r.TYPE == "telemetry"]
        if not records:
            return 0
        if any(r.time is None for r in records):
            log.warning(
                "AVISO: telemetria sem `time` no payload (contrato schema 1) — "
                "clock_timestamp() usado como telem_time"
            )
        rows = [
            (
                r.node_num,
                r.time,
                r.time,
                r.time_source if r.time is not None else "gateway",
                r.battery_level,
                r.voltage,
                r.channel_util,
                r.air_util_tx,
                r.uptime_s,
                r.rx_time,
                r.rx_time,
            )
            for r in records
        ]
        with conn.cursor() as cur:
            cur.executemany(cls._SQL_TELEMETRY, rows)
            return cur.rowcount
