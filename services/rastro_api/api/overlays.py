"""Camadas GeoJSON extras para o mapa, vindas de uma URL estática configurável.

``RASTRO_OVERLAYS_URL`` aponta para um arquivo .geojson/.json ou para um pacote
.zip (ex.: link público de pasta do File Browser, que responde um zip com todos
os arquivos). Cada .geojson/.json encontrado vira uma camada. A API busca e
guarda em cache (TTL); o navegador só fala com ``/api/overlays`` (mesma origem,
sem CORS e com a CSP ``connect-src 'self'`` intacta).
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable

# Pasta pública (zip com todos os .geojson: aldeias DSEI-VAJ e contorno da TI Javari).
DEFAULT_URL = "https://files.javari.guardianconnector.net/api/public/dl/M_A2PpDV/"
USER_AGENT = "Rastro-overlays-proxy (+https://github.com/digidem/rastro)"
TTL_SECS = 3600
TIMEOUT_SECS = 15
MAX_BYTES = 25 * 1024 * 1024
MAX_ARQUIVOS = 50
_EXTS = (".geojson", ".json")


class OverlaysIndisponiveis(Exception):
    """A fonte não respondeu um GeoJSON/zip válido."""


def _nome(caminho: str) -> str:
    base = caminho.rsplit("/", 1)[-1]
    for ext in _EXTS:
        if base.lower().endswith(ext):
            return base[: -len(ext)]
    return base


def _feature_collection(dados: bytes) -> dict | None:
    try:
        obj = json.loads(dados)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        return obj
    if isinstance(obj, dict) and obj.get("type") == "Feature":
        return {"type": "FeatureCollection", "features": [obj]}
    return None


class Overlays:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        timeout: float = TIMEOUT_SECS,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[..., object] | None = None,
    ) -> None:
        if not url.startswith("https://"):
            raise RuntimeError("RASTRO_OVERLAYS_URL inválida: use https://…")
        self._url = url
        self._timeout = timeout
        self._clock = clock
        self._open = opener or urllib.request.urlopen
        self._cache: tuple[float, list[dict]] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Overlays | None":
        """``None`` quando desligado (RASTRO_OVERLAYS_URL vazio ou 0/off)."""
        env = os.environ if env is None else env
        url = env.get("RASTRO_OVERLAYS_URL")
        if url is None:
            return cls(DEFAULT_URL)
        url = url.strip()
        if url.lower() in ("", "0", "false", "no", "off", "nao", "não"):
            return None
        return cls(url)

    def get(self) -> list[dict]:
        agora = self._clock()
        with self._lock:
            if self._cache is not None and self._cache[0] > agora:
                return self._cache[1]
        camadas = self._buscar()
        with self._lock:
            self._cache = (agora + TTL_SECS, camadas)
        return camadas

    def _buscar(self) -> list[dict]:
        req = urllib.request.Request(self._url, headers={"User-Agent": USER_AGENT})
        try:
            with self._open(req, timeout=self._timeout) as resp:  # type: ignore[misc]
                dados = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError):
            raise OverlaysIndisponiveis("falha ao buscar as camadas") from None
        if not dados or len(dados) > MAX_BYTES:
            raise OverlaysIndisponiveis("resposta inválida da fonte de camadas")
        if dados[:2] == b"PK":
            return self._de_zip(dados)
        fc = _feature_collection(dados)
        if fc is None:
            raise OverlaysIndisponiveis("fonte sem GeoJSON válido")
        return [{"name": fc.get("name") or _nome(self._url.split("?")[0]), "data": fc}]

    @staticmethod
    def _de_zip(dados: bytes) -> list[dict]:
        camadas: list[dict] = []
        try:
            with zipfile.ZipFile(io.BytesIO(dados)) as zf:
                for info in sorted(zf.infolist(), key=lambda i: i.filename):
                    if info.is_dir() or not info.filename.lower().endswith(_EXTS):
                        continue
                    if info.file_size > MAX_BYTES or len(camadas) >= MAX_ARQUIVOS:
                        continue
                    fc = _feature_collection(zf.read(info))
                    if fc is not None:
                        camadas.append({"name": _nome(info.filename), "data": fc})
        except zipfile.BadZipFile:
            raise OverlaysIndisponiveis("zip inválido") from None
        return camadas
