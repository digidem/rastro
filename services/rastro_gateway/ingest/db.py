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

# Kinds que decodificam para dado de domínio (base do dedupe ``packet_seen``):
# undecryptable/malformed/opaque NUNCA reclamam o (from_num, packet_id) — o
# pacote opaco pode chegar de novo com PSK válida e aí sim virar domínio.
_KINDS_DE_DOMINIO = ("position", "telemetry", "nodeinfo", "text")

# Janela do dedupe entre gateways: ``packet_seen`` mais velho que isso é
# re-claimado (id reutilizado nunca vira "duplicado eterno") e é o alvo de
# ``prune_packet_seen`` (hook de manutenção, NÃO agendado).
PACKET_SEEN_MAX_AGE_SECS = 7 * 86400

# Duração da locação (lease) durável do ``claim_outbox``: worker que não
# completar o envio dentro desse prazo tem a linha devolvida à fila (outro
# worker pode reivindicar). ``mark_outbox`` transiciona a partir da locação.
OUTBOX_LEASE_SECS = 60

# Prazo total que o ingester dá ao Postgres no boot (F3b) antes de desistir —
# compose com depends_on healthy ainda deixa janela para restart/volume init.
STARTUP_TIMEOUT_PADRAO_SECS = 120.0


def _limpa_texto(valor: str | None) -> str | None:
    """Remove ``\\x00`` — Postgres TEXT rejeita NUL e nomes/texto vêm da malha."""
    if valor is None:
        return None
    return valor.replace("\x00", "")

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

    # -------------------------------------------------------------------------
    # Ingest nativo Meshtastic + chat (WP-B / Tarefas 4 e 5)
    # -------------------------------------------------------------------------

    def store_native(self, envelopes: list) -> dict[str, int]:
        """Processa e armazena envelopes nativos decodificados (DecodedEnvelope).

        Idempotente, transacional (uma transação por lote) com deduplicação
        estrita por (from_num, packet_id) entre gateways e registro em raw_envelopes.
        Envelope que o banco REJEITA (IntegrityError/DataError) não trava o lote:
        SAVEPOINT por envelope, o rejeitado é gravado SÓ como bruto
        (duplicate=false) e o lote segue — ackável (problema do produtor, B1).
        Só erro OPERACIONAL (OperationalError: banco caiu) derruba o lote sem
        ack para o chamador reenfileirar.
        """
        counts = {
            "raw": 0,
            "positions": 0,
            "duplicates": 0,
            "chat": 0,
            "nodeinfo": 0,
            "telemetry": 0,
            "own_downlink": 0,
            "poison": 0,
        }
        if not envelopes:
            return counts

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                for env in envelopes:
                    try:
                        # SAVEPOINT por envelope: rejeição isolada, o resto do
                        # lote segue (mesma regra do _store_one_by_one legado).
                        with conn.transaction():
                            deltas = self._store_envelope(cur, env)
                    except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
                        counts["poison"] += 1
                        log.error(
                            "FALHA: envelope rejeitado pelo banco (from=%s id=%s): %s — "
                            "gravado como bruto e ackado; produtor fora do contrato?",
                            getattr(env, "from_num", "?"),
                            getattr(env, "packet_id", "?"),
                            str(exc).splitlines()[0][:120],
                        )
                        if self._gravar_bruto_veneno(conn, cur, env):
                            counts["raw"] += 1
                        continue
                    except psycopg.errors.OperationalError:
                        raise  # banco fora: lote inteiro volta, NADA é ackado
                    for chave, valor in deltas.items():
                        counts[chave] += valor

        return counts

    @staticmethod
    def _topico_de(env, gw_num: int | None) -> str:
        """Tópico do envelope (do extra ou sintetizado), sem ``\\x00``."""
        extra = env.extra if isinstance(env.extra, dict) else {}
        topico = _limpa_texto(extra.get("topic") or "")
        if topico:
            return topico
        canal = env.channel or "EVU"
        gw_str = env.gateway_id or (f"!{gw_num:08x}" if gw_num else "unknown")
        return f"univaja/mesh/2/e/{canal}/{gw_str}"

    def _gravar_bruto_veneno(self, conn, cur, env) -> bool:
        """Bruto do envelope venenoso: melhor esforço num novo SAVEPOINT.

        O savepoint do envelope rolou para trás TUDO (inclusive o bruto dele);
        sem esta re-gravação, o envelope ruim sumiria da auditoria. Devolve
        True quando o bruto foi gravado. Se até o bruto for rejeitado, loga e
        segue — nunca derruba o lote. Erro OPERACIONAL propaga.
        """
        try:
            with conn.transaction():
                extra = env.extra if isinstance(env.extra, dict) else {}
                cur.execute(
                    """
                    INSERT INTO raw_envelopes (
                        received_at, topic, channel, gateway_num, from_num,
                        packet_id, duplicate, raw
                    ) VALUES (now(), %s, %s, %s, %s, %s, false, %s)
                    """,
                    (
                        self._topico_de(env, getattr(env, "gateway_num", None)),
                        _limpa_texto(env.channel) or "",
                        env.gateway_num,
                        env.from_num,
                        env.packet_id,
                        extra.get("raw") or b"",
                    ),
                )
            return True
        except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc2:
            log.error(
                "FALHA: bruto do envelope venenoso também rejeitado: %s",
                str(exc2).splitlines()[0][:120],
            )
            return False

    def _store_envelope(self, cur, env) -> dict[str, int]:
        """Um envelope dentro do seu SAVEPOINT; devolve os acréscimos a counts.

        Toda exceção de conteúdo (IntegrityError/DataError) sobe para o
        ``store_native`` rolar o savepoint e isolar o envelope.
        """
        deltas = {
            "raw": 0,
            "positions": 0,
            "duplicates": 0,
            "chat": 0,
            "nodeinfo": 0,
            "telemetry": 0,
            "own_downlink": 0,
        }
        # Gateway numérico (do campo explícito ou do ID '!xxxxxxxx')
        gw_num = env.gateway_num
        if gw_num is None and env.gateway_id and env.gateway_id.startswith("!"):
            try:
                gw_num = int(env.gateway_id[1:], 16)
            except ValueError:
                gw_num = None

        # 1. Atualiza status do gateway transmissor
        if gw_num is not None:
            cur.execute(
                """
                INSERT INTO gateway_status (gateway_num, last_uplink, uplinks)
                VALUES (%s, now(), 1)
                ON CONFLICT (gateway_num) DO UPDATE SET
                    last_uplink = now(),
                    uplinks = gateway_status.uplinks + 1
                """,
                (gw_num,),
            )

        # 2. Deduplicação via packet_seen — SÓ kinds que decodificam para
        # domínio (undecryptable/malformed/opaque não reclamam o (from, id):
        # podem voltar depois com PSK válida e aí sim virar domínio).
        is_duplicate = False
        if (
            env.kind in _KINDS_DE_DOMINIO
            and env.from_num is not None
            and env.packet_id is not None
        ):
            cur.execute(
                """
                INSERT INTO packet_seen (from_num, packet_id, first_gateway, first_seen)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (from_num, packet_id) DO UPDATE SET
                    first_gateway = EXCLUDED.first_gateway,
                    first_seen = EXCLUDED.first_seen
                WHERE packet_seen.first_seen <= now() - (%s || ' seconds')::interval
                RETURNING 1
                """,
                (env.from_num, env.packet_id, gw_num, PACKET_SEEN_MAX_AGE_SECS),
            )
            if cur.fetchone() is None:
                is_duplicate = True

        if is_duplicate:
            deltas["duplicates"] += 1

        # 3. Gravação obrigatória do envelope bruto. received_at é CHEGADA
        # (now()); rx_time do gateway nunca é relógio de referência aqui.
        raw_bytes = b""
        if isinstance(env.extra, dict):
            raw_bytes = env.extra.get("raw") or b""

        cur.execute(
            """
            INSERT INTO raw_envelopes (
                received_at, topic, channel, gateway_num, from_num, packet_id, duplicate, raw
            ) VALUES (now(), %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self._topico_de(env, gw_num),
                _limpa_texto(env.channel) or "",
                gw_num,
                env.from_num,
                env.packet_id,
                is_duplicate,
                raw_bytes,
            ),
        )
        deltas["raw"] += 1

        # Se for duplicata de pacote já visto, descarta das tabelas de domínio
        if is_duplicate:
            return deltas

        # 4. Tabelas de domínio conforme tipo de payload
        # (a) Posição geográfica
        if env.kind == "position" and env.position is not None and env.from_num is not None:
            pos = env.position
            node_id = f"!{env.from_num:08x}"
            cur.execute(
                """
                INSERT INTO nodes (node_num, node_id, last_seen, updated_at)
                VALUES (%s, %s, now(), now())
                ON CONFLICT (node_num) DO UPDATE SET
                    last_seen = now(),
                    updated_at = now()
                """,
                (env.from_num, node_id),
            )
            if not (pos.lat_i == 0 and pos.lon_i == 0):
                cur.execute(
                    """
                    INSERT INTO positions (
                        node_num, pos_time, time_source, lat_i, lon_i,
                        altitude_m, sats_in_view, hop_limit, snr, rssi,
                        received_at, packet_id, gateway_num, time_flag, observed_at
                    ) VALUES (
                        %s, to_timestamp(%s::double precision), %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        now(),
                        %s, %s, %s,
                        CASE WHEN %s::double precision IS NULL THEN NULL ELSE to_timestamp(%s::double precision) END
                    )
                    ON CONFLICT (node_num, pos_time) DO NOTHING
                    """,
                    (
                        env.from_num,
                        pos.time,
                        pos.time_source,
                        pos.lat_i,
                        pos.lon_i,
                        pos.altitude_m,
                        pos.sats,
                        env.hop_limit,
                        env.snr,
                        env.rssi,
                        env.packet_id,
                        gw_num,
                        pos.time_flag,
                        env.rx_time,
                        env.rx_time,
                    ),
                )
                if cur.rowcount > 0:
                    deltas["positions"] += 1

        # (b) Informações do nó (NodeInfo)
        elif env.kind == "nodeinfo" and env.nodeinfo is not None and env.from_num is not None:
            info = env.nodeinfo
            # node_id SEMPRE derivado de from_num: o user.id da malha não é
            # controlado por nós e colide com o UNIQUE de nodes.node_id.
            node_id = f"!{env.from_num:08x}"
            cur.execute(
                """
                INSERT INTO node_info (node_num, long_name, short_name, hw_model, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (node_num) DO UPDATE SET
                    long_name = EXCLUDED.long_name,
                    short_name = EXCLUDED.short_name,
                    hw_model = COALESCE(EXCLUDED.hw_model, node_info.hw_model),
                    updated_at = now()
                """,
                (
                    env.from_num,
                    _limpa_texto(info.long_name) or "",
                    _limpa_texto(info.short_name) or "",
                    _limpa_texto(info.hw_model),
                ),
            )
            cur.execute(
                """
                INSERT INTO nodes (node_num, node_id, friendly_name, last_seen, updated_at)
                VALUES (%s, %s, %s, now(), now())
                ON CONFLICT (node_num) DO UPDATE SET
                    friendly_name = COALESCE(EXCLUDED.friendly_name, nodes.friendly_name),
                    last_seen = now(),
                    updated_at = now()
                """,
                (
                    env.from_num,
                    node_id,
                    _limpa_texto(info.long_name) or info.short_name or None,
                ),
            )
            deltas["nodeinfo"] += 1

        # (c) Mensagem de chat / texto
        elif env.kind == "text" and env.text is not None and env.from_num is not None and env.packet_id is not None:
            txt = env.text
            # 6. Downlink nosso ecoando de volta (re-uplink LoRa/broker): o
            # remetente é um gateway virtual — não é chat do barco.
            cur.execute(
                "SELECT 1 FROM virtual_gateways WHERE virtual_node_num = %s LIMIT 1",
                (env.from_num,),
            )
            if cur.fetchone() is not None:
                deltas["own_downlink"] += 1
                return deltas
            # 7. boat_id do VÍNCULO VIGENTE em boat_devices (não confia em
            # dado do envelope). Vigência: valid_from <= agora < COALESCE(valid_to, ∞).
            cur.execute(
                """
                SELECT boat_id FROM boat_devices
                WHERE node_num = %s
                  AND valid_from <= now()
                  AND (valid_to IS NULL OR valid_to > now())
                ORDER BY valid_from DESC
                LIMIT 1
                """,
                (env.from_num,),
            )
            boat_row = cur.fetchone()
            boat_id = boat_row[0] if boat_row else None
            # Janela do dedupe: a UNIQUE(from_num, packet_id) é permanente, mas o
            # dedupe do packet_seen expira em PACKET_SEEN_MAX_AGE_SECS. Passada a
            # janela, o (from, id) reutilizado com texto novo ATUALIZA a linha
            # antiga (o papel ingest não tem DELETE); dentro da janela, a
            # redelivery cai no ON CONFLICT DO NOTHING abaixo.
            cur.execute(
                """
                UPDATE chat_messages
                SET text = %s, is_alert = %s,
                    observed_at = CASE WHEN %s::double precision IS NULL THEN NULL ELSE to_timestamp(%s::double precision) END,
                    received_at = now()
                WHERE from_num = %s AND packet_id = %s
                  AND received_at < now() - (%s || ' seconds')::interval
                """,
                (
                    _limpa_texto(txt.text),
                    txt.is_alert,
                    env.rx_time,
                    env.rx_time,
                    env.from_num,
                    env.packet_id,
                    PACKET_SEEN_MAX_AGE_SECS,
                ),
            )
            if cur.rowcount > 0:
                deltas["chat"] += 1
            cur.execute(
                """
                INSERT INTO chat_messages (
                    direction, boat_id, from_num, packet_id, text, is_alert, observed_at, received_at
                ) VALUES (
                    'in', %s, %s, %s, %s, %s,
                    CASE WHEN %s::double precision IS NULL THEN NULL ELSE to_timestamp(%s::double precision) END,
                    now()
                )
                ON CONFLICT (from_num, packet_id) DO NOTHING
                """
                ,
                (
                    boat_id,
                    env.from_num,
                    env.packet_id,
                    _limpa_texto(txt.text),
                    txt.is_alert,
                    env.rx_time,
                    env.rx_time,
                ),
            )
            if cur.rowcount > 0:
                deltas["chat"] += 1

        # (d) Telemetria do dispositivo + estado de energia (node_power)
        elif env.kind == "telemetry" and env.telemetry is not None and env.from_num is not None:
            telem = env.telemetry
            node_id = f"!{env.from_num:08x}"
            cur.execute(
                """
                INSERT INTO nodes (node_num, node_id, last_seen, updated_at)
                VALUES (%s, %s, now(), now())
                ON CONFLICT (node_num) DO UPDATE SET
                    last_seen = now(),
                    updated_at = now()
                """,
                (env.from_num, node_id),
            )
            cur.execute(
                """
                INSERT INTO device_telemetry (
                    node_num, telem_time, time_source,
                    battery_level, voltage, channel_util, air_util_tx, uptime_s, received_at
                ) VALUES (
                    %s, to_timestamp(%s::double precision), %s,
                    %s, %s, %s, %s, %s,
                    CASE WHEN %s::double precision IS NULL THEN now() ELSE to_timestamp(%s::double precision) END
                )
                ON CONFLICT (node_num, telem_time) DO NOTHING
                """,
                (
                    env.from_num,
                    telem.time,
                    telem.time_source,
                    telem.battery_level,
                    telem.voltage,
                    telem.channel_util,
                    telem.air_util_tx,
                    telem.uptime_s,
                    env.rx_time,
                    env.rx_time,
                ),
            )
            if telem.battery_level is not None or telem.voltage is not None:
                cur.execute(
                    """
                    INSERT INTO node_power (node_num, battery_level, voltage, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (node_num) DO UPDATE SET
                        battery_level = COALESCE(EXCLUDED.battery_level, node_power.battery_level),
                        voltage = COALESCE(EXCLUDED.voltage, node_power.voltage),
                        updated_at = now()
                    """,
                    (env.from_num, telem.battery_level, telem.voltage),
                )
            deltas["telemetry"] += 1

        return deltas

    def prune_packet_seen(self, now=None) -> int:
        """Apaga ``packet_seen`` mais velho que a janela do dedupe (hook).

        Único expurgo restante do sistema: hook de manutenção explícito
        (retorna quantas linhas apagou), NÃO agendado por nada — a janela de
        7 dias já impede dedupe eterno no upsert (``WHERE first_seen <=
        now() - 7 days``); este método só recolhe o lixo antigo.
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM packet_seen
                    WHERE first_seen < (
                        CASE WHEN %s::timestamptz IS NULL THEN now() ELSE %s::timestamptz END
                    ) - (%s || ' seconds')::interval
                    """,
                    (now, now, PACKET_SEEN_MAX_AGE_SECS),
                )
                return cur.rowcount

    def claim_outbox(self, limit: int = 10, per_boat_limit: int = 3, now=None) -> list[dict]:
        """Marca expirados e reivindica 'queued' com locação durável (lease).

        Linhas fora do TTL viram 'expired'. Cada linha reivindicada recebe
        locação: status='sending' + lease_until=agora+OUTBOX_LEASE_SECS —
        workers concorrentes não reivindicam a mesma linha (FOR UPDATE SKIP
        LOCKED) e locação vencida (>60 s) volta a ser reivindicável. As linhas
        permanecem locadas após o commit (lease durável): quem publica é o
        worker reivindicante, e ``mark_outbox`` transiciona a partir da locação
        (de 'sending'/'queued' para o estado final).
        Retorna até `limit` mensagens ainda válidas, com limite por barco
        (``per_boat_limit``, padrão 3): um barco barulhento não trava a fila
        dos outros (ROW_NUMBER por boat_id; None = sem limite por barco).
        Assinatura e chaves de retorno inalteradas (contrato com chat/outbox).
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                agora_sql = (
                    "CASE WHEN %s::timestamptz IS NULL THEN now() "
                    "ELSE %s::timestamptz END"
                )
                # 1. Locação vencida volta para a fila (stale lease)
                cur.execute(
                    f"""
                    UPDATE chat_outbox
                    SET status = 'queued', lease_until = NULL
                    WHERE status = 'sending'
                      AND lease_until IS NOT NULL
                      AND lease_until <= {agora_sql}
                    """,
                    (now, now),
                )
                # 2. Expira o que venceu (enfileirado OU locado)
                cur.execute(
                    f"""
                    UPDATE chat_outbox
                    SET status = 'expired'
                    WHERE status IN ('queued', 'sending')
                      AND expires_at <= {agora_sql}
                    """,
                    (now, now),
                )
                # 3. Claim atômico: ranqueia por barco, trava as eleitas (SKIP
                # LOCKED: outro worker segue sem bloquear); a condição
                # status='queued' re-verificada sob a trava fecha a janela de
                # corrida com um mark_outbox concorrente.
                cur.execute(
                    f"""
                    WITH elegiveis AS (
                        SELECT o.id, o.boat_id, o.created_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY o.boat_id
                                   ORDER BY o.created_at ASC, o.id ASC
                               ) AS rn
                        FROM chat_outbox o
                        WHERE o.status = 'queued'
                          AND o.expires_at > {agora_sql}
                    ),
                    eleitas AS (
                        SELECT e.id
                        FROM elegiveis e
                        WHERE e.rn <= COALESCE(%s, 2147483647)
                        ORDER BY e.created_at ASC, e.id ASC
                        LIMIT %s
                    ),
                    travadas AS (
                        SELECT o.id
                        FROM chat_outbox o
                        JOIN eleitas t ON t.id = o.id
                        WHERE o.status = 'queued'
                        FOR UPDATE OF o SKIP LOCKED
                    )
                    UPDATE chat_outbox o
                    SET status = 'sending',
                        lease_until = {agora_sql} + (%s || ' seconds')::interval
                    FROM travadas x
                    WHERE o.id = x.id
                    RETURNING o.id
                    """,
                    (now, now, per_boat_limit, limit, now, now, OUTBOX_LEASE_SECS),
                )
                ids = [row[0] for row in cur.fetchall()]
                if not ids:
                    return []
                # Ordenação estável para o consumidor (UPDATE..RETURNING não
                # garante ordem); mesmas chaves de retorno de antes.
                cur.execute(
                    """
                    SELECT id, boat_id, text, created_by, created_at, expires_at,
                           status, sent_at, packet_id, error, lease_until
                    FROM chat_outbox
                    WHERE id = ANY(%s)
                    ORDER BY created_at ASC, id ASC
                    """,
                    (ids,),
                )
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]

    def mark_outbox(
        self,
        outbox_id: int,
        status: str,
        packet_id: int | None = None,
        error: str | None = None,
    ) -> bool:
        """Transiciona a mensagem a partir da locação do claim (FIX-5).

        Só transiciona linhas 'sending' (locadas) ou 'queued' (não reivindicadas
        — retrocompatibilidade com marcações diretas). Estados terminais
        ('sent'/'expired'/'failed') não são re-marcados e limpam a locação;
        'queued' sobre linha locada persiste o packet_id SEM derrubar a locação
        (o envio ainda está em curso — o chat persiste o id antes de publicar).
        """
        if status not in ("queued", "sending", "sent", "expired", "failed"):
            raise ValueError(f"status inválido para chat_outbox: {status}")

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE chat_outbox
                    SET status = CASE
                            WHEN chat_outbox.status = 'sending' AND %s = 'queued' THEN 'sending'
                            ELSE %s
                        END,
                        sent_at = CASE WHEN %s = 'sent' THEN now() ELSE sent_at END,
                        packet_id = COALESCE(%s, packet_id),
                        error = %s,
                        lease_until = CASE WHEN %s IN ('sent', 'expired', 'failed') THEN NULL ELSE lease_until END
                    WHERE id = %s
                      AND status IN ('queued', 'sending')
                    """,
                    (status, status, status, packet_id, error, status, outbox_id),
                )
                return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Alertas (WP-D): leituras do ciclo + gravação do estado em alert_state
    # -------------------------------------------------------------------------

    def alert_state_rows(self) -> list[dict]:
        """Linhas de ``alert_state`` como dicts (epochs float; None onde vazio)."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT key, node_num, kind,
                           EXTRACT(EPOCH FROM since)::double precision AS since,
                           CASE WHEN cleared_at IS NULL THEN NULL
                                ELSE EXTRACT(EPOCH FROM cleared_at)::double precision END AS cleared_at
                    FROM alert_state
                    """
                )
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def gateway_uplink_times(self) -> dict[int, float]:
        """``{gateway_num: epoch do último uplink}`` a partir de gateway_status."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT gateway_num, "
                    "EXTRACT(EPOCH FROM last_uplink)::double precision FROM gateway_status"
                )
                return {row[0]: row[1] for row in cur.fetchall()}

    def node_power_readings(self) -> dict[int, dict]:
        """``{node_num: {battery_level, voltage}}`` da leitura mais recente de ``node_power``."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT node_num, battery_level, voltage FROM node_power")
                return {
                    int(row[0]): {"battery_level": row[1], "voltage": row[2]}
                    for row in cur.fetchall()
                }

    def save_alert_events(
        self,
        raised: list[dict],
        cleared: list[dict],
        now=None,
    ) -> None:
        """Grava raises/clears do ciclo em ``alert_state`` (uma transação).

        ``raised``: dicts com ``key/node_num/kind/since`` (epoch float ou None).
        ``cleared``: dicts com ``key/cleared_at``. ``since`` só reinicia em linha
        previamente LIMPA — linha ativa mantém o ``since`` original (raise-uma-vez).
        """
        if not raised and not cleared:
            return
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                if raised:
                    cur.executemany(
                        """
                        INSERT INTO alert_state (key, node_num, kind, since, cleared_at)
                        VALUES (%s, %s, %s,
                                CASE WHEN %s::double precision IS NULL THEN now()
                                     ELSE to_timestamp(%s::double precision) END,
                                NULL)
                        ON CONFLICT (key) DO UPDATE SET
                            since = CASE WHEN alert_state.cleared_at IS NOT NULL
                                         THEN EXCLUDED.since
                                         ELSE alert_state.since END,
                            node_num = EXCLUDED.node_num,
                            kind = EXCLUDED.kind,
                            cleared_at = NULL
                        """,
                        [
                            (r["key"], r["node_num"], r["kind"], r.get("since"), r.get("since"))
                            for r in raised
                        ],
                    )
                if cleared:
                    cur.executemany(
                        """
                        UPDATE alert_state
                        SET cleared_at = CASE WHEN %s::double precision IS NULL THEN now()
                                              ELSE to_timestamp(%s::double precision) END
                        WHERE key = %s AND cleared_at IS NULL
                        """
                        ,
                        [(c["cleared_at"], c["cleared_at"], c["key"]) for c in cleared],
                    )

    def retire_alert_kinds(self, kinds: list[str], now=None) -> int:
        """Limpa alertas ativos de kinds removidos do motor (idempotente).

        O motor novo só avalia ``gateway_mudo``/``bateria_critica``: sem este
        retire, linhas antigas de kinds aposentados ficariam ativas para
        sempre (``cleared_at`` NULL) e vazariam na API. Preserva a linha —
        só preenche ``cleared_at`` (histórico intacto).
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE alert_state
                    SET cleared_at = (
                        CASE WHEN %s::double precision IS NULL THEN now()
                             ELSE to_timestamp(%s::double precision) END
                    )
                    WHERE cleared_at IS NULL
                      AND kind = ANY(%s)
                    """,
                    (now, now, kinds),
                )
                return cur.rowcount
