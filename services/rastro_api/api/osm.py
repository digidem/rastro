"""Proxy de tiles do OpenStreetMap — basemap padrão quando não há basemap próprio.

O navegador NUNCA fala com o OSM: pede ``/api/osm/{z}/{x}/{y}.png`` à API (mesma
origem, atrás do token/sessão como o resto de ``/api``), e a API busca o tile no
servidor de tiles. Assim o IP dos monitores não chega ao OSM e a política de CSP
(``connect-src 'self'``) continua fechada. Os tiles ficam num cache em memória
(LRU + TTL) para respeitar a política de uso do OSM (não rebuscar o mesmo tile).

Sensibilidade: a área que o monitor olha é dado sensível — este módulo NUNCA loga
coordenadas de tile (z/x/y), só falhas genéricas.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable

DEFAULT_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "Rastro-map-proxy (+https://github.com/digidem/rastro)"
MAX_ZOOM = 19
TTL_SECS = 6 * 3600
MAX_ITENS = 1000
MAX_BYTES = 512 * 1024
TIMEOUT_SECS = 8


class TileIndisponivel(Exception):
    """O servidor de tiles não respondeu um PNG válido (rede, HTTP, tamanho)."""


class OsmTiles:
    def __init__(
        self,
        url_template: str = DEFAULT_URL,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = TIMEOUT_SECS,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[..., object] | None = None,
    ) -> None:
        if not url_template.startswith("https://") or any(
            marca not in url_template for marca in ("{z}", "{x}", "{y}")
        ):
            raise RuntimeError(
                "RASTRO_OSM_TILE_URL inválida: use https://… com {z}, {x} e {y}"
            )
        self._url = url_template
        self._ua = user_agent
        self._timeout = timeout
        self._clock = clock
        self._open = opener or urllib.request.urlopen
        self._cache: OrderedDict[tuple[int, int, int], tuple[float, bytes]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: dict | None = None) -> "OsmTiles | None":
        """``None`` quando desligado (RASTRO_OSM_TILES=0): a rota responde 404."""
        env = os.environ if env is None else env
        if env.get("RASTRO_OSM_TILES", "1").strip().lower() in (
            "0", "false", "no", "off", "nao", "não",
        ):
            return None
        return cls(env.get("RASTRO_OSM_TILE_URL") or DEFAULT_URL)

    @staticmethod
    def valido(z: int, x: int, y: int) -> bool:
        if not 0 <= z <= MAX_ZOOM:
            return False
        limite = 1 << z
        return 0 <= x < limite and 0 <= y < limite

    def get(self, z: int, x: int, y: int) -> bytes:
        chave = (z, x, y)
        agora = self._clock()
        with self._lock:
            hit = self._cache.get(chave)
            if hit is not None and hit[0] > agora:
                self._cache.move_to_end(chave)
                return hit[1]
        dados = self._buscar(z, x, y)
        with self._lock:
            self._cache[chave] = (agora + TTL_SECS, dados)
            self._cache.move_to_end(chave)
            while len(self._cache) > MAX_ITENS:
                self._cache.popitem(last=False)
        return dados

    def _buscar(self, z: int, x: int, y: int) -> bytes:
        url = self._url.format(z=z, x=x, y=y)
        req = urllib.request.Request(
            url, headers={"User-Agent": self._ua, "Accept": "image/png"}
        )
        try:
            with self._open(req, timeout=self._timeout) as resp:  # type: ignore[misc]
                tipo = resp.headers.get("Content-Type", "")
                dados = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError):
            raise TileIndisponivel("falha ao buscar o tile") from None
        if not tipo.startswith("image/") or not dados or len(dados) > MAX_BYTES:
            raise TileIndisponivel("resposta inválida do servidor de tiles")
        return dados
