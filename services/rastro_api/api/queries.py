"""Consultas SQL da API — parametrizadas e majoritariamente somente-leitura.

As funções recebem uma conexão do pool (``row_factory=dict_row``) e retornam
linhas como dicts. A única escrita ocorre em ``insert_outbox_message``, que abre
uma transação READ WRITE explícita para inserção no ``chat_outbox``.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

_SQL_LATEST = """
    SELECT v.node_num, v.node_id, v.nome, v.pos_time, v.time_source, v.lat, v.lon,
           v.altitude_m, v.sats_in_view, v.battery, v.received_at,
           p.time_flag
    FROM vw_ultima_posicao v
    LEFT JOIN LATERAL (
        SELECT q.time_flag
        FROM positions q
        WHERE q.node_num = v.node_num AND q.pos_time = v.pos_time
        LIMIT 1
    ) p ON true
    ORDER BY v.node_num
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


_SQL_CHAT_MESSAGES_SINCE = """
    SELECT id, direction, boat_id, from_num, text, is_alert, observed_at, received_at
    FROM chat_messages
    WHERE received_at > %s
    ORDER BY received_at ASC, id ASC
"""

_SQL_CHAT_MESSAGES_NEWEST = """
    SELECT * FROM (
        SELECT id, direction, boat_id, from_num, text, is_alert, observed_at, received_at
        FROM chat_messages
        ORDER BY received_at DESC, id DESC
        LIMIT %s
    ) sub ORDER BY received_at ASC, id ASC
"""

_SQL_CHAT_MESSAGES_BEFORE = """
    SELECT * FROM (
        SELECT id, direction, boat_id, from_num, text, is_alert, observed_at, received_at
        FROM chat_messages
        WHERE id < %s
        ORDER BY received_at DESC, id DESC
        LIMIT %s
    ) sub ORDER BY received_at ASC, id ASC
"""

_SQL_CHAT_BOATS = """
    SELECT boat_id, gateway_id, virtual_node_num, active
    FROM virtual_gateways
    WHERE active IS TRUE
    ORDER BY boat_id ASC
"""

_SQL_ACTIVE_BOAT = """
    SELECT boat_id, gateway_id, virtual_node_num, active
    FROM virtual_gateways
    WHERE boat_id = %s AND active IS TRUE
    LIMIT 1
"""

_SQL_INSERT_OUTBOX = """
    INSERT INTO chat_outbox (boat_id, text, created_by, expires_at)
    VALUES (%s, %s, %s, %s)
    RETURNING id, status, expires_at
"""

_SQL_GET_OUTBOX = """
    SELECT id, boat_id, text, created_by, created_at, expires_at, status, sent_at, error
    FROM chat_outbox
    WHERE id = %s
"""


def chat_messages(
    conn: Any,
    since: dt.datetime | None = None,
    limit: int = 200,
    before_id: int | None = None,
) -> list[dict]:
    """Mensagens de chat em ordem cronológica ASC (newest-last).

    Com `since`: retorna linhas com received_at > since - 60s (sobreposição de 60 s
    para cobrir transações concorrentes cujo início foi now()).
    Sem `since`: retorna as mais recentes N mensagens (default 200, máx 500), ordenadas
    cronologicamente para exibição, com paginação retroativa via `before_id`.
    """
    if since is not None:
        overlap = since - dt.timedelta(seconds=60)
        return conn.execute(_SQL_CHAT_MESSAGES_SINCE, (overlap,)).fetchall()

    clamped_limit = min(max(1, limit), 500)
    if before_id is not None:
        return conn.execute(
            _SQL_CHAT_MESSAGES_BEFORE, (before_id, clamped_limit)
        ).fetchall()
    return conn.execute(_SQL_CHAT_MESSAGES_NEWEST, (clamped_limit,)).fetchall()


def chat_boats(conn: Any) -> list[dict]:
    """Barcos com gateways virtuais ativos."""
    return conn.execute(_SQL_CHAT_BOATS).fetchall()


def get_active_boat(conn: Any, boat_id: str) -> dict | None:
    """Busca gateway virtual ativo para o barco; None se inexistente ou inativo."""
    row = conn.execute(_SQL_ACTIVE_BOAT, (boat_id,)).fetchone()
    return dict(row) if row else None


def insert_outbox_message(
    conn: Any, boat_id: str, text: str, created_by: str, expires_at: dt.datetime
) -> dict:
    """Insere mensagem na fila chat_outbox (status 'queued'); retorna id, status, expires_at.

    Abre uma transação READ WRITE explicitamente para permitir a escrita mesmo quando
    o pool opera sob default_transaction_read_only=on.
    """
    is_ro_conn = hasattr(conn, "read_only")
    prev_ro = getattr(conn, "read_only", None)
    if is_ro_conn:
        conn.read_only = False
    try:
        if hasattr(conn, "transaction"):
            with conn.transaction():
                row = conn.execute(
                    _SQL_INSERT_OUTBOX, (boat_id, text, created_by, expires_at)
                ).fetchone()
        else:
            row = conn.execute(
                _SQL_INSERT_OUTBOX, (boat_id, text, created_by, expires_at)
            ).fetchone()
            if hasattr(conn, "commit"):
                conn.commit()
    finally:
        if is_ro_conn:
            conn.read_only = prev_ro

    return dict(row)


def get_outbox_message(conn: Any, outbox_id: int) -> dict | None:
    """Busca registro de mensagem no outbox pelo id; None se inexistente."""
    row = conn.execute(_SQL_GET_OUTBOX, (outbox_id,)).fetchone()
    return dict(row) if row else None

