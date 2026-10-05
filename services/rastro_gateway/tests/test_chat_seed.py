"""Seed idempotente de virtual_gateways/boat_devices via RASTRO_NATIVE_NODES (H1).

Testes de banco reutilizam os fixtures de tests/test_native_db.py (skip sem
Postgres/docker, igual aos demais); a gravação roda como o papel
``<db>_ingest`` — o mínimo privilégio real de produção.
"""
from __future__ import annotations

import psycopg
import pytest

# Fixtures compartilhadas (test_pg_url, pg_session, db, db_ingest): sessão de
# Postgres descartável, limpeza por teste, papel ingest real.
from tests.test_native_db import db, db_ingest, pg_session, test_pg_url  # noqa: F401

from rastro_gateway.chat.seed import seed_from_env

ENV_BASE = {"RASTRO_NATIVE_NODES": "!a0000001=b1,!a0000002=b2,!a0000003=b1"}


def _conexao(pg_session) -> str:
    return (
        f"host={pg_session['host']} port={pg_session['port']} "
        f"user={pg_session['user']} password={pg_session['password']} "
        f"dbname={pg_session['dbname']}"
    )


def _linhas_vgw(pg_session) -> list:
    with psycopg.connect(_conexao(pg_session)) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "SELECT boat_id, gateway_id, virtual_node_num, active "
                "FROM virtual_gateways ORDER BY boat_id"
            )
            return cur.fetchall()


def _linhas_devices(pg_session) -> list:
    with psycopg.connect(_conexao(pg_session)) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "SELECT node_num, boat_id, valid_to FROM boat_devices ORDER BY id"
            )
            return cur.fetchall()

def test_seed_sem_env_e_noop(db_ingest):
    assert seed_from_env(db_ingest._pool, {}) == (0, 0)


def test_seed_idempotente_em_duplicidade(db_ingest, pg_session):
    """Seed duas vezes: linhas estáveis, sem duplicatas, extras fora do env intactos."""
    with psycopg.connect(_conexao(pg_session), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO virtual_gateways (boat_id, gateway_id, virtual_node_num, active) "
                "VALUES ('b9', '!12345678', 305419896, true)"
            )
            cur.execute(
                "INSERT INTO boat_devices (node_num, boat_id, valid_from, valid_to) "
                "VALUES (3054198967, 'b9', now() - interval '1 day', NULL)"
            )

    primeira = seed_from_env(db_ingest._pool, ENV_BASE)
    segunda = seed_from_env(db_ingest._pool, ENV_BASE)

    assert primeira == (2, 3)
    assert segunda == (2, 3)

    vgw = _linhas_vgw(pg_session)
    assert vgw == [
        ("b1", "!e4068a7a", 3825633914, True),
        ("b2", "!fe3573f8", 4264915960, True),
        ("b9", "!12345678", 305419896, True),
    ]

    devices = _linhas_devices(pg_session)
    assert sorted(n for n, _, _ in devices) == sorted(
        [0xA0000001, 0xA0000002, 0xA0000003, 3054198967]
    )
    assert all(vt is None for _, _, vt in devices)
    assert len(devices) == 4
    assert {b for _, b, _ in devices} == {"b1", "b2", "b9"}


def test_seed_ativa_vgw_desativada_e_reabre_vinculo(db_ingest, pg_session):
    """vgw desativada reativa; vínculo fechado (valid_to setado) permanece fechado."""
    with psycopg.connect(_conexao(pg_session), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO virtual_gateways (boat_id, gateway_id, virtual_node_num, active) "
                "VALUES ('b1', '!ffffffff', 4242, false)"
            )
            cur.execute(
                "INSERT INTO boat_devices (node_num, boat_id, valid_from, valid_to) "
                "VALUES (0xA0000001, 'b1', now() - interval '2 days', now() - interval '1 day')"
            )

    assert seed_from_env(db_ingest._pool, ENV_BASE) == (2, 3)

    vgw = _linhas_vgw(pg_session)
    por_barco = {r[0]: r for r in vgw}
    assert por_barco["b1"][1:3] == ("!e4068a7a", 3825633914)
    assert por_barco["b1"][3] is True  # reativada

    todos = _linhas_devices(pg_session)
    assert any(vt is not None for _, _, vt in todos)  # fechado permanece
    abertos = [(n, b) for n, b, vt in todos if vt is None]
    assert set(abertos) == {
        (0xA0000001, "b1"),
        (0xA0000002, "b2"),
        (0xA0000003, "b1"),
    }
    # re-executar com o MESMO env não acumula linhas: mesmo conjunto de abertos
    seed_from_env(db_ingest._pool, ENV_BASE)
    assert [(n, b) for n, b, vt in _linhas_devices(pg_session) if vt is None] == abertos
