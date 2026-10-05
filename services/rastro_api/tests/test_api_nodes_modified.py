"""Testes dos endpoints modificados: /api/nodes/latest e /api/nodes/{n}/track (WP-F1).

Cobre:
- /api/nodes/latest:
  - ganho de age_s (segundos decorridos desde pos_time calculados no servidor)
  - ganho de time_flag (repassado de positions.time_flag via LATERAL join)
- /api/nodes/{n}/track:
  - divisão da trilha em múltiplos LineStrings em lacunas > RASTRO_TRACK_GAP_SECS (default 1800 s)
  - preservação de segmento único quando fixes ocorrem dentro do limiar de lacuna
  - suporte a limiar customizado via RASTRO_TRACK_GAP_SECS
  - suporte a formato MultiLineString quando solicitado
  - preservação dos Point features para todos os fixes
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rastro_api.api import geojson
from rastro_api.api.main import create_app

TOKEN = "teste-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _FakeCursor:
    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows or []

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, handler=None):
        self.queries: list[tuple[str, Any]] = []
        self.handler = handler

    def execute(self, query: str, params: Any = None):
        self.queries.append((query, params))
        if self.handler:
            rows = self.handler(query, params)
            return _FakeCursor(rows)
        return _FakeCursor([])


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        return _Ctx()


def _make_client(conn: _FakeConn, monkeypatch) -> TestClient:
    monkeypatch.setenv("RASTRO_API_AUTH", "token")
    monkeypatch.setenv("RASTRO_API_TOKEN", TOKEN)
    app = create_app()
    app.state.pool = _FakePool(conn)
    return TestClient(app)


def test_latest_features_gain_age_s_and_time_flag(monkeypatch):
    agora = dt.datetime.now(dt.timezone.utc)
    t_120s = agora - dt.timedelta(seconds=120)
    t_3600s = agora - dt.timedelta(seconds=3600)

    mock_rows = [
        {
            "node_num": 101,
            "node_id": "!aaaa0001",
            "nome": "Monitor Alpha",
            "pos_time": t_120s,
            "time_source": "device",
            "lat": -3.1,
            "lon": -60.0,
            "altitude_m": 45,
            "sats_in_view": 8,
            "battery": 92.0,
            "received_at": agora,
            "time_flag": None,
        },
        {
            "node_num": 102,
            "node_id": "!bbbb0002",
            "nome": "Monitor Beta",
            "pos_time": t_3600s,
            "time_source": "gateway",
            "lat": -3.2,
            "lon": -60.1,
            "altitude_m": 50,
            "sats_in_view": 5,
            "battery": 75.0,
            "received_at": agora,
            "time_flag": "invalid_zero",
        },
    ]

    def handler(query: str, params: Any):
        assert "vw_ultima_posicao" in query
        assert "time_flag" in query
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/nodes/latest", headers=AUTH)
    assert res.status_code == 200
    fc = res.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2

    f1 = fc["features"][0]
    p1 = f1["properties"]
    assert p1["node_id"] == "!aaaa0001"
    assert "age_s" in p1
    assert 118 <= p1["age_s"] <= 125
    assert p1["time_flag"] is None

    f2 = fc["features"][1]
    p2 = f2["properties"]
    assert p2["node_id"] == "!bbbb0002"
    assert "age_s" in p2
    assert 3595 <= p2["age_s"] <= 3610
    assert p2["time_flag"] == "invalid_zero"


def test_track_gap_splitting_multiple_linestrings(monkeypatch):
    t0 = dt.datetime(2026, 10, 5, 8, 0, 0, tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=10)   # +600 s (sem lacuna <= 1800 s)
    t2 = t1 + dt.timedelta(minutes=40)   # +2400 s (> 1800 s: quebra de trilha!)
    t3 = t2 + dt.timedelta(minutes=15)   # +900 s (sem lacuna <= 1800 s)

    mock_rows = [
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t0, "lat": -3.0, "lon": -60.0, "altitude_m": 10, "sats_in_view": 7, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t1, "lat": -3.1, "lon": -60.1, "altitude_m": 12, "sats_in_view": 8, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t2, "lat": -3.5, "lon": -60.5, "altitude_m": 15, "sats_in_view": 6, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t3, "lat": -3.6, "lon": -60.6, "altitude_m": 14, "sats_in_view": 7, "time_source": "device"},
    ]

    def handler(query: str, params: Any):
        if "FROM nodes WHERE node_num = %s" in query:
            return [{"node_num": 101}]
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/nodes/!aaaa0001/track", headers=AUTH)
    assert res.status_code == 200
    feats = res.json()["features"]

    # Identifica linhas e pontos
    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    points = [f for f in feats if f["geometry"]["type"] == "Point"]

    # 4 fixes com uma lacuna no meio geram 2 LineStrings separadas
    assert len(lines) == 2
    assert len(points) == 4

    # Primeiro segmento (t0, t1)
    seg1 = lines[0]["geometry"]["coordinates"]
    assert len(seg1) == 2
    assert seg1[0] == [-60.0, -3.0]
    assert seg1[1] == [-60.1, -3.1]
    assert lines[0]["properties"]["segment"] == 0

    # Segundo segmento (t2, t3)
    seg2 = lines[1]["geometry"]["coordinates"]
    assert len(seg2) == 2
    assert seg2[0] == [-60.5, -3.5]
    assert seg2[1] == [-60.6, -3.6]
    assert lines[1]["properties"]["segment"] == 1


def test_track_without_gaps_single_linestring(monkeypatch):
    t0 = dt.datetime(2026, 10, 5, 8, 0, 0, tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=5)
    t2 = t1 + dt.timedelta(minutes=10)

    mock_rows = [
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t0, "lat": -3.0, "lon": -60.0, "altitude_m": 10, "sats_in_view": 7, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t1, "lat": -3.1, "lon": -60.1, "altitude_m": 12, "sats_in_view": 8, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t2, "lat": -3.2, "lon": -60.2, "altitude_m": 14, "sats_in_view": 7, "time_source": "device"},
    ]

    def handler(query: str, params: Any):
        if "FROM nodes WHERE node_num = %s" in query:
            return [{"node_num": 101}]
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/nodes/!aaaa0001/track", headers=AUTH)
    assert res.status_code == 200
    feats = res.json()["features"]

    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    assert len(lines) == 1
    assert len(lines[0]["geometry"]["coordinates"]) == 3


def test_track_gap_custom_env_threshold(monkeypatch):
    # Com RASTRO_TRACK_GAP_SECS=300 (5 min), um intervalo de 10 min vira lacuna
    monkeypatch.setenv("RASTRO_TRACK_GAP_SECS", "300")
    t0 = dt.datetime(2026, 10, 5, 8, 0, 0, tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=10)  # 600 s > 300 s -> quebra

    mock_rows = [
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t0, "lat": -3.0, "lon": -60.0, "altitude_m": 10, "sats_in_view": 7, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t1, "lat": -3.1, "lon": -60.1, "altitude_m": 12, "sats_in_view": 8, "time_source": "device"},
    ]

    def handler(query: str, params: Any):
        if "FROM nodes WHERE node_num = %s" in query:
            return [{"node_num": 101}]
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/nodes/!aaaa0001/track", headers=AUTH)
    assert res.status_code == 200
    feats = res.json()["features"]

    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    assert len(lines) == 2


def test_track_multilinestring_format_option(monkeypatch):
    t0 = dt.datetime(2026, 10, 5, 8, 0, 0, tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=10)
    t2 = t1 + dt.timedelta(minutes=45)

    mock_rows = [
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t0, "lat": -3.0, "lon": -60.0, "altitude_m": 10, "sats_in_view": 7, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t1, "lat": -3.1, "lon": -60.1, "altitude_m": 12, "sats_in_view": 8, "time_source": "device"},
        {"node_num": 101, "node_id": "!aaaa0001", "nome": "Monitor Alpha", "pos_time": t2, "lat": -3.5, "lon": -60.5, "altitude_m": 15, "sats_in_view": 6, "time_source": "device"},
    ]

    def handler(query: str, params: Any):
        if "FROM nodes WHERE node_num = %s" in query:
            return [{"node_num": 101}]
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/nodes/!aaaa0001/track?format=multilinestring", headers=AUTH)
    assert res.status_code == 200
    feats = res.json()["features"]

    multi_lines = [f for f in feats if f["geometry"]["type"] == "MultiLineString"]
    assert len(multi_lines) == 1
    coords = multi_lines[0]["geometry"]["coordinates"]
    assert len(coords) == 2  # 2 segmentos no MultiLineString
