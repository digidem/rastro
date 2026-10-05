"""Testes de integração com PostgreSQL para o ingest nativo (WP-B).

Levanta automaticamente um PostgreSQL descartável via docker (postgres:16 em
127.0.0.1:55432); se já houver um contêiner ``rastro-test-native-pg`` rodando,
ele é reaproveitado (nada é parado). Sem docker, ignora via pytest.skip.

Alternativamente, aponte RASTRO_TEST_PG_URL para um Postgres que já esteja de pé:
    export RASTRO_TEST_PG_URL=postgresql://postgres:test@127.0.0.1:55432/rastro
    cd services/rastro_gateway && .venv/bin/python -m pytest -q tests/test_native_db.py
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
import pytest
from psycopg import sql

from rastro_gateway.ingest.db import Db, PgConfig
from rastro_gateway.native.model import (
    DecodedEnvelope,
    NodeInfo,
    PositionFix,
    TelemetryFix,
    TextMessage,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_01 = REPO_ROOT / "deploy" / "postgres" / "init" / "01-schema.sql"
SCHEMA_02 = REPO_ROOT / "deploy" / "postgres" / "init" / "02-native.sql"

TEST_PG_PORT = 55432
CONTAINER_NOME = "rastro-test-native-pg"
URL_TESTE = f"postgresql://postgres:test@127.0.0.1:{TEST_PG_PORT}/rastro"
SENHA_INGEST = "ingest-teste"
SENHA_VIEWER = "viewer-teste"


def _docker_disponivel() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def test_pg_url():
    """RASTRO_TEST_PG_URL, ou um contêiner postgres:16 descartável via docker."""
    url = os.environ.get("RASTRO_TEST_PG_URL")
    if url:
        yield url
        return

    if not _docker_disponivel():
        pytest.skip(
            "docker indisponível e RASTRO_TEST_PG_URL não definida — testes de banco ignorados"
        )

    inspectado = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NOME],
        capture_output=True,
        text=True,
    )
    iniciado = False
    if inspectado.returncode != 0:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                CONTAINER_NOME,
                "-p",
                f"127.0.0.1:{TEST_PG_PORT}:5432",
                "-e",
                "POSTGRES_PASSWORD=test",
                "postgres:16",
            ],
            check=True,
            capture_output=True,
        )
        iniciado = True

    prazo = time.monotonic() + 60.0
    admin = (
        f"host=127.0.0.1 port={TEST_PG_PORT} user=postgres password=test "
        "dbname=postgres connect_timeout=3"
    )
    while time.monotonic() < prazo:
        try:
            with psycopg.connect(admin):
                break
        except psycopg.OperationalError:
            time.sleep(1.0)
    else:
        if iniciado:
            subprocess.run(["docker", "stop", CONTAINER_NOME], capture_output=True)
        pytest.fail("Postgres de teste (docker) não ficou pronto em 60s")

    yield URL_TESTE
    # --rm: stop já remove o contêiner. Contêiner reaproveitado fica de pé.
    if iniciado:
        subprocess.run(["docker", "stop", CONTAINER_NOME], capture_output=True)


@pytest.fixture(scope="session")
def pg_session(test_pg_url):
    """Garante que o banco de teste existe e aplica 01+02; cria os papéis de teste.

    Os papéis ``<db>_ingest``/``<db>_viewer`` são criados e a 02-native.sql é
    reaplicada: o DO block da seção 11 deriva os nomes de ``current_database()``
    e concede o conjunto canônico — os testes de privilégio exercitam exatamente
    o SQL de deploy, não um espelho manual.
    """
    conn_info = _parse_url(test_pg_url)
    dbname = conn_info["dbname"]

    # Conecta ao postgres padrão para criar o banco de teste caso não exista
    admin_conn_str = f"host={conn_info['host']} port={conn_info['port']} user={conn_info['user']} password={conn_info['password']} dbname=postgres"
    with psycopg.connect(admin_conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{dbname}"')

    # Conecta ao banco de teste e inicializa esquemas
    target_conn_str = f"host={conn_info['host']} port={conn_info['port']} user={conn_info['user']} password={conn_info['password']} dbname={dbname}"
    with psycopg.connect(target_conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS rastro")
            cur.execute("SET search_path = rastro, public")
            # Aplica 01-schema.sql se tabelas básicas ainda não existirem
            cur.execute("SELECT to_regclass('rastro.nodes')")
            if not cur.fetchone()[0]:
                sql_01 = SCHEMA_01.read_text(encoding="utf-8")
                cur.execute(sql_01)

            # Aplica 02-native.sql
            sql_02 = SCHEMA_02.read_text(encoding="utf-8")
            cur.execute(sql_02)

            # Configura search_path padrão no banco para rastro, public
            cur.execute(f'ALTER DATABASE "{dbname}" SET search_path = rastro, public')

            # Papéis de teste + reaplicação da 02 (o DO block concede quando existem)
            for papel, senha in (
                (f"{dbname}_ingest", SENHA_INGEST),
                (f"{dbname}_viewer", SENHA_VIEWER),
            ):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (papel,))
                if not cur.fetchone():
                    # PASSWORD não aceita bind em utility statement: interpola com escape
                    cur.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(papel),
                            sql.Literal(senha),
                        )
                    )
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA rastro TO {}, {}").format(
                    sql.Identifier(f"{dbname}_ingest"),
                    sql.Identifier(f"{dbname}_viewer"),
                )
            )
            cur.execute("SET search_path = rastro, public")
            cur.execute(sql_02)  # reaplica: agora com os papéis existentes

    yield conn_info


def _conn_de(pg_session, user: str, password: str) -> str:
    """String de conexão como um papel de teste, já no search_path do schema."""
    return (
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={user} password={password} dbname={pg_session['dbname']} "
        "options='-c search_path=rastro,public'"
    )


@pytest.fixture
def db(pg_session):
    """Instância do Db apontando para o banco de teste com limpeza entre testes."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"

    # Limpa dados entre execuções
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                TRUNCATE TABLE
                    positions, device_telemetry, nodes, raw_envelopes,
                    packet_seen, gateway_status, node_info, chat_messages,
                    chat_outbox, virtual_gateways, boat_devices, node_power,
                    alert_state
                CASCADE
                """
            )

    cfg = PgConfig(
        host=pg_session["host"],
        port=pg_session["port"],
        dbname=pg_session["dbname"],
        user=pg_session["user"],
        password=pg_session["password"],
    )
    database = Db(cfg)
    yield database
    database.close()


@pytest.fixture
def db_ingest(db, pg_session):
    """Db autenticado como ``<db>_ingest`` — o mínimo-privilegio real de produção."""
    cfg = PgConfig(
        host=pg_session["host"],
        port=pg_session["port"],
        dbname=pg_session["dbname"],
        user=f"{pg_session['dbname']}_ingest",
        password=SENHA_INGEST,
    )
    database = Db(cfg)
    yield database
    database.close()


# --------------------------------------------------------------------------
# Helpers de envelope
# --------------------------------------------------------------------------

def _env_posicao(
    from_num: int,
    packet_id: int,
    time: int,
    *,
    rx_time: int | None = None,
    gateway_num: int = 1001,
    lat_i: int = -40000000,
    lon_i: int = -70000000,
) -> DecodedEnvelope:
    return DecodedEnvelope(
        kind="position",
        channel="EVU",
        gateway_id=f"!{gateway_num:08x}",
        gateway_num=gateway_num,
        from_num=from_num,
        packet_id=packet_id,
        rx_time=rx_time if rx_time is not None else time,
        hop_limit=3,
        snr=9.5,
        rssi=-75,
        position=PositionFix(
            lat_i=lat_i,
            lon_i=lon_i,
            altitude_m=80,
            sats=7,
            time=time,
            time_source="device",
            time_flag=None,
        ),
        extra={"raw": f"raw-{from_num}-{packet_id}".encode()},
    )


def _env_texto(
    from_num: int,
    packet_id: int,
    texto: str,
    *,
    rx_time: int = 1728300000,
) -> DecodedEnvelope:
    return DecodedEnvelope(
        kind="text",
        channel="EVU",
        gateway_id="!00001001",
        gateway_num=1001,
        from_num=from_num,
        packet_id=packet_id,
        rx_time=rx_time,
        text=TextMessage(text=texto, is_alert=False),
        extra={"raw": f"raw-{from_num}-{packet_id}".encode()},
    )


# --------------------------------------------------------------------------
# Idempotência do schema e fluxos básicos
# --------------------------------------------------------------------------

def test_02_native_sql_twice_is_noop(pg_session):
    """Executar 02-native.sql duas vezes consecutivas é um no-op idempotente."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    sql_02 = SCHEMA_02.read_text(encoding="utf-8")
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # Executa uma vez
            cur.execute(sql_02)
            # Executa a segunda vez: deve ser totalmente tolerado sem exceção
            cur.execute(sql_02)


