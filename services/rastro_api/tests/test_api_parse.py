"""Parse de node id ASCII-safe: caracteres Unicode não-dígitos na rota → []/404, nunca 500.

``'²'.isdigit()`` é True, mas ``int('²')`` lança ValueError — sem a guarda
``isascii()``, a rota ``/api/nodes/!²/track`` estourava 500.
"""
from .test_api_auth import AUTH


def test_superscript_dois_nunca_500(client):
    resposta = client.get("/api/nodes/!²/track", headers=AUTH)
    assert resposta.status_code in (200, 404)


def test_e_quadrado_outros_coords_nunca_500(client):
    for char in "²³¹⁰⁴":
        resposta = client.get(f"/api/nodes/!{char}/track", headers=AUTH)
        assert resposta.status_code in (200, 404)
        assert resposta.status_code != 500


def test_pg_user_padrao_deriva_do_banco(monkeypatch):
    from rastro_api.api import main as api_main

    monkeypatch.setenv("RASTRO_PG_DB", "mapa")
    monkeypatch.delenv("RASTRO_PG_USER", raising=False)
    assert api_main._pg_user() == "mapa_viewer"
    assert "user=mapa_viewer" in api_main._conninfo()
    monkeypatch.setenv("RASTRO_PG_USER", "")
    assert api_main._pg_user() == "mapa_viewer"
    monkeypatch.setenv("RASTRO_PG_USER", "outro")
    assert api_main._pg_user() == "outro"
