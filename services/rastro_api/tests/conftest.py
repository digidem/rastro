"""Fixtures da API: banco de teste dedicado ``rastro_api_test``.

NUNCA usa o banco ``rastro`` de produção: o nome do banco é FORÇADO aqui.
Cria o schema (01-schema.sql) + dados fixos, dropa no fim da sessão. Sem
RASTRO_PG_PASSWORD no ambiente, os testes pulam com o motivo no relatório.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from fastapi.testclient import TestClient  # noqa: E402
from rastro_api.api.main import create_app  # noqa: E402

TEST_DB = "rastro_api_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "deploy" / "postgres" / "init" / "01-schema.sql"
NATIVE_PATH = REPO_ROOT / "deploy" / "postgres" / "init" / "02-native.sql"  # colunas/tabelas do ingest nativo

# Banco de teste FORÇADO — os testes jamais abrem o banco de produção.
os.environ["RASTRO_PG_DB"] = TEST_DB
# Token também FORÇADO: o ambiente pode carregar o token real do deploy/.env.
os.environ["RASTRO_API_TOKEN"] = "teste-token-123"
os.environ.setdefault("RASTRO_PG_HOST", "127.0.0.1")
os.environ.setdefault("RASTRO_PG_PORT", "5432")
os.environ.setdefault("RASTRO_PG_USER", "rastro")

_NUM_A = int("aaaa0001", 16)
_NUM_B = int("bbbb0002", 16)
_NUM_S1 = int("5e550001", 16)  # sensor sintético, sem posição
_NUM_S2 = int("5e550002", 16)  # sensor parado há mais de 14 dias
_NUM_S3 = int("5e550003", 16)  # sensor com relógio adiantado


def _pg_connect(dbname: str):
    return psycopg.connect(
        host=os.environ["RASTRO_PG_HOST"],
        port=os.environ["RASTRO_PG_PORT"],
        user=os.environ["RASTRO_PG_USER"],
        password=os.environ["RASTRO_PG_PASSWORD"],
        dbname=dbname,
        connect_timeout=10,
    )


@pytest.fixture(scope="session")
def banco_teste():
    """Cria o banco dedicado com o schema e dropa no fim (banco próprio)."""
    if not os.environ.get("RASTRO_PG_PASSWORD"):
        pytest.skip(
            "RASTRO_PG_PASSWORD ausente no ambiente — pulando testes que exigem Postgres"
        )
    if not SCHEMA_PATH.exists():
        pytest.fail(f"schema não encontrado: {SCHEMA_PATH}")
    with _pg_connect("postgres") as admin:
        admin.autocommit = True  # CREATE/DROP DATABASE não rodam dentro de transação
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
        try:
            with _pg_connect(TEST_DB) as conn:
                conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
                conn.execute(NATIVE_PATH.read_text(encoding="utf-8"))
                conn.commit()
            yield TEST_DB
        finally:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture(scope="session")
def dados(banco_teste):
    """2 nós: !aaaa0001 com 3 posições + 1 telemetria; !bbbb0002 com 1 posição."""
    agora = datetime.now(timezone.utc)
    t1 = agora - timedelta(hours=2)
    t2 = agora - timedelta(hours=1)
    t3 = agora - timedelta(minutes=30)
    with _pg_connect(banco_teste) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO nodes (node_num, node_id, friendly_name)"
            " VALUES (%s, %s, %s), (%s, %s, %s)",
            (_NUM_A, "!aaaa0001", "Monitor A", _NUM_B, "!bbbb0002", "Monitor B"),
        )
        cur.executemany(
            "INSERT INTO positions"
            " (node_num, pos_time, time_source, lat_i, lon_i, altitude_m, sats_in_view)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (_NUM_A, t1, "device", -65_000_000, -700_000_000, 85, 7),
                (_NUM_A, t2, "device", -65_100_000, -700_100_000, 88, 8),
                (_NUM_A, t3, "device", -65_200_000, -700_200_000, 90, 6),
                (_NUM_B, agora - timedelta(minutes=45), "device", -66_000_000, -701_000_000, 100, 5),
            ],
        )
        cur.execute(
            "INSERT INTO device_telemetry"
            " (node_num, telem_time, time_source, battery_level, voltage,"
            "  channel_util, air_util_tx, uptime_s)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (_NUM_A, t2, "device", 87.5, 4.1, 2.5, 0.12, 3600),
        )
        cur.execute(
            "INSERT INTO nodes (node_num, node_id, friendly_name) VALUES"
            " (%s, '!5e550001', 'Sensor 1'), (%s, '!5e550002', NULL), (%s, '!5e550003', NULL)",
            (_NUM_S1, _NUM_S2, _NUM_S3),
        )
        cur.executemany(
            "INSERT INTO environment_telemetry"
            " (node_num, telem_time, time_source, temperature_c, relative_humidity, pressure_hpa)"
            " VALUES (%s, %s, 'device', %s, %s, %s)",
            [
                (_NUM_S1, t1, 26.0, 90.0, 1008.0),
                (_NUM_S1, t3, 27.5, 82.0, None),
                (_NUM_S2, agora - timedelta(days=20), 25.0, 70.0, 1010.0),
                (_NUM_S3, agora + timedelta(minutes=4), 24.0, 60.0, 1011.0),
            ],
        )
        conn.commit()
    return {
        "num_a": _NUM_A, "num_b": _NUM_B, "t1": t1, "t2": t2, "t3": t3,
        "num_s1": _NUM_S1, "num_s3": _NUM_S3,
    }


@pytest.fixture()
def client(dados):
    """App + TestClient com lifespan ativo (pool criado no startup)."""
    with TestClient(create_app()) as test_client:
        yield test_client