def test_positions_gain_packet_id_gateway_num_time_flag(db, pg_session):
    """Tabela positions ganha packet_id, gateway_num e time_flag."""
    env = _env_posicao(2852170113, 777888, 1728100000)
    counts = db.store_native([env])
    assert counts["positions"] == 1
    assert counts["raw"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                SELECT packet_id, gateway_num, time_flag, time_source, lat_i, lon_i
                FROM positions
                WHERE node_num = %s
                """,
                (2852170113,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 777888
            assert row[1] == 1001
            assert row[2] is None
            assert row[3] == "device"
            assert row[4] == -40000000
            assert row[5] == -70000000


def test_dedupe_by_from_id_across_two_gateways(db, pg_session):
    """Deduplicação por (from, id) entre gateways: 2º gateway gera raw duplicate=true e nenhum domínio."""
    # Gateway 1 recebe o pacote
    env_gw1 = _env_posicao(2000, 5555, 1728100010, gateway_num=1001)
    c1 = db.store_native([env_gw1])
    assert c1["raw"] == 1
    assert c1["positions"] == 1
    assert c1["duplicates"] == 0

    # Gateway 2 recebe o mesmo pacote (mesmo from_num e packet_id)
    env_gw2 = _env_posicao(2000, 5555, 1728100010, rx_time=1728100011, gateway_num=1002)
    c2 = db.store_native([env_gw2])
    assert c2["raw"] == 1
    assert c2["positions"] == 0  # Nenhum registro de domínio added
    assert c2["duplicates"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # Exatamente 1 linha em positions
            cur.execute("SELECT count(*) FROM positions WHERE node_num = 2000")
            assert cur.fetchone()[0] == 1

            # 2 linhas em raw_envelopes: 1ª duplicate=false, 2ª duplicate=true
            cur.execute("SELECT gateway_num, duplicate FROM raw_envelopes ORDER BY id ASC")
            raw_rows = cur.fetchall()
            assert len(raw_rows) == 2
            assert raw_rows[0] == (1001, False)
            assert raw_rows[1] == (1002, True)


def test_replay_older_valid_position_never_changes_vw_ultima_posicao(db, pg_session):
    """Replay de posição anterior válida nunca altera vw_ultima_posicao."""
    # 1. Posição recente (tempo = 2000)
    env_nova = _env_posicao(3000, 101, 2000, rx_time=2000, gateway_num=1001)
    db.store_native([env_nova])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT lat, lon, pos_time FROM vw_ultima_posicao WHERE node_num = 3000")
            row1 = cur.fetchone()
            assert row1 is not None
            assert row1[0] == pytest.approx(-4.0)
            assert row1[1] == pytest.approx(-7.0)

    # 2. Posição anterior que chega atrasada (tempo = 1000)
    env_antiga = _env_posicao(3000, 102, 1000, rx_time=2500, gateway_num=1001)
    db.store_native([env_antiga])

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # A view continua apontando para a posição com pos_time mais recente (tempo = 2000)
            cur.execute("SELECT lat, lon, pos_time FROM vw_ultima_posicao WHERE node_num = 3000")
            row2 = cur.fetchone()
            assert row2 is not None
            assert row2[0] == pytest.approx(-4.0)
            assert row2[1] == pytest.approx(-7.0)


def test_flagged_time_fallback(db, pg_session):
    """Fix com horário inválido aplica fallback sinalizado (time_source='gateway', time_flag definido)."""
    env = DecodedEnvelope(
        kind="position",
        channel="EVU",
        gateway_id="!gw000001",
        gateway_num=1001,
        from_num=4000,
        packet_id=201,
        rx_time=1728200000,
        position=PositionFix(
            lat_i=-41000000,
            lon_i=-71000000,
            altitude_m=60,
            sats=5,
            time=1728200000,
            time_source="gateway",
            time_flag="invalid_zero",
        ),
    )
    db.store_native([env])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                SELECT time_source, time_flag, observed_at
                FROM positions
                WHERE node_num = 4000
                """
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "gateway"
            assert row[1] == "invalid_zero"
            assert row[2] is not None


def test_chat_insert_and_is_alert(db, pg_session):
    """Mensagem de texto recebida é inserida em chat_messages com flag is_alert."""
    with psycopg.connect(
        _conn_de(pg_session, pg_session["user"], pg_session["password"]),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                INSERT INTO boat_devices (node_num, boat_id, valid_from)
                VALUES (5000, 'barco-01', now() - interval '1 day')
                """
            )

    env = DecodedEnvelope(
        kind="text",
        channel="EVU",
        gateway_id="!f0000001",
        gateway_num=5001,
        from_num=5000,
        packet_id=301,
        rx_time=1728300000,
        text=TextMessage(text="Emergência no rio Javari", is_alert=True),
    )
    counts = db.store_native([env])
    assert counts["chat"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                SELECT direction, boat_id, from_num, packet_id, text, is_alert
                FROM chat_messages
                WHERE from_num = 5000 AND packet_id = 301
                """
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "in"
            assert row[1] == "barco-01"
            assert row[2] == 5000
            assert row[3] == 301
            assert row[4] == "Emergência no rio Javari"
            assert row[5] is True


def test_node_info_upsert(db, pg_session):
    """NodeInfo realiza upsert em node_info e atualiza nodes (sem hw_model em nodes)."""
    env1 = DecodedEnvelope(
        kind="nodeinfo",
        from_num=6000,
        packet_id=401,
        nodeinfo=NodeInfo(
            user_id="!00001770",
            long_name="Barco Javari 1",
            short_name="JV01",
            hw_model="TBEAM",
        ),
    )
    db.store_native([env1])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT long_name, short_name, hw_model FROM node_info WHERE node_num = 6000")
            row1 = cur.fetchone()
            assert row1 == ("Barco Javari 1", "JV01", "TBEAM")

            cur.execute("SELECT friendly_name, hw_model FROM nodes WHERE node_num = 6000")
            node_row1 = cur.fetchone()
            assert node_row1 == ("Barco Javari 1", None)

    # Atualização com novo nome longo
    env2 = DecodedEnvelope(
        kind="nodeinfo",
        from_num=6000,
        packet_id=402,
        nodeinfo=NodeInfo(
            user_id="!00001770",
            long_name="Barco Javari Atualizado",
            short_name="JV01",
            hw_model="TBEAM",
        ),
    )
    db.store_native([env2])

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT long_name FROM node_info WHERE node_num = 6000")
            assert cur.fetchone()[0] == "Barco Javari Atualizado"

            cur.execute("SELECT friendly_name FROM nodes WHERE node_num = 6000")
            assert cur.fetchone()[0] == "Barco Javari Atualizado"


def test_node_power_upsert(db, pg_session):
    """Telemetria com métricas de energia realiza upsert em node_power."""
    env1 = DecodedEnvelope(
        kind="telemetry",
        from_num=7000,
        packet_id=501,
        rx_time=1728400000,
        telemetry=TelemetryFix(
            time=1728400000,
            time_source="device",
            time_flag=None,
            battery_level=88.5,
            voltage=4.12,
        ),
    )
    db.store_native([env1])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT battery_level, voltage FROM node_power WHERE node_num = 7000")
            row1 = cur.fetchone()
            assert row1 is not None
            assert row1[0] == pytest.approx(88.5, 0.01)
            assert row1[1] == pytest.approx(4.12, 0.01)

    # Nova leitura atualiza a mesma linha
    env2 = DecodedEnvelope(
        kind="telemetry",
        from_num=7000,
        packet_id=502,
        rx_time=1728401000,
        telemetry=TelemetryFix(
            time=1728401000,
            time_source="device",
            time_flag=None,
            battery_level=85.0,
            voltage=4.08,
        ),
    )
    db.store_native([env2])

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT battery_level, voltage FROM node_power WHERE node_num = 7000")
            row2 = cur.fetchone()
            assert row2 is not None
            assert row2[0] == pytest.approx(85.0, 0.01)
            assert row2[1] == pytest.approx(4.08, 0.01)


def test_gateway_status_uplink_counter(db, pg_session):
    """gateway_status contabiliza uplinks de cada gateway."""
    env1 = _env_posicao(8100, 601, 100, gateway_num=8001)
    env2 = _env_posicao(8200, 602, 101, gateway_num=8001)
    db.store_native([env1, env2])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT uplinks FROM gateway_status WHERE gateway_num = 8001")
            assert cur.fetchone()[0] == 2


def test_purge_expired_retention_env(db, pg_session, monkeypatch):
    """purge_expired só apaga quando env de retenção definida, sendo no-op quando ausente."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # Insere registro bruto antigo (15 dias atrás)
            cur.execute(
                """
                INSERT INTO raw_envelopes (received_at, topic, channel, raw)
                VALUES (now() - interval '15 days', 'univaja/mesh/2/e/EVU/!gw1', 'EVU', %s)
                """,
                (b"old",),
            )
            # Insere chat antigo (15 dias atrás)
            cur.execute(
                """
                INSERT INTO chat_messages (direction, from_num, packet_id, text, received_at)
                VALUES ('in', 9001, 701, 'mensagem antiga', now() - interval '15 days')
                """
            )

    # 1. Sem variáveis de retenção configuradas: no-op
    monkeypatch.delenv("RASTRO_RAW_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("RASTRO_CHAT_RETENTION_DAYS", raising=False)
    noop_res = db.purge_expired()
    assert noop_res == {"raw": 0, "chat": 0}

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM raw_envelopes")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM chat_messages")
            assert cur.fetchone()[0] == 1

    # 2. Com retenção configurada (ex: 7 dias)
    monkeypatch.setenv("RASTRO_RAW_RETENTION_DAYS", "7")
    monkeypatch.setenv("RASTRO_CHAT_RETENTION_DAYS", "7")
    purged_res = db.purge_expired()
    assert purged_res["raw"] == 1
    assert purged_res["chat"] == 1

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM raw_envelopes")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM chat_messages")
            assert cur.fetchone()[0] == 0


def test_claim_outbox_marks_expired_and_claims_exactly_once(db, pg_session):
    """claim_outbox marca expirados e devolve mensagens pendentes exatamente uma vez."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # 1. Mensagem válida (vence no futuro)
            cur.execute(
                """
                INSERT INTO chat_outbox (boat_id, text, created_by, expires_at)
                VALUES ('b1', 'Mensagem Válida', 'admin', now() + interval '10 minutes')
                RETURNING id
                """
            )
            msg1_id = cur.fetchone()[0]

            # 2. Mensagem já vencida (expires_at no passado)
            cur.execute(
                """
                INSERT INTO chat_outbox (boat_id, text, created_by, expires_at)
                VALUES ('b1', 'Mensagem Expirada', 'admin', now() - interval '5 minutes')
                RETURNING id
                """
            )
            msg2_id = cur.fetchone()[0]

    # Reivindica mensagens
    claimed = db.claim_outbox(limit=10)
    assert len(claimed) == 1
    assert claimed[0]["id"] == msg1_id
    assert claimed[0]["text"] == "Mensagem Válida"

    # Confere que a mensagem vencida foi marcada como 'expired' no banco
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT status FROM chat_outbox WHERE id = %s", (msg2_id,))
            assert cur.fetchone()[0] == "expired"

    # Marca a mensagem como enviada (fluxo real do Outbox após PUBACK)
    ok = db.mark_outbox(msg1_id, status="sent", packet_id=98765)
    assert ok is True

    # Segunda chamada não devolve mais msg1_id nem msg2_id (entregues exatamente uma vez)
    claimed2 = db.claim_outbox(limit=10)
    assert claimed2 == []


# --------------------------------------------------------------------------
# Regressões FIX-1 (achados 2, 3, 5, 6, 7, 8, 9)
# --------------------------------------------------------------------------

def test_nodeinfo_node_id_derived_from_from_num(db, pg_session):
    """Achado 2: nodes.node_id é derivado de from_num, nunca do user.id da malha."""
    env = DecodedEnvelope(
        kind="nodeinfo",
        from_num=0x00001770,  # 6000
        packet_id=411,
        nodeinfo=NodeInfo(
            user_id="!ffffffff",  # colidiria com o UNIQUE se copiado
            long_name="Barco Z",
            short_name="BZ",
            hw_model="TBEAM",
        ),
    )
    db.store_native([env])

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT node_id, hw_model FROM nodes WHERE node_num = 6000")
            row = cur.fetchone()
            assert row == ("!00001770", None)  # derivado; hw_model NUNCA vai p/ nodes

            cur.execute("SELECT hw_model FROM node_info WHERE node_num = 6000")
            assert cur.fetchone() == ("TBEAM",)


def test_text_and_nodeinfo_strip_nul_bytes(db, pg_session):
    """Achado 3 (parte): \\x00 de nomes/texto da malha é removido antes do INSERT."""
    env_txt = _env_texto(5200, 311, "SOS\x00agora")
    env_node = DecodedEnvelope(
        kind="nodeinfo",
        from_num=5201,
        packet_id=312,
        nodeinfo=NodeInfo(
            user_id="!00001451",
            long_name="Ba\x00rco Z",
            short_name="BZ\x00",
            hw_model="TBEAM",
        ),
    )
    counts = db.store_native([env_txt, env_node])
    assert counts["chat"] == 1
    assert counts["nodeinfo"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT text FROM chat_messages WHERE from_num = 5200")
            assert cur.fetchone() == ("SOSagora",)

            cur.execute("SELECT long_name, short_name FROM node_info WHERE node_num = 5201")
            assert cur.fetchone() == ("Barco Z", "BZ")

            cur.execute("SELECT friendly_name FROM nodes WHERE node_num = 5201")
            assert cur.fetchone() == ("Barco Z",)


def test_poison_envelope_raw_and_batch_continues(db, pg_session):
    """Achado 3 (parte): envelope venenoso vira raw duplicate=false; o resto do lote grava.

    pos.time gigante estoura timestamptz (DataError) dentro do savepoint: o
    savepoint rola, o raw é regravado best-effort e o lote segue.
    """
    veneno = _env_posicao(2100, 9001, 9_500_000_000_000)  # ano ~301288: DataError
    bom = _env_posicao(2200, 9002, 1728100000)
    counts = db.store_native([veneno, bom])
    assert counts["poison"] == 1
    assert counts["positions"] == 1
    assert counts["raw"] == 2  # veneno (regravado) + bom
    assert counts["duplicates"] == 0

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM positions WHERE node_num = 2200")
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT count(*) FROM packet_seen")
            assert cur.fetchone()[0] == 1  # o veneno não reclama o (from, id)

            cur.execute(
                "SELECT from_num, duplicate FROM raw_envelopes ORDER BY id ASC"
            )
            assert cur.fetchall() == [(2100, False), (2200, False)]


def test_claim_outbox_per_boat_limit(db, pg_session):
    """Achado 5: fila justa por barco — 12 mensagens de b1 não travam a de b2."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ids_b1: list[int] = []
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            for i in range(12):
                cur.execute(
                    """
                    INSERT INTO chat_outbox (boat_id, text, created_by, expires_at, created_at)
                    VALUES ('b1', %s, 'admin', now() + interval '30 minutes', now() - (%s || ' minutes')::interval)
                    RETURNING id
                    """,
                    (f"msg b1 {i}", 30 - i),
                )
                ids_b1.append(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO chat_outbox (boat_id, text, created_by, expires_at)
                VALUES ('b2', 'msg b2', 'admin', now() + interval '30 minutes')
                RETURNING id
                """
            )
            id_b2 = cur.fetchone()[0]

    # Padrão: 3 do b1 + a do b2
    claimed = db.claim_outbox(limit=10)
    ids = [m["id"] for m in claimed]
    assert len(claimed) == 4
    assert id_b2 in ids
    ids_b1_reivindicados = [i for i in ids if i != id_b2]
    assert sorted(ids_b1_reivindicados) == sorted(ids_b1[:3])  # as 3 mais antigas do b1

    _retrazar_locacoes(pg_session)  # stale lease: devolve tudo à fila
    # Sem limite por barco: todas as 13
    todas = db.claim_outbox(limit=20, per_boat_limit=None)
    assert len(todas) == 13

    _retrazar_locacoes(pg_session)  # stale lease de novo: fila restaurada
    # Retrocompatibilidade: limit posicional continua funcionando
    duas = db.claim_outbox(2)
    assert len(duas) == 2
    assert duas[0]["id"] == ids_b1[0]  # ordem global por created_at ASC


def test_own_downlink_via_virtual_gateway(db, pg_session):
    """Achado 6: texto cujo remetente é gateway virtual próprio não vira chat."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                INSERT INTO virtual_gateways (boat_id, gateway_id, virtual_node_num)
                VALUES ('barco-01', '!feed000', 4242)
                """
            )

    env = _env_texto(4242, 321, "eco do escritório")
    counts = db.store_native([env])
    assert counts["own_downlink"] == 1
    assert counts["chat"] == 0
    assert counts["raw"] == 1

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM chat_messages")
            assert cur.fetchone()[0] == 0


def test_chat_boat_id_from_boat_devices(db, pg_session):
    """Achado 7: boat_id vem do vínculo VIGENTE em boat_devices, não do envelope."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                INSERT INTO boat_devices (node_num, boat_id, valid_from, valid_to)
                VALUES
                    (5300, 'barco-01', now() - interval '1 day', NULL),
                    (5301, 'barco-02', now() - interval '2 days', now() - interval '1 day')
                """
            )

    counts = db.store_native(
        [
            _env_texto(5300, 331, "vínculo vigente"),
            _env_texto(5301, 332, "vínculo vencido"),
        ]
    )
    assert counts["chat"] == 2

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "SELECT from_num, boat_id FROM chat_messages ORDER BY from_num ASC"
            )
            rows = cur.fetchall()
            assert rows == [(5300, "barco-01"), (5301, None)]


