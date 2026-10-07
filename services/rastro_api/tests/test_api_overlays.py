"""Camadas extras (/api/overlays) — unit tests, sem rede e sem Postgres."""
import io
import json
import zipfile

import pytest

from rastro_api.api.overlays import Overlays, OverlaysIndisponiveis

FC = {"type": "FeatureCollection", "features": []}


class _Resp:
    def __init__(self, corpo):
        self._corpo = corpo

    def read(self, n=-1):
        return self._corpo[:n] if n >= 0 else self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _zip(arquivos):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nome, conteudo in arquivos.items():
            zf.writestr(nome, conteudo)
    return buf.getvalue()


def _ov(corpo, chamadas=None):
    def opener(req, timeout=None):
        if chamadas is not None:
            chamadas.append(req.full_url)
        return _Resp(corpo)

    return Overlays("https://f.example/dl/x/", opener=opener)


def test_zip_com_varios_geojson():
    corpo = _zip({"a.geojson": json.dumps(FC), "b.geojson": json.dumps(FC), "x.txt": "n"})
    assert [c["name"] for c in _ov(corpo).get()] == ["a", "b"]


def test_arquivo_unico_e_cache():
    chamadas = []
    ov = _ov(json.dumps(FC).encode(), chamadas)
    assert len(ov.get()) == 1
    ov.get()
    assert len(chamadas) == 1


def test_fonte_invalida():
    with pytest.raises(OverlaysIndisponiveis):
        _ov(b"<html>").get()


def test_env_desligado_e_padrao():
    assert Overlays.from_env({"RASTRO_OVERLAYS_URL": ""}) is None
    assert Overlays.from_env({"RASTRO_OVERLAYS_URL": "off"}) is None
    assert Overlays.from_env({}) is not None
    with pytest.raises(RuntimeError):
        Overlays.from_env({"RASTRO_OVERLAYS_URL": "http://x"})
