"""Proxy de tiles OSM (/api/osm/z/x/y.png) — unit tests, sem rede e sem Postgres.

O servidor de tiles é substituído por um ``opener`` falso; a rota é exercida via
TestClient sem lifespan (como em test_api_https.py).
"""
import io
import urllib.error

import pytest
from fastapi.testclient import TestClient

from rastro_api.api import osm as osm_mod
from rastro_api.api.main import create_app
from rastro_api.api.osm import OsmTiles, TileIndisponivel

AUTH = {"Authorization": "Bearer teste-token-123"}
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


class _Resp:
    def __init__(self, corpo=PNG, tipo="image/png"):
        self._corpo, self.headers = corpo, {"Content-Type": tipo}

    def read(self, n=-1):
        return self._corpo[:n] if n >= 0 else self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _tiles(resp=None, erro=None, **kw):
    chamadas = []

    def opener(req, timeout=None):
        chamadas.append((req.full_url, req.get_header("User-agent"), timeout))
        if erro:
            raise erro
        return resp or _Resp()

    return OsmTiles("https://t.example/{z}/{x}/{y}.png", opener=opener, **kw), chamadas


def test_busca_e_cacheia():
    t, chamadas = _tiles()
    assert t.get(3, 2, 1) == PNG
    assert t.get(3, 2, 1) == PNG
    assert len(chamadas) == 1  # segunda vez veio do cache
    assert chamadas[0][0] == "https://t.example/3/2/1.png"
    assert "Rastro" in chamadas[0][1]  # User-Agent identificável (política do OSM)


def test_cache_expira():
    relogio = [0.0]
    t, chamadas = _tiles(clock=lambda: relogio[0])
    t.get(1, 0, 0)
    relogio[0] = osm_mod.TTL_SECS + 1
    t.get(1, 0, 0)
    assert len(chamadas) == 2


def test_cache_lru_limitado(monkeypatch):
    monkeypatch.setattr(osm_mod, "MAX_ITENS", 3)
    t, chamadas = _tiles()
    for x in range(5):
        t.get(4, x, 0)
    t.get(4, 4, 0)  # ainda em cache
    assert len(chamadas) == 5
    t.get(4, 0, 0)  # já despejado
    assert len(chamadas) == 6


@pytest.mark.parametrize(
    "z,x,y,ok",
    [(0, 0, 0, True), (19, 524287, 524287, True), (20, 0, 0, False),
     (2, 4, 0, False), (2, 0, -1, False), (-1, 0, 0, False)],
)
def test_valido(z, x, y, ok):
    assert OsmTiles.valido(z, x, y) is ok


def test_falhas_viram_tile_indisponivel():
    for kw in (
        {"erro": urllib.error.URLError("x")},
        {"erro": TimeoutError()},
        {"resp": _Resp(tipo="text/html")},
        {"resp": _Resp(corpo=b"")},
        {"resp": _Resp(corpo=b"x" * (osm_mod.MAX_BYTES + 5))},
    ):
        t, _ = _tiles(**kw)
        with pytest.raises(TileIndisponivel):
            t.get(1, 0, 0)


def test_url_invalida():
    for url in ("http://t/{z}/{x}/{y}.png", "https://t/{z}/{x}.png", ""):
        with pytest.raises(RuntimeError):
            OsmTiles(url)


def test_from_env_desligado():
    for v in ("0", "false", "OFF", "não"):
        assert OsmTiles.from_env({"RASTRO_OSM_TILES": v}) is None
    assert OsmTiles.from_env({}) is not None


def _client(monkeypatch, ligado=True, urlopen=None):
    monkeypatch.setenv("RASTRO_API_AUTH", "token")
    monkeypatch.delenv("RASTRO_REQUIRE_HTTPS", raising=False)
    monkeypatch.setenv("RASTRO_OSM_TILES", "1" if ligado else "0")
    monkeypatch.setenv("RASTRO_OSM_TILE_URL", "https://t.example/{z}/{x}/{y}.png")
    monkeypatch.setattr(
        osm_mod.urllib.request, "urlopen", urlopen or (lambda req, timeout=None: _Resp())
    )
    return TestClient(create_app())


def test_rota_exige_auth(monkeypatch):
    assert _client(monkeypatch).get("/api/osm/1/0/0.png").status_code == 401


def test_rota_devolve_png(monkeypatch):
    r = _client(monkeypatch).get("/api/osm/1/0/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"  # nada de cache no navegador
    assert r.content == PNG


def test_rota_fora_do_intervalo_e_desligada(monkeypatch):
    assert _client(monkeypatch).get("/api/osm/2/9/0.png", headers=AUTH).status_code == 404
    r = _client(monkeypatch, ligado=False).get("/api/osm/1/0/0.png", headers=AUTH)
    assert r.status_code == 404


def test_rota_falha_do_servidor_502_sem_detalhes(monkeypatch):
    def falha(req, timeout=None):
        raise urllib.error.URLError("dns")

    c = _client(monkeypatch, urlopen=falha)
    r = c.get("/api/osm/1/1/1.png", headers=AUTH)
    assert r.status_code == 502
    assert r.json() == {"detail": "mapa base indisponível"}
