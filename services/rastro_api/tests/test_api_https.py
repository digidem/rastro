"""HTTPS obrigatório (RASTRO_REQUIRE_HTTPS) atrás do proxy reverso — unit tests.

Nenhum teste exige Postgres: o TestClient é criado SEM entrar no lifespan (o
pool abriria preguiçoso com wait=False e jamais seria tocado nestes caminhos)
e, no único caso que alcançaria o banco (/api/healthz), app.state.pool recebe
um stub que falha como banco fora do ar — a rota devolve 503, nunca 403.
"""
import psycopg
import pytest
from fastapi.testclient import TestClient

from rastro_api.api.main import create_app

# mesmo token forçado no conftest (os testes nunca leem o token real do deploy)
AUTH = {"Authorization": "Bearer teste-token-123"}
HTTPS = {"X-Forwarded-Proto": "https"}


class _PoolSemBanco:
    """Substituto do pool: connection() falha igual a um banco fora do ar."""

    def connection(self):
        raise psycopg.OperationalError("sem banco nestes testes")


def _client(monkeypatch, require_https: str | None) -> TestClient:
    """App com RASTRO_REQUIRE_HTTPS fixado; sem lifespan, logo sem pool real."""
    monkeypatch.setenv("RASTRO_API_AUTH", "token")
    if require_https is None:
        monkeypatch.delenv("RASTRO_REQUIRE_HTTPS", raising=False)
    else:
        monkeypatch.setenv("RASTRO_REQUIRE_HTTPS", require_https)
    app = create_app()
    app.state.pool = _PoolSemBanco()
    return TestClient(app)


def test_https_exigido_sem_header_403_antes_do_401(monkeypatch):
    # o middleware roda antes da autenticação: 403, nunca 401
    resposta = _client(monkeypatch, "1").get("/api/nodes/latest")
    assert resposta.status_code == 403
    assert resposta.json() == {"detail": "HTTPS obrigatório"}


def test_https_exigido_com_https_401_normal(monkeypatch):
    c = _client(monkeypatch, "true")
    resposta = c.get("/api/nodes/latest", headers=HTTPS)
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "token inválido"}
    # comparação case-insensitive do valor do header
    resposta = c.get(
        "/api/nodes/latest", headers={"X-Forwarded-Proto": "HTTPS"}
    )
    assert resposta.status_code == 401


def test_https_exigido_com_http_403(monkeypatch):
    resposta = _client(monkeypatch, "yes").get(
        "/api/nodes/latest", headers={"X-Forwarded-Proto": "http"}
    )
    assert resposta.status_code == 403
    assert resposta.json() == {"detail": "HTTPS obrigatório"}


def test_healthz_livre_do_403(monkeypatch):
    # sem header e com o banco "fora do ar" (stub): 503 (ou 200), jamais 403
    resposta = _client(monkeypatch, "1").get("/api/healthz")
    assert resposta.status_code in (200, 503)


def test_sem_exigir_https_mantem_401(monkeypatch):
    # RASTRO_REQUIRE_HTTPS ausente (default off): comportamento antigo
    resposta = _client(monkeypatch, None).get("/api/nodes/latest")
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "token inválido"}


def test_cookie_de_sessao_secure(monkeypatch):
    resposta = _client(monkeypatch, "1").post(
        "/api/auth/sessao", json={"lembrar": True}, headers={**AUTH, **HTTPS}
    )
    assert resposta.status_code == 204
    assert "secure" in resposta.headers["set-cookie"].lower()


def test_desativada_com_hosts_vazios_recusa_subir(monkeypatch):
    # definida vazia (ou só com vírgulas/espaços) rende conjunto VAZIO — não
    # cai no default — e o app recusa subir nesse modo
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    for bruto in ("", " , ", ",,"):
        monkeypatch.setenv("RASTRO_API_HOSTS_LOCAIS", bruto)
        with pytest.raises(
            RuntimeError, match="RASTRO_API_HOSTS_LOCAIS não vazio"
        ):
            create_app()


def test_desativada_sem_hosts_locais_nao_recusa(monkeypatch):
    # variável AUSENTE: usa o default não vazio — não pode levantar
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    monkeypatch.delenv("RASTRO_API_HOSTS_LOCAIS", raising=False)
    create_app()