def test_packet_seen_skips_non_domain_kinds(db, pg_session):
    """Achado 8 (parte): undecryptable/malformed/opaque não reclamam (from, id)."""
    venenosos = [
        DecodedEnvelope(
            kind=k,
            from_num=2600,
            packet_id=341,
            gateway_num=1001,
            reason="teste",
        )
        for k in ("malformed", "undecryptable", "opaque")
    ]
    # Duas entregas de cada (redelivery do broker): nunca marca duplicata
    counts = db.store_native(venenosos + venenosos)
    assert counts["raw"] == 6
    assert counts["duplicates"] == 0

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM packet_seen")
            assert cur.fetchone()[0] == 0


def test_packet_seen_expired_reclaimed_and_prune(db, pg_session):
    """Achado 8 (parte): janela de 7 dias re-clama (from, id) expirado; prune recolhe lixo."""
    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            # (from, id) visto há 8 dias: fora da janela de dedupe
            cur.execute(
                """
                INSERT INTO packet_seen (from_num, packet_id, first_gateway, first_seen)
                VALUES
                    (2000, 7777, 1001, now() - interval '8 days'),
                    (2100, 8888, 1001, now() - interval '8 days')
                """
            )

    env = _env_posicao(2000, 7777, 1728100000)
    counts = db.store_native([env])
    assert counts["duplicates"] == 0  # expirado foi re-claimado
    assert counts["positions"] == 1

    # prune_packet_seen apaga SÓ linhas fora da janela; a recém-atualizada fica
    apagadas = db.prune_packet_seen()
    assert apagadas >= 1

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT count(*) FROM packet_seen WHERE from_num = 2000 AND packet_id = 7777")
            assert cur.fetchone()[0] == 1  # re-claimado → linha fresca sobrevive

            cur.execute("SELECT count(*) FROM packet_seen")
            assert cur.fetchone()[0] == 1  # só o lixo antigo (2100/8888) foi embora

            # Nada mais fora da janela: segunda passada não apaga nada
            assert db.prune_packet_seen() == 0


