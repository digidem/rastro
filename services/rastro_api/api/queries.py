"""Consultas SQL puras da API — todas somente-leitura e parametrizadas.

As funções recebem uma conexão do pool (``row_factory=dict_row``) e retornam
linhas como dicts; nenhuma escrita em nenhuma tabela.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

_SQL_LATEST = """
    SELECT node_num, node_id, nome, pos_time, time_source, lat, lon,
           altitude_m, sats_in_view, battery, received_at
    FROM vw_ultima_posicao
    ORDER BY node_num
"""

_SQL_TRACK = """
    SELECT * FROM (
        SELECT p.pos_time, p.time_source, p.lat, p.lon, p.altitude_m, p.sats_in_view,
               n.node_id, COALESCE(n.friendly_name, n.node_id) AS nome
        FROM positions p
        JOIN nodes n ON n.node_num = p.node_num
        WHERE p.node_num = %s
          AND p.pos_time >= %s
          AND p.pos_time <= %s
        ORDER BY p.pos_time DESC
        LIMIT %s
    ) sub ORDER BY pos_time ASC
"""

_SQL_TELEMETRY = """
    SELECT * FROM (
        SELECT battery_level, voltage, channel_util, air_util_tx, uptime_s, telem_time
        FROM device_telemetry
        WHERE node_num = %s
          AND telem_time >= %s
          AND telem_time <= %s
        ORDER BY telem_time DESC
        LIMIT %s
    ) sub ORDER BY telem_time ASC
"""


def _parse_node(node: str) -> int | None:
    """'!0c0ffee0'/'0c0ffee0' → node_num (hex); dígitos decimais → direto."""
    text = (node or "").strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    hex_part = text[1:] if text.startswith("!") else text
    try:
        return int(hex_part, 16)
    except ValueError:
        return None


def resolve_node(conn: Any, node: str) -> int | None:
    """Converte o identificador da rota em node_num conhecido; None se desconhecido."""
    node_num = _parse_node(node)
    if node_num is None:
        return None
    row = conn.execute(
        "SELECT node_num FROM nodes WHERE node_num = %s", (node_num,)
    ).fetchone()
    return row["node_num"] if row else None


def latest_nodes(conn: Any) -> list[dict]:
    """Última posição de cada nó (view vw_ultima_posicao) + bateria mais recente."""
    return conn.execute(_SQL_LATEST).fetchall()


def track(
    conn: Any, node: str, ts_from: dt.datetime, ts_to: dt.datetime, limit: int,
) -> list[dict]:
    """Fixes do nó na janela, em ordem cronológica ASC; [] se nó desconhecido."""
    node_num = resolve_node(conn, node)
    if node_num is None:
        return []
    return conn.execute(
        _SQL_TRACK, (node_num, ts_from, ts_to, limit)
    ).fetchall()


def telemetry(
    conn: Any, node: str, ts_from: dt.datetime, ts_to: dt.datetime, limit: int,
) -> list[dict]:
    """Telemetria do nó na janela, ASC; [] se nó desconhecido."""
    node_num = resolve_node(conn, node)
    if node_num is None:
        return []
    return conn.execute(
        _SQL_TELEMETRY, (node_num, ts_from, ts_to, limit)
    ).fetchall()
