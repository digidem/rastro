"""Escrita no PostgreSQL: upsert de nós + posições/telemetria idempotentes (D6).

UMA transação por lote: ``ConnectionPool.connection()`` (psycopg_pool) faz
commit ao sair do bloco sem erro e rollback em exceção — é a transação do
lote; nada acka no MQTT antes dela retornar.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import psycopg
import psycopg.errors
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

# Prazo total que o ingester dá ao Postgres no boot (F3b) antes de desistir —
# compose com depends_on healthy ainda deixa janela para restart/volume init.
STARTUP_TIMEOUT_PADRAO_SECS = 120.0

# Arquivo publicado pelo app "-setup" (CapRover) num volume compartilhado com SÓ
# host/porta/sslmode do Postgres — nunca usuário nem senha.
CONN_FILE_ENV = "RASTRO_PG_CONN_FILE"
CONN_FILE_PADRAO = "/rastro-pgconn/conn.env"
CONN_WAIT_ENV = "RASTRO_PG_CONN_WAIT_SECS"
CONN_WAIT_PADRAO_SECS = 300.0
CONN_WAIT_POLL_SECS = 5.0
_CONN_FILE_CHAVES = ("RASTRO_PG_HOST", "RASTRO_PG_PORT", "RASTRO_PG_SSLMODE")


def ler_conn_file(caminho: str) -> dict[str, str]:
    """Lê ``KEY=VALUE`` do conn.env (só HOST/PORT/SSLMODE; o resto é ignorado).

    Arquivo ausente/ilegível → ``{}``. Nunca lê senha: chaves desconhecidas
    (inclusive uma eventual ``RASTRO_PG_PASSWORD``) são descartadas.
    """
    try:
        with open(caminho, encoding="utf-8") as fh:
            linhas = fh.read().splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        if chave in _CONN_FILE_CHAVES:
            out[chave] = valor.strip()
    return out


def aguardar_conn_file(
    env: dict | None = None,
    *,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> bool:
    """Espera o preparo do Postgres publicar o conn.env (como a espera da CA).

    Com ``RASTRO_PG_HOST`` definido, ou com o arquivo já presente, retorna True
    na hora. Senão avisa uma vez e consulta a cada 5 s até
    ``RASTRO_PG_CONN_WAIT_SECS`` (padrão 300); esgotado → False.
    """
    env = os.environ if env is None else env
    if env.get("RASTRO_PG_HOST"):
        return True
    caminho = env.get(CONN_FILE_ENV) or CONN_FILE_PADRAO
    if os.path.isfile(caminho):
        return True
    espera = float(env.get(CONN_WAIT_ENV) or CONN_WAIT_PADRAO_SECS)
    log.info("aguardando o preparo do Postgres publicar a conexão em %s", caminho)
    prazo = monotonic() + max(0.0, espera)
    while monotonic() < prazo:
        sleep(CONN_WAIT_POLL_SECS)
        if os.path.isfile(caminho):
            return True
    return os.path.isfile(caminho)


# Checagem de prontidão do banco no boot. UMA consulta, e SÓ metadados:
# nada de SELECT em positions/device_telemetry (tabelas de histórico com
# milhões de linhas — o boot não pode depender delas).
# O ``to_regclass`` vem antes de cada privilégio dentro do CASE: sem a tabela,
# has_table_privilege/has_column_privilege levantariam erro e encobririam o
# diagnóstico ("tabela ausente" viraria exceção em vez de False limpo).
_CHECK_READY_SQL = """
SELECT
    to_regclass('nodes') IS NOT NULL,
    to_regclass('positions') IS NOT NULL,
    to_regclass('device_telemetry') IS NOT NULL,
    CASE WHEN to_regclass('nodes') IS NULL THEN false
         ELSE has_table_privilege('nodes', 'INSERT') END,
    CASE WHEN to_regclass('positions') IS NULL THEN false
         ELSE has_table_privilege('positions', 'INSERT') END,
    CASE WHEN to_regclass('device_telemetry') IS NULL THEN false
         ELSE has_table_privilege('device_telemetry', 'INSERT') END,
    CASE WHEN to_regclass('positions') IS NULL THEN false
         ELSE has_column_privilege('positions', 'pos_time', 'SELECT') END,
    CASE WHEN to_regclass('device_telemetry') IS NULL THEN false
         ELSE has_column_privilege('device_telemetry', 'telem_time', 'SELECT') END,
    CASE WHEN to_regclass('nodes') IS NULL THEN false
         ELSE has_column_privilege('nodes', 'node_id', 'UPDATE') END,
    current_schemas(false)::text