def test_arrival_time_now_not_gateway_rx_time(db, pg_session):
    """Achado 9: received_at é CHEGADA (now()); rx_time vira observed_at."""
    env = _env_posicao(2400, 351, 1728100000, rx_time=100)  # rx_time = 1970
    counts = db.store_native([env])
    assert counts["positions"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM received_at)::double precision,
                       EXTRACT(EPOCH FROM observed_at)::bigint,
                       EXTRACT(EPOCH FROM pos_time)::bigint
                FROM positions WHERE node_num = 2400
                """
            )
            row = cur.fetchone()
            assert row is not None
            recebido, observado, posicao = row
            assert abs(recebido - time.time()) < 300  # chegada ≈ agora
            assert observado == 100  # rx_time do gateway em observed_at
            assert posicao == 1728100000  # relógio do fix em pos_time

            cur.execute(
                "SELECT EXTRACT(EPOCH FROM received_at)::double precision FROM raw_envelopes WHERE from_num = 2400"
            )
            assert abs(cur.fetchone()[0] - time.time()) < 300


def test_chat_arrival_time_now_not_gateway_rx_time(db, pg_session):
    """Achado 9 (parte): chat_messages.received_at = now(); rx_time vira observed_at."""
    env = _env_texto(2500, 352, "chegou agora", rx_time=100)
    counts = db.store_native([env])
    assert counts["chat"] == 1

    conn_str = f"host={pg_session['host']} port={pg_session['port']} user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM received_at)::double precision,
                       EXTRACT(EPOCH FROM observed_at)::bigint
                FROM chat_messages WHERE from_num = 2500
                """
            )
            row = cur.fetchone()
            assert row is not None
            recebido, observado = row
            assert abs(recebido - time.time()) < 300
            assert observado == 100


