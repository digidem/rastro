"""Autenticação da API: Bearer, cookie de sessão HttpOnly e modo 'desativada'.

Cobre 401 sem/errado e 200 com token (Bearer), healthz aberto; emissão e
validação do cookie de sessão (lembrar, Secure, janela do exp, rotação do
token); modo desativada só dispensa credencial para Host local sem
X-Rastro-Via; Cache-Control: no-store; bind do compose em 127.0.0.1.
"""
import hmac
import time
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from rastro_api.api.main import create_app

AUTH = {"Authorization": "Bearer teste-token-123"}
# mesmo token forçado no conftest (os testes nunca leem o token real do deploy)
TOKEN = "teste-token-123"


def _cookie_assinado(token: str, exp: int) -> str:
    """Mesma fórmula do servidor — ``exp.assinatura`` — para casos de borda."""
    chave = hmac.new(token.encode(), b"rastro-sessao-v1", sha256).digest()
    return f"{exp}.{hmac.new(chave, f'rastro-sessao-v1:{exp}'.encode(), sha256).hexdigest()}"


def _emitir_sessao(client, lembrar: bool = True) -> str:
    """Emite sessão via API (Bearer válido) e devolve o valor do cookie."""
    resposta = client.post(
        "/api/auth/sessao", json={"lembrar": lembrar}, headers=AUTH
    )
    assert resposta.status_code == 204, resposta.text
    bruto = resposta.headers["set-cookie"]
    return bruto.split("rastro_sessao=", 1)[1].split(";", 1)[0]


def test_sem_token_rejeita_401(client):
    resposta = client.get("/api/nodes/latest")
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "token inválido"}


def test_token_errado_rejeita_401(client):
    resposta = client.get(
        "/api/nodes/latest", headers={"Authorization": "Bearer senha-errada"}
    )
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "token inválido"}


def test_token_valido_consulta_200(client):
    resposta = client.get("/api/nodes/latest", headers=AUTH)
    assert resposta.status_code == 200


def test_healthz_sem_token_200(client):
    resposta = client.get("/api/healthz")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert "db_latency_ms" in corpo


def test_estado_sem_credencial(client):
    resposta = client.get("/api/auth/estado")
    assert resposta.status_code == 200
    assert resposta.json() == {
        "exigida": True,
        "autenticado": False,
        "dias_lembrar": 30,
    }


def test_estado_com_bearer(client):
    resposta = client.get("/api/auth/estado", headers=AUTH)
    assert resposta.json()["autenticado"] is True


def test_estado_com_cookie(client):
    valor = _emitir_sessao(client)
    resposta = client.get(
        "/api/auth/estado", headers={"Cookie": f"rastro_sessao={valor}"}
    )
    assert resposta.json()["autenticado"] is True


def test_sessao_lembrar_true_flags_do_cookie(client):
    resposta = client.post("/api/auth/sessao", json={"lembrar": True}, headers=AUTH)
    assert resposta.status_code == 204
    bruto = resposta.headers["set-cookie"].lower()
    assert "httponly" in bruto
    assert "samesite=strict" in bruto
    assert "path=/api" in bruto
    assert "max-age=2592000" in bruto  # 30 dias


def test_sessao_lembrar_false_sem_max_age(client):
    resposta = client.post("/api/auth/sessao", json={"lembrar": False}, headers=AUTH)
    assert resposta.status_code == 204
    bruto = resposta.headers["set-cookie"].lower()
    assert "rastro_sessao=" in bruto
    assert "max-age" not in bruto


def test_sessao_bearer_errado_401_sem_cookie(client):
    resposta = client.post(
        "/api/auth/sessao",
        json={"lembrar": True},
        headers={"Authorization": "Bearer senha-errada"},
    )
    assert resposta.status_code == 401
    assert "set-cookie" not in resposta.headers


def test_cookie_emitido_autentica_sem_bearer(client, dados):
    valor = _emitir_sessao(client)
    resposta = client.get(
        "/api/nodes/latest", headers={"Cookie": f"rastro_sessao={valor}"}
    )
    assert resposta.status_code == 200


