"""Testes de GET /api/nodes/{node}/events (linha do tempo paginada por cursor).

Usam conexão falsa (sem Postgres): o handler responde por fonte (positions /
device_telemetry / chat_messages) e aplica o limite pedido.
"""
from __future__ import annotations

import datetime as dt

from tests.test_api_nodes_modified import AUTH, _FakeConn, _make_client

T0 = dt.datetime(2026, 10, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def _at(minutes: int) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def _handler(query: str, params):
    if "SELECT node_num FROM nodes" in query:
        return [{"node_num": 7}]
    limit = params[-1]
    if "FROM positions" in query:
        rows = [
            {"id": 2, "ts": _at(10), "lat": -3.1, "lon": -60.0, "altitude_m": 40,
             "sats_in_view": 9, "snr": 6.5, "rssi": -91, "hop_limit": 3,
             "packet_id": 11, "gateway_num": 99, "time_source": "device",
             "time_flag": None, "received_at": _at(10), "gateway_name": "Base Ituí"},
            {"id": 1, "ts": _at(5), "lat": -3.0, "lon": -60.1, "altitude_m": None,
             "sats_in_view": None, "snr": None, "rssi": None, "hop_limit": None,
             "packet_id": None, "gateway_num": None, "time_source": "gateway",
             "time_flag": "future", "received_at": _at(5), "gateway_name": None},
        ]
    elif "FROM device_telemetry" in query:
        rows = [{"id": 5, "ts": _at(8), "battery_level": 87.0, "voltage": 4.1,
                 "channel_util": 2.0, "air_util_tx": 0.1, "uptime_s": 3600,
                 "time_source": "device", "received_at": _at(8)}]
    else:
        rows = [{"id": 3, "ts": _at(9), "direction": "in", "text": "oi", "is_alert": True,
                 "packet_id": 1, "observed_at": _at(9), "received_at": _at(9)}]
    return rows[:limit]


def _client(monkeypatch):
    conn = _FakeConn(_handler)
    return _make_client(conn, monkeypatch), conn


def test_events_merge_ordem_desc_e_campos(monkeypatch):
    client, _ = _client(monkeypatch)
    body = client.get("/api/nodes/!00000007/events", headers=AUTH).json()
    assert [(e["kind"], e["id"]) for e in body["events"]] == [
        ("pos", 2), ("msg", 3), ("telem", 5), ("pos", 1),
    ]
    assert body["next_cursor"] is None
    pos = body["events"][0]
    assert pos["gateway_num"] == 99 and pos["gateway_name"] == "Base Ituí"
    assert pos["snr"] == 6.5 and pos["ts"].endswith("Z")
    assert body["events"][1]["is_alert"] is True


def test_events_paginacao_cursor(monkeypatch):
    client, _ = _client(monkeypatch)
    body = client.get("/api/nodes/!00000007/events?limit=2", headers=AUTH).json()
    assert [e["id"] for e in body["events"]] == [2, 3]
    assert body["next_cursor"] == f"{body['events'][1]['ts']}|msg|3"


def test_events_kinds_filtra_fontes(monkeypatch):
    client, conn = _client(monkeypatch)
    body = client.get("/api/nodes/!00000007/events?kinds=telem", headers=AUTH).json()
    assert [e["kind"] for e in body["events"]] == ["telem"]
    assert not any("FROM positions" in q for q, _ in conn.queries)


def test_events_cursor_aplicado_na_sql(monkeypatch):
    client, conn = _client(monkeypatch)
    cur = "2026-10-01T12:09:00Z|msg|3"
    r = client.get(f"/api/nodes/!00000007/events?before={cur}&kinds=pos,msg", headers=AUTH)
    assert r.status_code == 200
    sqls = {("pos" if "FROM positions" in q else "msg"): q
            for q, _ in conn.queries if "SELECT node_num" not in q}
    assert sqls["pos"].count("p.pos_time <= %s") == 1  # só a janela; cursor é "<" estrito
    assert "p.pos_time < %s" in sqls["pos"]
    assert "m.id < %s" in sqls["msg"]


def test_events_parametros_invalidos(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/api/nodes/!00000007/events?before=lixo", headers=AUTH).status_code == 400
    assert client.get("/api/nodes/!00000007/events?kinds=x", headers=AUTH).status_code == 400


def test_events_limit_maximo_200(monkeypatch):
    client, conn = _client(monkeypatch)
    client.get("/api/nodes/!00000007/events?limit=99999", headers=AUTH)
    assert all(p[-1] == 201 for q, p in conn.queries if "SELECT node_num" not in q)


def test_events_sem_token_401(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/api/nodes/!00000007/events").status_code == 401