# --------------------------------------------------------------------------
# Privilégios reais do papel <db>_ingest / <db>_viewer (achado 4)
# --------------------------------------------------------------------------

def test_ingest_role_store_native_node_activity(db, db_ingest):
    """Achado 4: tudo que store_native/node_activity/check_ready usam é concedido."""
    db_ingest.check_ready()  # sondagens de boot sob o papel de produção

    counts = db_ingest.store_native(
        [
            _env_posicao(6200, 611, 1728100000),
            DecodedEnvelope(
                kind="telemetry",
                from_num=6200,
                packet_id=612,
                rx_time=1728100000,
                telemetry=TelemetryFix(
                    time=1728100000,
                    time_source="device",
                    time_flag=None,
                    battery_level=76.0,
                    voltage=4.0,
                ),
            ),
            DecodedEnvelope(
                kind="nodeinfo",
                from_num=6200,
                packet_id=613,
                nodeinfo=NodeInfo(
                    user_id="!00001838",
                    long_name="Barco Papel",
                    short_name="BP",
                    hw_model="TBEAM",
                ),
            ),
            _env_texto(6200, 614, "dentro do privilégio"),
        ]
    )
    assert counts["positions"] == 1
    assert counts["telemetry"] == 1
    assert counts["nodeinfo"] == 1
    assert counts["chat"] == 1
    assert counts["raw"] == 4

    atividade = db_ingest.node_activity()
    info = atividade[6200]
    # o ingest NÃO lê coordenadas: nem direto, nem via node_activity
    assert "lat_i" not in info and "lon_i" not in info
    assert info["displacement_m"] is None  # só 1 fix
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db_ingest._pool.connection() as conn:
            conn.execute("SELECT lat_i FROM positions")
    assert info["fix_time"] is not None
    assert info["first_seen"] is not None
    assert info["last_seen"] is not None