def test_cookies_invalidos_401(client):
    valor = _emitir_sessao(client)
    exp, _, sig = valor.partition(".")
    troca = "1" if sig[0] != "1" else "2"
    casos = [
        f"{exp}.{troca}{sig[1:]}",  # assinatura adulterada (1 char)
        _cookie_assinado(TOKEN, int(time.time()) - 60),  # exp no passado
        f"12ab.{sig}",  # exp não numérico
    ]
    for invalido in casos:
        resposta = client.get(
            "/api/nodes/latest", headers={"Cookie": f"rastro_sessao={invalido}"}
        )
        assert resposta.status_code == 401, invalido
    # dígito Unicode "²" (byte 0xB2, latin-1): 401, não 500 — httpx exige bytes
    resposta = client.get(
        "/api/nodes/latest",
        headers={"Cookie": f"rastro_sessao=12².{sig}".encode("latin-1")},
    )
    assert resposta.status_code == 401


def test_delete_sessao_204_limpa_cookie(client):
    _emitir_sessao(client)
    resposta = client.delete("/api/auth/sessao")
    assert resposta.status_code == 204
    bruto = resposta.headers["set-cookie"].lower()
    assert "max-age=0" in bruto or ("expires=" in bruto and "1970" in bruto)


def test_auth_invalida_levanta_runtimeerror(monkeypatch):
    monkeypatch.setenv("RASTRO_API_AUTH", "qualquer")
    with pytest.raises(RuntimeError):
        create_app()


def test_sessao_dias_invalidos_levantam_runtimeerror(monkeypatch):
    monkeypatch.setenv("RASTRO_API_AUTH", "token")
    for bruto in ("0", "abc", "91"):
        monkeypatch.setenv("RASTRO_SESSAO_DIAS", bruto)
        with pytest.raises(RuntimeError):
            create_app()


def test_cookie_sempre_secure(client):
    # Secure é incondicional: a API pública só serve por HTTPS (proxy reverso)
    resposta = client.post(
        "/api/auth/sessao",
        json={"lembrar": True},
        headers={**AUTH, "X-Forwarded-Proto": "https"},
    )
    assert "secure" in resposta.headers["set-cookie"].lower()
    resposta = client.post("/api/auth/sessao", json={"lembrar": True}, headers=AUTH)
    assert "secure" in resposta.headers["set-cookie"].lower()


def test_rotacao_de_token_revoga_cookie(client, monkeypatch, dados):
    valor = _emitir_sessao(client)
    monkeypatch.setenv("RASTRO_API_TOKEN", "token-novo-rotacionado")
    with TestClient(create_app()) as novo_client:
        resposta = novo_client.get(
            "/api/nodes/latest", headers={"Cookie": f"rastro_sessao={valor}"}
        )
    assert resposta.status_code == 401


def test_cookie_exp_acima_do_teto_401(client):
    # 301 s acima do teto de 300 s + 60 s de folga: não depende do tick do
    # relógio entre a emissão no teste e a validação no servidor
    agora = int(time.time())
    valor = _cookie_assinado(TOKEN, agora + 30 * 86400 + 361)
    resposta = client.get(
        "/api/nodes/latest", headers={"Cookie": f"rastro_sessao={valor}"}
    )
    assert resposta.status_code == 401


def test_sessao_sem_lembrar_expira_em_12h(client):
    antes = time.time()
    resposta = client.post("/api/auth/sessao", json={"lembrar": False}, headers=AUTH)
    assert resposta.status_code == 204
    valor = resposta.headers["set-cookie"].split("rastro_sessao=", 1)[1].split(";", 1)[0]
    exp = int(valor.split(".")[0])
    assert abs(exp - antes - 12 * 3600) <= 60


