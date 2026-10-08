"""GET /api/alerts: array de alertas ativos com nome do nó (plano §2.3)."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

TOKEN = "teste-token-123"  # forçado no conftest


def _seed_alertas(dados):
    """1 alerta de bateria (nó A), 1 de gateway (fora de nodes), 1 limpo (ignorado)."""
    import psycopg, os
    conn = psycopg.connect(
        host=os.environ["RASTRO_PG_HOST"],
        port=os.environ["RASTRO_PG_PORT"],
        user=os.environ["RASTRO_PG_USER"],
        password=os.environ["RASTRO_PG_PASSWORD"],
        dbname="rastro_api_test",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET search_path = rastro, public")
        cur.execute(
            "INSERT INTO node_power (node_num, battery_level, voltage) VALUES (%s, %s, %s)",
            (dados["num_a"], 15.0, 3.40),
        )
        cur.execute(
            "INSERT INTO alert_state (key, node_num, kind, since, cleared_at)"
            " VALUES (%s, %s, %s, now() - interval '30 minutes', NULL)",
            (f"bateria_critica:{dados['num_a']}", dados["num_a"], "bateria_critica"),
        )
        cur.execute(
            "INSERT INTO alert_state (key, node_num, kind, since, cleared_at)"
            " VALUES (%s, %s, %s, now() - interval '90 minutes', NULL)",
            ("gateway_mudo:4294967295", 4294967295, "gateway_mudo"),
        )
        # gateway_status.last_uplink é a fonte de details.last_seen (o ciclo
        # de gateway_mudo usa este timestamp, não nodes.last_seen).
        cur.execute(
            "INSERT INTO gateway_status (gateway_num, last_uplink)"
            " VALUES (%s, now() - interval '95 minutes')",
            (4294967295,),
        )
        cur.execute(
            "INSERT INTO alert_state (key, node_num, kind, since, cleared_at)"
            " VALUES (%s, %s, %s, now() - interval '2 hours', now() - interval '1 hour')",
            (f"bateria_critica:{dados['num_b']}", dados["num_b"], "bateria_critica"),
        )
    conn.close()


def test_alerts_ativos_array_com_nome_e_detalhes(client, dados):
    _seed_alertas(dados)
    resp = client.get("/api/alerts", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    por_tipo = {a["alert_type"]: a for a in body}
    assert "bateria_critica" in por_tipo and "gateway_mudo" in por_tipo
    bateria = por_tipo["bateria_critica"]
    assert bateria["node_num"] == dados["num_a"]
    assert bateria["node_id"] == "!aaaa0001"
    assert bateria["node_name"] == "Monitor A"
    assert bateria["severity"] == "high"
    assert bateria["details"]["battery_level"] == 15.0
    assert bateria["details"]["voltage"] == 3.40
    assert bateria["alert_id"] == f"bateria_critica:{dados['num_a']}"
    assert bateria["triggered_at"]
    gateway = por_tipo["gateway_mudo"]
    assert gateway["node_id"] == "!ffffffff"
    assert gateway["details"]["last_seen"]  # vem de gateway_status.last_uplink
    assert gateway["node_name"] == "!ffffffff"  # gateway fora de nodes
    assert gateway["severity"] == "critical"
    # alerta limpo (cleared_at NOT NULL) NUNCA aparece
    assert f"bateria_critica:{dados['num_b']}" not in [a["alert_id"] for a in body]


def test_alerts_exige_credencial(client):
    resp = client.get("/api/alerts")
    assert resp.status_code == 401