"""

# O que cada um dos 9 booleanos acima representa — a ordem TEM que bater com a
# do SELECT (é o que aparece no log de boot; ver _CHECK_READY_SQL).
_CHECK_READY_ITENS = (
    "tabela nodes não existe",
    "tabela positions não existe",
    "tabela device_telemetry não existe",
    "falta INSERT em nodes",
    "falta INSERT em positions",
    "falta INSERT em device_telemetry",
    "falta SELECT em positions.pos_time",
    "falta SELECT em device_telemetry.telem_time",
    "falta UPDATE em nodes.node_id",
)


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = ""
    startup_timeout_secs: float = STARTUP_TIMEOUT_PADRAO_SECS

    @classmethod
    def from_env(cls, env: dict | None = None) -> "PgConfig":
        env = os.environ if env is None else env
        password = env.get("RASTRO_PG_PASSWORD", "")
        if not password:
            raise RuntimeError("RASTRO_PG_PASSWORD não definida")
        dbname = env.get("RASTRO_PG_DB") or "rastro"
        # variáveis de ambiente vencem; o conn.env só entra sem RASTRO_PG_HOST
        arq: dict[str, str] = {}
        if not env.get("RASTRO_PG_HOST"):
            arq = ler_conn_file(env.get(CONN_FILE_ENV) or CONN_FILE_PADRAO)
        return cls(
            host=env.get("RASTRO_PG_HOST") or arq.get("RASTRO_PG_HOST") or "localhost",
            port=int(env.get("RASTRO_PG_PORT") or arq.get("RASTRO_PG_PORT") or "5432"),
            sslmode=env.get("RASTRO_PG_SSLMODE") or arq.get("RASTRO_PG_SSLMODE") or "",
            dbname=dbname,
            # sem RASTRO_PG_USER: o bootstrap cria o papel <banco>_ingest
            user=env.get("RASTRO_PG_USER") or f"{dbname}_ingest",
            password=password,
            startup_timeout_secs=float(
                env.get("RASTRO_PG_STARTUP_TIMEOUT_SECS")
                or STARTUP_TIMEOUT_PADRAO_SECS
            ),
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
                # vazio = padrão do libpq (prefer)
                **({"sslmode": cfg.sslmode} if cfg.sslmode else {}),
            },
        )

    def close(self) -> None:
        self._pool.close()

    def check_ready(self) -> None:
        """Boot (F3b): as 3 tabelas existem e temos os privilégios que gravam?

        Uma conexão do pool, UMA consulta, nenhuma linha de histórico lida.
        ``RuntimeError`` lista o que falta e os schemas do search_path (o erro
        clássico de deploy é esquema no schema errado ou role sem GRANT).
        Erro de DISPONIBILIDADE (``psycopg.OperationalError``, inclusive
        ``PoolTimeout``) vaza para o chamador decidir o retry.
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_CHECK_READY_SQL)
                row = cur.fetchone()
        if not row:
            raise RuntimeError("banco não respondeu à checagem de prontidão")
        schemas = row[len(_CHECK_READY_ITENS)] or "{}"
        faltando = [item for item, ok in zip(_CHECK_READY_ITENS, row) if not ok]
        if faltando:
            raise RuntimeError(
                "banco não está pronto: "
                + "; ".join(faltando)
                + f"; schemas no search_path: {schemas}"
            )
        log.info("banco pronto (schemas no search_path: %s)", schemas)

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