def test_sessao_dias_configuravel(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_SESSAO_DIAS", "7")
    with TestClient(create_app()) as novo_client:
        resposta = novo_client.post(
            "/api/auth/sessao", json={"lembrar": True}, headers=AUTH
        )
    assert resposta.status_code == 204
    assert "max-age=604800" in resposta.headers["set-cookie"].lower()


def test_desativada_host_local_passa_sem_credencial(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    with TestClient(create_app(), base_url="http://127.0.0.1:8081") as c:
        assert c.get("/api/nodes/latest").status_code == 200
        estado = c.get("/api/auth/estado").json()
        assert estado["exigida"] is False


def test_desativada_host_remoto_401(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    with TestClient(create_app(), base_url="http://evil.example:8081") as c:
        resposta = c.get("/api/nodes/latest")
        assert resposta.status_code == 401
        assert resposta.json() == {"detail": "token inválido"}


def test_desativada_host_local_com_via_remota_401(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    with TestClient(create_app(), base_url="http://127.0.0.1:8081") as c:
        resposta = c.get("/api/nodes/latest", headers={"X-Rastro-Via": "remoto"})
        assert resposta.status_code == 401


def test_desativada_host_local_via_remota_vazia_401(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    with TestClient(create_app(), base_url="http://127.0.0.1:8081") as c:
        resposta = c.get("/api/nodes/latest", headers={"X-Rastro-Via": ""})
        assert resposta.status_code == 401


def test_desativada_remoto_com_credencial(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    with TestClient(create_app(), base_url="http://evil.example:8081") as c:
        assert c.get("/api/nodes/latest", headers=AUTH).status_code == 200
        resposta = c.post("/api/auth/sessao", json={"lembrar": True}, headers=AUTH)
        assert resposta.status_code == 204
        valor = resposta.headers["set-cookie"].split("rastro_sessao=", 1)[1].split(";", 1)[0]
        resposta = c.get(
            "/api/nodes/latest", headers={"Cookie": f"rastro_sessao={valor}"}
        )
        assert resposta.status_code == 200


def test_desativada_sem_token_falha_fechada(monkeypatch, dados):
    monkeypatch.setenv("RASTRO_API_AUTH", "desativada")
    monkeypatch.delenv("RASTRO_API_TOKEN")
    with TestClient(create_app(), base_url="http://evil.example:8081") as c:
        resposta = c.get("/api/nodes/latest", headers={"Authorization": "Bearer "})
        assert resposta.status_code == 401
        resposta = c.post("/api/auth/sessao", json={"lembrar": True})
        assert resposta.status_code == 409
        assert resposta.json() == {
            "detail": "autenticação por token não configurada"
        }


def test_post_sessao_com_cookie_sem_bearer_401(client):
    _emitir_sessao(client)
    resposta = client.post("/api/auth/sessao", json={"lembrar": True})
    assert resposta.status_code == 401
    assert "set-cookie" not in resposta.headers


def test_cache_control_no_store(client, dados):
    resposta = client.get("/api/nodes/latest", headers=AUTH)
    assert resposta.headers["cache-control"] == "no-store"
    resposta = client.get("/api/auth/estado")
    assert resposta.headers["cache-control"] == "no-store"
    resposta = client.get("/api/nodes/latest")
    assert resposta.status_code == 401
    assert resposta.headers["cache-control"] == "no-store"


def test_compose_publica_somente_no_loopback():
    compose = yaml.safe_load(
        (
            Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
    )
    violacoes = []
    for nome, servico in compose["services"].items():
        for porta in servico.get("ports") or []:
            bruto = porta if isinstance(porta, str) else str(porta.get("host_ip", ""))
            if not bruto.startswith("127.0.0.1:"):
                violacoes.append(f"{nome}: {bruto}")
    assert violacoes == [], f"portas fora do loopback: {violacoes}"


def test_sessao_exige_content_type_json(client):
    corpo = b'{"lembrar": true}'
    resposta = client.post(
        "/api/auth/sessao",
        content=corpo,
        headers={**AUTH, "Content-Type": "text/plain"},
    )
    assert resposta.status_code == 415
    resposta = client.post("/api/auth/sessao", content=corpo, headers=AUTH)
    assert resposta.status_code == 415