def test_viewer_role_reads_and_inserts_outbox(db, pg_session):
    """Achado 4: viewer lê chat/outbox, insere no outbox (IDENTITY) e não lê raw_envelopes."""
    # Seed de dados visíveis ao viewer (como rastro_ingest, o papel de produção)
    cfg_ing = PgConfig(
        host=pg_session["host"],
        port=pg_session["port"],
        dbname=pg_session["dbname"],
        user=f"{pg_session['dbname']}_ingest",
        password=SENHA_INGEST,
    )
    db_ing_seed = Db(cfg_ing)
    try:
        db_ing_seed.store_native(
            [
                _env_posicao(6300, 621, 1728100000),
                _env_texto(6300, 622, "visível ao viewer"),
            ]
        )
    finally:
        db_ing_seed.close()

    with psycopg.connect(
        _conn_de(pg_session, f"{pg_session['dbname']}_viewer", SENHA_VIEWER),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chat_messages")
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT count(*) FROM chat_outbox")
            assert cur.fetchone()[0] == 0

            # INSERT limitado no outbox: IDENTITY exige USAGE na sequência
            cur.execute(
                """
                INSERT INTO chat_outbox (boat_id, text, created_by, expires_at)
                VALUES ('b9', 'do viewer', 'qa', now() + interval '10 minutes')
                RETURNING id
                """
            )
            assert cur.fetchone()[0] > 0

            cur.execute("SELECT text FROM chat_outbox WHERE boat_id = 'b9'")
            assert cur.fetchone() == ("do viewer",)

            # raw_envelopes contém payloads brutos: viewer NÃO lê
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT count(*) FROM raw_envelopes")
                cur.fetchone()


def test_ingest_role_cannot_write_reference_tables(db, pg_session):
    """Achado 4: ingest não grava mais virtual_gateways/boat_devices (só lê)."""
    with psycopg.connect(
        _conn_de(pg_session, f"{pg_session['dbname']}_ingest", SENHA_INGEST),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO virtual_gateways (boat_id, gateway_id, virtual_node_num) "
                    "VALUES ('x', '!x000', 1)"
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO boat_devices (node_num, boat_id, valid_from) VALUES (1, 'x', now())"
                )


def _parse_url(url: str) -> dict:
    u = urlsplit(url)
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 5432,
        "user": unquote(u.username or "postgres"),
        "password": unquote(u.password or ""),
        "dbname": unquote(u.path.lstrip("/") or "postgres"),
    }


