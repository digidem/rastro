"""Testes da API de chat (/api/chat/*) — unit tests sem Postgres real (WP-F1).

Cobre:
- Autenticação obrigatória (401 sem sessão / credencial inválida; 200 com Bearer ou cookie)
- GET /api/chat/messages (newest-last, campos exigidos, filtro since)
- GET /api/chat/boats (barcos ativos)
- POST /api/chat/send (validação 1..200 bytes UTF-8, barco ativo 404, TTL default 900 s e custom, session user label)
- GET /api/chat/outbox/{id} (rótulos em português: 'na fila', 'enviado ao gateway do barco', 'expirada (não entregue)', 'falhou' — NUNCA 'lido')
- NUNCA logar texto da mensagem
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
from hashlib import sha256
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rastro_api.api.main import create_app

TOKEN = "teste-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _cookie_assinado(token: str, exp: int) -> str:
    """Emite valor de cookie de sessão válido idêntico ao gerado pelo servidor."""
    chave = hmac.new(token.encode(), b"rastro-sessao-v1", sha256).digest()
    sig = hmac.new(chave, f"rastro-sessao-v1:{exp}".encode(), sha256).hexdigest()
    return f"{exp}.{sig}"


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
        self.committed = False

    def execute(self, query: str, params: Any = None):
        self.queries.append((query, params))
        if self.handler:
            rows = self.handler(query, params)
            return _FakeCursor(rows)
        return _FakeCursor([])

    def commit(self):
        self.committed = True


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


def test_chat_auth_required(monkeypatch):
    conn = _FakeConn()
    client = _make_client(conn, monkeypatch)

    # 401 sem qualquer credencial
    assert client.get("/api/chat/messages").status_code == 401
    assert client.get("/api/chat/boats").status_code == 401
    assert client.post("/api/chat/send", json={"boat": "b1", "text": "oi"}).status_code == 401
    assert client.get("/api/chat/outbox/1").status_code == 401

    # 401 com token errado
    headers_errados = {"Authorization": "Bearer token-errado"}
    assert client.get("/api/chat/messages", headers=headers_errados).status_code == 401

    # 200 com Bearer válido
    conn.handler = lambda q, p: []
    assert client.get("/api/chat/messages", headers=AUTH).status_code == 200

    # 200 com cookie de sessão válido
    agora = int(dt.datetime.now(dt.timezone.utc).timestamp())
    cookie_val = _cookie_assinado(TOKEN, agora + 3600)
    res_cookie = client.get("/api/chat/messages", headers={"Cookie": f"rastro_sessao={cookie_val}"})
    assert res_cookie.status_code == 200


def test_chat_messages_fields_and_order(monkeypatch):
    t1 = dt.datetime(2026, 10, 5, 10, 0, 0, tzinfo=dt.timezone.utc)
    t2 = dt.datetime(2026, 10, 5, 10, 5, 0, tzinfo=dt.timezone.utc)

    mock_rows = [
        {
            "id": 1,
            "direction": "in",
            "boat_id": "b1",
            "from_num": 123456,
            "text": "Socorro motor parou",
            "is_alert": True,
            "observed_at": t1,
            "received_at": t1,
        },
        {
            "id": 2,
            "direction": "out",
            "boat_id": "b1",
            "from_num": 999999,
            "text": "Equipe a caminho",
            "is_alert": False,
            "observed_at": None,
            "received_at": t2,
            "outbox_id": 42,
        },
    ]

    def handler(query: str, params: Any):
        assert "FROM chat_messages" in query
        assert "ORDER BY received_at ASC, id ASC" in query
        return mock_rows

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/chat/messages", headers=AUTH)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2

    # Verifica todos os campos obrigatórios
    m1 = data[0]
    assert m1["id"] == 1
    assert m1["direction"] == "in"
    assert m1["boat_id"] == "b1"
    assert m1["from_num"] == 123456
    assert m1["text"] == "Socorro motor parou"
    assert m1["is_alert"] is True
    assert m1["observed_at"] == "2026-10-05T10:00:00Z"
    assert m1["received_at"] == "2026-10-05T10:00:00Z"
    assert m1["outbox_id"] is None

    # newest-last: mensagem 1 (10:00) antes da mensagem 2 (10:05)
    m2 = data[1]
    assert m2["id"] == 2
    assert m2["direction"] == "out"
    assert m2["is_alert"] is False
    assert m2["observed_at"] is None
    assert m2["received_at"] == "2026-10-05T10:05:00Z"
    assert m2["outbox_id"] == 42


def test_chat_messages_since_filter(monkeypatch):
    captured_params = []

    def handler(query: str, params: Any):
        if params:
            captured_params.append(params[0])
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    # Parâmetro since ISO válido com sobreposição de 60 s
    res = client.get(
        "/api/chat/messages",
        headers=AUTH,
        params={"since": "2026-10-05T09:00:00Z"},
    )
    assert res.status_code == 200
    assert len(captured_params) == 1
    assert isinstance(captured_params[0], dt.datetime)
    assert captured_params[0] == dt.datetime(2026, 10, 5, 8, 59, 0, tzinfo=dt.timezone.utc)

    # Parâmetro since inválido vira 400
    res_invalido = client.get(
        "/api/chat/messages",
        headers=AUTH,
        params={"since": "data-invalida"},
    )
    assert res_invalido.status_code == 400
    assert "parâmetro 'since' inválido" in res_invalido.json()["detail"]


def test_chat_messages_without_since_limit_and_before_id(monkeypatch):
    executed = []

    def handler(query: str, params: Any):
        executed.append((query, params))
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    # 1. Sem since: default limit=200
    res1 = client.get("/api/chat/messages", headers=AUTH)
    assert res1.status_code == 200
    q1, p1 = executed[-1]
    assert "LIMIT %s" in q1
    assert p1 == (200,)

    # 2. Limit customizado
    res2 = client.get("/api/chat/messages", headers=AUTH, params={"limit": 50})
    assert res2.status_code == 200
    q2, p2 = executed[-1]
    assert p2 == (50,)

    # 3. Limit acima de 500 é limitado a 500
    res3 = client.get("/api/chat/messages", headers=AUTH, params={"limit": 999})
    assert res3.status_code == 200
    q3, p3 = executed[-1]
    assert p3 == (500,)

    # 4. Paginação com before_id
    res4 = client.get("/api/chat/messages", headers=AUTH, params={"before_id": 42})
    assert res4.status_code == 200
    q4, p4 = executed[-1]
    assert "WHERE id < %s" in q4
    assert p4 == (42, 200)


def test_chat_boats_active(monkeypatch):
    mock_boats = [
        {"boat_id": "barco-alpha", "gateway_id": "!f0000001", "virtual_node_num": 1001, "active": True},
        {"boat_id": "barco-beta", "gateway_id": "!f0000002", "virtual_node_num": 1002, "active": True},
    ]

    def handler(query: str, params: Any):
        assert "FROM virtual_gateways" in query
        assert "active IS TRUE" in query
        return mock_boats

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/chat/boats", headers=AUTH)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert data[0]["boat_id"] == "barco-alpha"
    assert data[0]["gateway_id"] == "!f0000001"
    assert data[0]["active"] is True
    assert data[1]["boat_id"] == "barco-beta"


def test_chat_send_validation(monkeypatch):
    conn = _FakeConn(handler=lambda q, p: [])
    client = _make_client(conn, monkeypatch)

    # Texto vazio (0 bytes) -> 422
    res_vazio = client.post("/api/chat/send", headers=AUTH, json={"boat": "b1", "text": ""})
    assert res_vazio.status_code == 422

    # Texto > 200 bytes UTF-8 -> 422
    texto_longo = "a" * 201
    res_longo = client.post("/api/chat/send", headers=AUTH, json={"boat": "b1", "text": texto_longo})
    assert res_longo.status_code == 422

    # Multibyte UTF-8 (ex.: 'ã' ocupa 2 bytes) estourando 200 bytes
    texto_acentos = "ã" * 101  # 202 bytes
    res_acentos = client.post("/api/chat/send", headers=AUTH, json={"boat": "b1", "text": texto_acentos})
    assert res_acentos.status_code == 422

    # Barco ausente / não informado -> 404
    res_sem_barco = client.post("/api/chat/send", headers=AUTH, json={"text": "ola"})
    assert res_sem_barco.status_code == 404


def test_chat_send_boat_not_found_or_inactive_404(monkeypatch):
    def handler(query: str, params: Any):
        if "FROM virtual_gateways" in query:
            # Barco não encontrado ou inativo
            return []
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.post(
        "/api/chat/send",
        headers=AUTH,
        json={"boat": "barco-fantasma", "text": "mensagem de teste"},
    )
    assert res.status_code == 404
    assert "barco não encontrado ou inativo" in res.json()["detail"]


def test_chat_send_success_and_ttl_default(monkeypatch):
    saved_rows = []

    def handler(query: str, params: Any):
        if "FROM virtual_gateways" in query:
            return [{"boat_id": "b1", "gateway_id": "!f0000001", "virtual_node_num": 1001, "active": True}]
        if "INSERT INTO chat_outbox" in query:
            # params: (boat_id, text, created_by, expires_at)
            saved_rows.append(params)
            return [{"id": 10, "status": "queued", "expires_at": params[3]}]
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    agora = dt.datetime.now(dt.timezone.utc)
    res = client.post(
        "/api/chat/send",
        headers=AUTH,
        json={"boat": "b1", "text": "Mensagem rápida"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 10
    assert data["status"] == "queued"
    assert data["expires_at"].endswith("Z")

    # Verifica que o TTL padrão de 900 s foi aplicado no banco
    assert len(saved_rows) == 1
    boat_id, text, created_by, expires_at = saved_rows[0]
    assert boat_id == "b1"
    assert text == "Mensagem rápida"
    # Sessão com token compartilhado -> rotulo fixo 'escritorio'
    assert created_by == "escritorio"
    diff_s = (expires_at - agora).total_seconds()
    assert 895 <= diff_s <= 905


def test_chat_send_ttl_custom_and_session_label(monkeypatch):
    monkeypatch.setenv("RASTRO_CHAT_TTL_SECS", "300")
    saved_rows = []

    def handler(query: str, params: Any):
        if "FROM virtual_gateways" in query:
            return [{"boat_id": "b2", "gateway_id": "!f0000002", "virtual_node_num": 1002, "active": True}]
        if "INSERT INTO chat_outbox" in query:
            saved_rows.append(params)
            return [{"id": 15, "status": "queued", "expires_at": params[3]}]
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    agora_sec = int(dt.datetime.now(dt.timezone.utc).timestamp())
    cookie_val = _cookie_assinado(TOKEN, agora_sec + 3600)

    # Envia com cookie de sessão e headers de usuário que DEVEM SER IGNORADOS
    res = client.post(
        "/api/chat/send",
        headers={
            "Cookie": f"rastro_sessao={cookie_val}",
            "X-User-Label": "Operador Malicioso",
            "X-User": "hacker",
        },
        json={"boat_id": "b2", "text": "Texto com TTL customizado"},
    )
    assert res.status_code == 200
    assert len(saved_rows) == 1
    _, _, created_by, expires_at = saved_rows[0]
    # Rótulo derivado exclusivamente da sessão: 'escritorio' (headers do cliente ignorados)
    assert created_by == "escritorio"
    diff_s = (expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
    assert 295 <= diff_s <= 305


def test_chat_outbox_status_labels_never_lido(monkeypatch):
    casos = [
        ("queued", "na fila"),
        ("sent", "enviado ao gateway do barco"),
        ("expired", "expirada (não entregue)"),
        ("failed", "falhou"),
    ]

    for status_code, expected_label in casos:
        def handler(query: str, params: Any, st=status_code):
            return [{
                "id": 99,
                "boat_id": "b1",
                "text": "mensagem",
                "created_by": "operador",
                "created_at": dt.datetime(2026, 10, 5, 8, 0, 0, tzinfo=dt.timezone.utc),
                "expires_at": dt.datetime(2026, 10, 5, 8, 15, 0, tzinfo=dt.timezone.utc),
                "status": st,
                "sent_at": dt.datetime(2026, 10, 5, 8, 1, 0, tzinfo=dt.timezone.utc) if st == "sent" else None,
                "error": "sem rota" if st == "failed" else None,
            }]

        conn = _FakeConn(handler=handler)
        client = _make_client(conn, monkeypatch)

        res = client.get("/api/chat/outbox/99", headers=AUTH)
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == 99
        assert data["status"] == status_code
        assert data["label"] == expected_label

        # REGRA CRÍTICA: NUNCA usar o termo "lido" (não há confirmação de leitura em ponta a ponta)
        serialized = str(data).lower()
        assert "lido" not in serialized
        assert "read" not in serialized


def test_chat_outbox_not_found_404(monkeypatch):
    conn = _FakeConn(handler=lambda q, p: [])
    client = _make_client(conn, monkeypatch)

    res = client.get("/api/chat/outbox/9999", headers=AUTH)
    assert res.status_code == 404
    assert "mensagem não encontrada" in res.json()["detail"]


def test_never_log_chat_text(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret_text = "SEGREDO_CONFIDENCIAL_12345"

    def handler(query: str, params: Any):
        if "FROM virtual_gateways" in query:
            return [{"boat_id": "b1", "gateway_id": "!f0000001", "virtual_node_num": 1001, "active": True}]
        if "INSERT INTO chat_outbox" in query:
            return [{"id": 7, "status": "queued", "expires_at": params[3]}]
        if "FROM chat_messages" in query:
            return [{
                "id": 1,
                "direction": "in",
                "boat_id": "b1",
                "from_num": 101,
                "text": secret_text,
                "is_alert": False,
                "observed_at": None,
                "received_at": dt.datetime.now(dt.timezone.utc),
            }]
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    client.post("/api/chat/send", headers=AUTH, json={"boat": "b1", "text": secret_text})
    client.get("/api/chat/messages", headers=AUTH)

    # Nenhuma linha de log pode conter o texto confidencial da mensagem
    for record in caplog.records:
        assert secret_text not in record.getMessage()


def test_pool_configured_with_default_transaction_read_only(monkeypatch):
    """Verifica que o pool em main.py configura default_transaction_read_only=on."""
    import inspect
    from rastro_api.api import main
    src = inspect.getsource(main.create_app)
    assert "default_transaction_read_only=on" in src

    captured_kwargs = []

    class _MockPool:
        check_connection = staticmethod(lambda conn: None)

        def __init__(self, *args, **kwargs):
            captured_kwargs.append(kwargs)

        def open(self, wait=False):
            pass

        def close(self):
            pass

    monkeypatch.setattr(main, "ConnectionPool", _MockPool)
    monkeypatch.setenv("RASTRO_API_AUTH", "token")
    monkeypatch.setenv("RASTRO_API_TOKEN", TOKEN)
    monkeypatch.setenv("RASTRO_PG_HOST", "127.0.0.1")
    app = main.create_app()
    with TestClient(app):
        pass
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["kwargs"]["options"] == "-c default_transaction_read_only=on"


def test_insert_outbox_message_opens_read_write_transaction():
    """Verifica que insert_outbox_message abre transação READ WRITE e restaura o estado anterior."""
    from rastro_api.api import queries

    class _MockPsycopgConn:
        def __init__(self):
            self.read_only: bool | None = None
            self.read_only_during_tx: bool | None = None
            self.tx_entered = False
            self.tx_exited = False
            self.queries = []

        def transaction(self):
            conn = self

            class _Tx:
                def __enter__(self):
                    conn.tx_entered = True
                    conn.read_only_during_tx = conn.read_only
                    return conn

                def __exit__(self, exc_type, exc_val, exc_tb):
                    conn.tx_exited = True

            return _Tx()

        def execute(self, query, params=None):
            self.queries.append((query, params))
            return _FakeCursor([{"id": 77, "status": "queued", "expires_at": params[3]}])

    mock_conn = _MockPsycopgConn()
    now = dt.datetime.now(dt.timezone.utc)
    res = queries.insert_outbox_message(mock_conn, "b1", "ola", "escritorio", now)

    assert res["id"] == 77
    assert mock_conn.tx_entered is True
    assert mock_conn.tx_exited is True
    # Durante a transação, read_only foi definido como False (READ WRITE)
    assert mock_conn.read_only_during_tx is False
    # Após a transação, read_only foi restaurado para o valor original (None)
    assert mock_conn.read_only is None


def test_client_user_headers_cannot_spoof_created_by(monkeypatch):
    """Garante que cabeçalhos X-User-Label ou X-User não alteram o created_by."""
    saved_rows = []

    def handler(query: str, params: Any):
        if "FROM virtual_gateways" in query:
            return [{"boat_id": "b1", "gateway_id": "!f0000001", "virtual_node_num": 1001, "active": True}]
        if "INSERT INTO chat_outbox" in query:
            saved_rows.append(params)
            return [{"id": 20, "status": "queued", "expires_at": params[3]}]
        return []

    conn = _FakeConn(handler=handler)
    client = _make_client(conn, monkeypatch)

    res = client.post(
        "/api/chat/send",
        headers={
            **AUTH,
            "X-User-Label": "Administrador",
            "X-User": "admin_root",
        },
        json={"boat": "b1", "text": "Teste de spoofing"},
    )
    assert res.status_code == 200
    assert len(saved_rows) == 1
    _, _, created_by, _ = saved_rows[0]
    assert created_by == "escritorio"

