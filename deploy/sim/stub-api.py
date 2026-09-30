#!/usr/bin/env python3
"""API falsa do simulador de fallback OSM (deploy/sim/browser-osm.py).

Serve o mínimo que o viewer precisa para chegar ao mapa SEM login e provar o
fallback do basemap — tudo no mesmo estilo de browser-http.py (API falsa):

  GET /api/auth/estado        → {"exigida": false, ...} → o mapa abre sem tela de token
  GET /api/nodes/latest       → FeatureCollection vazia (sem nós)
  GET /api/osm/<z>/<x>/<y>.png → PNG 256×256 gerado aqui (tile OSM falso, sempre 200)
  qualquer outra /api/*       → 404

Sensibilidade: não há dado real — os tiles são sintéticos — e nada é logado
(log_message silenciado: as coordenadas de tile observadas são dado sensível).
Roda DENTRO de um contêiner, na rede do teste; só a stdlib do Python da imagem.
Uso: stub-api.py [porta]   (padrão 8080)
"""
from __future__ import annotations

import json
import re
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# PNG 256×256 (tamanho de tile do mapa) só com a stdlib: IHDR + IDAT + IEND.
def png_256(cor: tuple[int, int, int] = (0x1C, 0x2B, 0x1F)) -> bytes:
    larg = alt = 256
    linha = b"\x00" + bytes(cor) * larg  # filtro 0 + RGB
    bruto = linha * alt

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        corpo = tipo + dados
        return struct.pack(">I", len(dados)) + corpo + struct.pack(">I", zlib.crc32(corpo) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", struct.pack(">IIBBBBB", larg, alt, 8, 2, 0, 0, 0))
        + bloco(b"IDAT", zlib.compress(bruto))
        + bloco(b"IEND", b"")
    )


PNG = png_256()
TILE_RE = re.compile(r"/api/osm/\d+/\d+/\d+\.png")


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # sem log: coordenadas de tile são sensíveis
        pass

    def _ok(self, corpo: bytes, tipo: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self) -> None:  # noqa: N802 (nome do http.server)
        if self.path == "/api/auth/estado":
            self._ok(json.dumps(
                {"exigida": False, "autenticado": False, "dias_lembrar": 30}
            ).encode(), "application/json")
        elif TILE_RE.fullmatch(self.path):
            self._ok(PNG, "image/png")
        elif self.path == "/api/nodes/latest":
            self._ok(b'{"type":"FeatureCollection","features":[]}', "application/json")
        else:
            self.send_error(404)


if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", porta), Stub).serve_forever()