# --------------------------------------------------------------------------
# Regressões FIX-4/FIX-5
# --------------------------------------------------------------------------


def _args_format(texto: str, inicio: int) -> str:
    """Argumentos de uma chamada ``format(...)`` que começa em ``inicio``."""
    i = texto.index("(", inicio)
    prof = 0
    em_lit = False
    j = i
    while j < len(texto):
        c = texto[j]
        if em_lit:
            if c == "'":
                if j + 1 < len(texto) and texto[j + 1] == "'":
                    j += 1
                else:
                    em_lit = False
        elif c == "'":
            em_lit = True
        elif c == "(":
            prof += 1
        elif c == ")":
            prof -= 1
            if prof == 0:
                return texto[i + 1 : j]
        j += 1
    raise ValueError("format( sem fecho")


def _grants_canonicos(caminho: Path) -> set[tuple[str, str]]:
    """GRANTs de tabela/sequência normalizados de um arquivo de deploy (FIX-4).

    Cada ``format('GRANT ...')`` vira (corpo normalizado, papel). Normalização:
    remove ``%I.``, troca ``ON SEQUENCE`` por ``ON`` e resolve o papel a partir
    do último argumento (``ingest_role`` / ``:'ingest'``). Grants de banco e de
    esquema (CONNECT, USAGE ON SCHEMA) ficam fora do escopo — o 02-native.sql
    standalone não os executa (banco/esquema já existem nesse caminho).
    """
    texto = caminho.read_text(encoding="utf-8")
    grants: set[tuple[str, str]] = set()
    for m in re.finditer(r"format\(\s*'((?:[^']|'')*)'", texto):
        lit = m.group(1)
        if (
            not lit.lstrip().startswith("GRANT")
            or " ON DATABASE" in lit
            or " ON SCHEMA " in lit
            or " ON " not in lit  # GRANT de papel (membership), não de objeto
        ):
            continue
        args = _args_format(texto, m.start())
        ultimo = re.split(r"[,\s]+", args.strip())[-1]
        papel = re.sub(r"[^a-z_]", "", ultimo.lower()).removesuffix("_role")
        corpo = lit.strip().replace("%I.", "").replace(" ON SEQUENCE ", " ON ")
        grants.add((re.sub(r"\s+", " ", corpo), papel))
    return grants


def test_grants_identicos_entre_caminhos():
    """FIX-4: os três caminhos de deploy concedem o mesmo conjunto canônico."""
    fontes = {
        "02-native.sql": _grants_canonicos(SCHEMA_02),
        "migrate-02-native.sh": _grants_canonicos(
            REPO_ROOT / "deploy" / "postgres" / "migrate-02-native.sh"
        ),
        "bootstrap-existing.sh": _grants_canonicos(
            REPO_ROOT / "deploy" / "postgres" / "bootstrap-existing.sh"
        ),
    }
    referencia = fontes["02-native.sql"]
    assert referencia, "nenhum GRANT extraído da 02-native.sql"
    for nome, obtido in fontes.items():
        faltando = referencia - obtido
        sobrando = obtido - referencia
        assert not faltando, f"{nome}: faltando {sorted(faltando)}"
        assert not sobrando, f"{nome}: sobrando {sorted(sobrando)}"


def test_telemetria_parcial_preserva_bateria_conhecida(db, pg_session):
    """FIX-2 (banco): telemetria só-tensão não apaga bateria conhecida do node_power."""
    cheia = DecodedEnvelope(
        kind="telemetry",
        from_num=7100,
        packet_id=521,
        rx_time=1728400000,
        telemetry=TelemetryFix(
            time=1728400000,
            time_source="device",
            time_flag=None,
            battery_level=76.0,
            voltage=4.10,
        ),
    )
    parcial = DecodedEnvelope(
        kind="telemetry",
        from_num=7100,
        packet_id=522,
        rx_time=1728401000,
        telemetry=TelemetryFix(
            time=1728401000,
            time_source="device",
            time_flag=None,
            battery_level=None,
            voltage=3.90,
        ),
    )
    counts = db.store_native([cheia, parcial])
    assert counts["telemetry"] == 2

    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT battery_level, voltage FROM node_power WHERE node_num = 7100")
            linha = cur.fetchone()
            assert linha[0] == pytest.approx(76.0, 0.01)
            assert linha[1] == pytest.approx(3.90, 0.01)
            cur.execute(
                "SELECT battery_level, voltage FROM device_telemetry "
                "WHERE node_num = 7100 AND telem_time = to_timestamp(1728401000)"
            )
            assert cur.fetchone() == (None, pytest.approx(3.90))


def test_chat_dedupe_janela_7_dias(db, pg_session):
    """FIX-3: (from,id) reutilizado fora da janela grava texto novo; dentro dela dedupe."""
    db.store_native([_env_texto(6100, 777, "texto antigo")])
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "UPDATE chat_messages SET received_at = now() - interval '8 days' "
                "WHERE from_num = 6100"
            )
            cur.execute("DELETE FROM packet_seen WHERE from_num = 6100")
    counts = db.store_native([_env_texto(6100, 777, "texto novo")])
    assert counts["chat"] == 1
    assert counts["duplicates"] == 0
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT text FROM chat_messages WHERE from_num = 6100")
            assert cur.fetchone() == ("texto novo",)
    counts2 = db.store_native([_env_texto(6100, 777, "texto novo")])
    assert counts2["duplicates"] == 1
    assert counts2["chat"] == 0


def _retrazar_locacoes(pg_session) -> None:
    """FIX-5: atrasa lease_until das linhas 'sending' — stale lease reivindicável."""
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "UPDATE chat_outbox SET lease_until = now() - interval '1 second' "
                "WHERE status = 'sending'"
            )


def test_claim_outbox_lease_duravel(db, pg_session):
    """FIX-5: claim loca as linhas ('sending' + lease_until) e não as revende."""
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO chat_outbox (boat_id, text, created_by, expires_at) "
                "VALUES ('b1', 'a', 'admin', now() + interval '30 minutes'), "
                "('b2', 'b', 'admin', now() + interval '30 minutes')"
            )
    primeira = db.claim_outbox(limit=10)
    assert len(primeira) == 2
    for linha in primeira:
        assert linha["status"] == "sending"
        assert linha["lease_until"] is not None
    vazio = db.claim_outbox(limit=10)
    assert vazio == []
    _retrazar_locacoes(pg_session)
    renovada = db.claim_outbox(limit=10)
    assert {m["id"] for m in renovada} == {m["id"] for m in primeira}


def test_mark_outbox_transicoes_da_locacao(db, pg_session):
    """FIX-5: mark transiciona a partir da locação; terminais limpam lease_until."""
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO chat_outbox (boat_id, text, created_by, expires_at) "
                "VALUES ('b1', 'a', 'admin', now() + interval '30 minutes'), "
                "('b1', 'b', 'admin', now() + interval '30 minutes'), "
                "('b1', 'c', 'admin', now() + interval '30 minutes')"
            )
    linhas = db.claim_outbox(limit=5)
    assert len(linhas) == 3
    id1, id2, id3 = (m["id"] for m in linhas)
    assert db.mark_outbox(id1, "queued", packet_id=4242) is True
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT status, packet_id, lease_until FROM chat_outbox WHERE id = %s", (id1,))
            st, pid, lease = cur.fetchone()
            assert st == "sending"
            assert pid == 4242
            assert lease is not None
    assert db.mark_outbox(id1, "sent") is True
    assert db.mark_outbox(id1, "sent") is False
    assert db.mark_outbox(id2, "failed", error="mqtt down") is True
    assert db.mark_outbox(id3, "expired") is True
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT id, status, lease_until FROM chat_outbox ORDER BY id")
            estados = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert estados[id1] == ("sent", None)
    assert estados[id2] == ("failed", None)
    assert estados[id3] == ("expired", None)


def test_claim_outbox_concorrente(db, pg_session):
    """FIX-5: claims concorrentes são disjuntos; união cobre todas as linhas."""
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}",
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO chat_outbox (boat_id, text, created_by, expires_at) "
                "SELECT 'b1', 'm' || g, 'admin', now() + interval '30 minutes' "
                "FROM generate_series(1, 6) AS g"
            )
    ids_todas = set()
    with psycopg.connect(
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} dbname={pg_session['dbname']}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM chat_outbox")
            ids_todas = {r[0] for r in cur.fetchall()}
    assert len(ids_todas) == 6
    db2 = Db(
        PgConfig(
            host=pg_session["host"],
            port=pg_session["port"],
            dbname=pg_session["dbname"],
            user=pg_session["user"],
            password=pg_session["password"],
        )
    )
    resultados: list[list[dict]] = [[], []]
    barreira = threading.Barrier(2)

    def _trabalhador(indice: int, instancia: Db) -> None:
        barreira.wait()
        resultados[indice] = instancia.claim_outbox(limit=10, per_boat_limit=None)

    fios = [
        threading.Thread(target=_trabalhador, args=(0, db)),
        threading.Thread(target=_trabalhador, args=(1, db2)),
    ]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    db2.close()
    ids_a = {m["id"] for m in resultados[0]}
    ids_b = {m["id"] for m in resultados[1]}
    assert not (ids_a & ids_b), "mesma linha reivindicada por dois workers"
    assert ids_a | ids_b == ids_todas


def test_node_activity_deslocamento_sem_expor_coordenadas(db, db_ingest):
    """node_activity_summary: deslocamento haversine entre os 2 últimos fixes (~111 m por 0,001° de lat)."""
    import time as _t

    agora = int(_t.time())
    db.store_native(
        [
            _env_posicao(6300, 701, agora - 7200, lat_i=-40000000, lon_i=-70000000),
            _env_posicao(6300, 702, agora - 60, lat_i=-39990000, lon_i=-70000000),
        ]
    )
    info = db_ingest.node_activity()[6300]
    assert info["prev_time"] is not None and info["fix_time"] > info["prev_time"]
    assert 105 < info["displacement_m"] < 117
    assert "lat_i" not in info
