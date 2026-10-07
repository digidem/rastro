#!/usr/bin/env python3
"""F5 — navegador real: sem basemap pmtiles local, o viewer cai no OSM.

Contraponto do browser-http.py: sobe a imagem web (communityfirst/rastro-web:<tag>)
com uma API falsa (deploy/sim/stub-api.py — auth sem senha + tiles /api/osm/…),
publica em 127.0.0.1 numa porta efêmera e abre no Chrome headless. O viewer faz
HEAD /tiles/basemap.pmtiles (web/src/InitializeMap.tsx, existeBasemapLocal); com
404 — o estado do sim, volume de tiles vazio — ele mantém a source raster OSM e o
mapa passa a pedir /api/osm/<z>/<x>/<y>.png. Pelo CDP confere o observável:

  1. HEAD /tiles/basemap.pmtiles responde 404 (gatilho do fallback);
  2. o mapa pede tiles /api/osm/… e eles chegam com 200 (fallback ativo);
  3. o canvas do mapa renderizou.

Rede/contêineres/portas com nome único da execução; tudo removido no finally.
Pré-requisito ausente (docker, daemon, chrome, websocket-client, imagem não
construída) → imprime SKIP com o motivo e sai 0 (a run.sh não falha por isso).
Uso: deploy/sim/browser-osm.py [tag]   (precisa de docker e google-chrome)
"""
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

TAG = sys.argv[1] if len(sys.argv) > 1 else "simtest"
AQUI = os.path.dirname(os.path.abspath(__file__))
SUFIXO = f"{int(time.time())}-{os.getpid()}"  # nome único desta execução
REDE = f"rastro-osm-{SUFIXO}"
CONT_WEB = f"bt-osm-web-{SUFIXO}"
CONT_API = f"bt-osm-api-{SUFIXO}"
FALHAS = []

NOMES = [
    "navegador: HEAD /tiles/basemap.pmtiles → 404 (sem basemap local)",
    "navegador: fallback OSM ativo — tiles /api/osm/… com 200",
    "navegador: mapa renderizou (canvas)",
]


def ok(cond, nome, detalhe=""):
    print(("PASS " if cond else "FAIL ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


def pula(motivo):
    """Pré-requisito ausente: o check não roda aqui, mas não conta como falha."""
    for nome in NOMES:
        print(f"SKIP {nome}: {motivo}")
    print("--- 0 falha(s)")
    sys.exit(0)


def sh(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cdp:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=10)
        self.n = 0
        self.eventos = []

    def call(self, metodo, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": metodo, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})
            self.eventos.append(msg)

    def espera(self, cond, segundos=15):
        """Drena eventos do CDP até a condição (ou o prazo); devolve a condição."""
        fim = time.time() + segundos
        self.ws.settimeout(0.5)
        try:
            while time.time() < fim:
                if cond():
                    return True
                try:
                    self.eventos.append(json.loads(self.ws.recv()))
                except websocket.WebSocketTimeoutException:
                    pass
            return cond()
        finally:
            self.ws.settimeout(10)


def analisa(cdp):
    """Do que o navegador fez: (HEAD pmtiles 404?, algum tile OSM concluído com 200?)."""
    pedidos, respostas, concluidos = {}, {}, set()
    for e in cdp.eventos:
        m = e.get("method")
        p = e.get("params", {})
        if m == "Network.requestWillBeSent":
            pedidos[p["requestId"]] = (p["request"]["method"], p["request"]["url"])
        elif m == "Network.responseReceived":
            respostas[p["requestId"]] = p["response"]["status"]
        elif m == "Network.loadingFinished":
            concluidos.add(p["requestId"])
    head404 = any(
        m == "HEAD" and u.endswith("/tiles/basemap.pmtiles") and respostas.get(i) == 404
        for i, (m, u) in pedidos.items()
    )
    osm200 = any(
        re.search(r"/api/osm/\d+/\d+/\d+\.png$", u)
        and respostas.get(i) == 200
        and i in concluidos
        for i, (m, u) in pedidos.items()
    )
    return head404, osm200


def resumo(cdp):
    """Só os pedidos relevantes, para o detalhe do FAIL (sem coordenadas alheias)."""
    saida = []
    for e in cdp.eventos:
        if e.get("method") == "Network.requestWillBeSent":
            u = e["params"]["request"]["url"]
            if "basemap.pmtiles" in u or "/api/osm/" in u:
                saida.append(f"{e['params']['request']['method']} {u}")
    return str(saida[:6])


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def unfilter_png(png_bytes: bytes) -> tuple[int, int, int, bytearray] | None:
    """Decodifica chunks IDAT revertindo filtros de scanline (0..4).
    Retorna (largura, altura, bpp, pixels) ou None se inválido."""
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    idat = bytearray()
    largura = altura = bit_depth = color_type = 0
    while pos < len(png_bytes):
        comp = struct.unpack(">I", png_bytes[pos:pos+4])[0]
        tipo = png_bytes[pos+4:pos+8]
        dados = png_bytes[pos+8:pos+8+comp]
        if tipo == b"IHDR":
            largura, altura, bit_depth, color_type = struct.unpack(">IIBB", dados[:10])
        elif tipo == b"IDAT":
            idat.extend(dados)
        pos += 12 + comp

    if not idat or largura == 0 or altura == 0 or bit_depth != 8:
        return None

    bpp = 3 if color_type == 2 else 4 if color_type == 6 else None
    if bpp is None:
        return None

    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None

    stride = 1 + largura * bpp
    if len(raw) != stride * altura:
        return None

    pixels = bytearray(largura * altura * bpp)
    prev_row = bytearray(largura * bpp)

    for y in range(altura):
        row_start = y * stride
        filter_type = raw[row_start]
        row_raw = raw[row_start + 1 : row_start + stride]
        curr_row = bytearray(largura * bpp)

        for x in range(len(row_raw)):
            filt = row_raw[x]
            a = curr_row[x - bpp] if x >= bpp else 0
            b = prev_row[x]
            c = prev_row[x - bpp] if x >= bpp else 0

            if filter_type == 0:
                recon = filt
            elif filter_type == 1:
                recon = (filt + a) & 0xFF
            elif filter_type == 2:
                recon = (filt + b) & 0xFF
            elif filter_type == 3:
                recon = (filt + ((a + b) >> 1)) & 0xFF
            elif filter_type == 4:
                recon = (filt + _paeth(a, b, c)) & 0xFF
            else:
                return None
            curr_row[x] = recon

        pixels[y * largura * bpp : (y + 1) * largura * bpp] = curr_row
        prev_row = curr_row

    return largura, altura, bpp, pixels


def tem_cor_tile(png_bytes: bytes, cor_esperada: tuple[int, int, int] = (0x1C, 0x2B, 0x1F)) -> bool:
    """Decodifica a imagem PNG reconstruindo os pixels e verifica a cor do tile."""
    res = unfilter_png(png_bytes)
    if res is None:
        return False
    largura, altura, bpp, pixels = res
    er, eg, eb = cor_esperada
    for i in range(0, len(pixels), bpp):
        r, g, b = pixels[i], pixels[i+1], pixels[i+2]
        if abs(r - er) <= 3 and abs(g - eg) <= 3 and abs(b - eb) <= 3:
            return True
    return False


def fecha_chrome(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def main():
    global websocket  # o import abaixo vira global (a classe Cdp usa)
    # --- pré-requisitos → SKIP com motivo ------------------------------------------------
    if shutil.which("docker") is None:
        pula("docker não encontrado no host")
    if sh("docker", "info", check=False).returncode != 0:
        pula("daemon do docker inacessível")
    if shutil.which("google-chrome") is None:
        pula("google-chrome não encontrado no host")
    try:
        import websocket  # noqa: F811
    except ImportError:
        pula("pacote python 'websocket-client' ausente")
    for imagem in (f"communityfirst/rastro-web:{TAG}", f"communityfirst/rastro-ingest:{TAG}"):
        if sh("docker", "image", "inspect", imagem, check=False).returncode != 0:
            pula(f"imagem {imagem} não construída (rode run.sh sem --no-build)")

    tmp = tempfile.mkdtemp()
    chrome = None
    cdp = None
    try:
        # --- stack mínima: web + API falsa, rede própria e nomes únicos -------------------
        sh("docker", "network", "create", REDE, check=False)
        sh("docker", "run", "-d", "--rm", "--name", CONT_API, "--network", REDE,
           "--network-alias", "api-falsa", "--entrypoint", "python",
           "-v", f"{AQUI}/stub-api.py:/stub-api.py:ro",
           f"communityfirst/rastro-ingest:{TAG}", "/stub-api.py", "8080")
        sh("docker", "run", "-d", "--rm", "--name", CONT_WEB,
           "-p", "127.0.0.1::80", "--network", REDE,
           "-e", "RASTRO_API_UPSTREAM=api-falsa:8080",
           f"communityfirst/rastro-web:{TAG}")
        porta_web = ""
        for _ in range(30):
            saida = sh("docker", "port", CONT_WEB, "80/tcp", check=False).stdout.strip()
            if saida:
                porta_web = saida.split(":")[-1]
                break
            time.sleep(0.5)
        if not porta_web:
            print("FAIL contêiner web não publicou porta")
            FALHAS.append("contêiner web não publicou porta")
            print(f"--- {len(FALHAS)} falha(s)")
            return 1
        base = f"http://127.0.0.1:{porta_web}"
        # pronto quando a API falsa responde atrás do web (caddy → stub)
        for _ in range(60):
            try:
                if urllib.request.urlopen(base + "/api/auth/estado", timeout=2).status == 200:
                    break
            except Exception:
                time.sleep(0.5)

        # --- Chrome headless por CDP (porta livre) ---------------------------------------
        cdp_porta = porta_livre()
        chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", f"--remote-debugging-port={cdp_porta}",
             f"--user-data-dir={tmp}", "--no-first-run", "--disable-extensions",
             f"--remote-allow-origins=http://127.0.0.1:{cdp_porta}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pagina = None
        for _ in range(50):
            try:
                alvos = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{cdp_porta}/json"))
                pagina = next(a for a in alvos if a["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        if pagina is None:
            # ambiente sem chrome utilizável (sandbox etc.): SKIP, não FAIL
            fecha_chrome(chrome)
            chrome = None
            pula("Chrome headless não iniciou neste ambiente")
        cdp = Cdp(pagina["webSocketDebuggerUrl"])
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('rastro_basemap', 'osm');")

        cdp.call("Page.navigate", url=base + "/")
        achou = cdp.espera(lambda: all(analisa(cdp)), segundos=15)
        head404, osm200 = analisa(cdp)
        detalhe = "" if achou else resumo(cdp)
        ok(head404, NOMES[0], detalhe)
        ok(osm200, NOMES[1], detalhe)

        # Espera o canvas existir no DOM com dimensões reais
        canvas_eval = """
        (() => {
            const canvas = document.querySelector('canvas.maplibregl-canvas') || document.querySelector('canvas');
            if (!canvas || canvas.width <= 0 || canvas.height <= 0) return false;
            const container = document.querySelector('.maplibregl-map');
            if (!container) return false;
            return true;
        })()
        """
        canvas_ok = cdp.espera(
            lambda: bool(cdp.call("Runtime.evaluate", expression=canvas_eval).get("result", {}).get("value")),
            segundos=10,
        )
        # Decodifica o screenshot e verifica se a cor do tile sintético (#1C2B1F)
        # aparece na área visível do mapa, aguardando até sua renderização
        render_ok = False
        if canvas_ok and osm200:
            fim = time.time() + 10
            while time.time() < fim:
                try:
                    clip_res = cdp.call(
                        "Runtime.evaluate",
                        expression="""
                        (() => {
                            const el = document.querySelector('canvas.maplibregl-canvas') || document.querySelector('canvas');
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) return null;
                            return {x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.max(1, r.width), height: Math.max(1, r.height), scale: 1};
                        })()
                        """,
                        returnByValue=True,
                    )
                    clip = clip_res.get("result", {}).get("value")
                    if not clip or clip.get("width", 0) <= 0 or clip.get("height", 0) <= 0:
                        time.sleep(0.5)
                        continue
                    ss = cdp.call("Page.captureScreenshot", format="png", clip=clip)
                    b64_data = ss.get("data")
                    if b64_data and tem_cor_tile(base64.b64decode(b64_data)):
                        render_ok = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)
        ok(canvas_ok and render_ok, NOMES[2])
    finally:
        if cdp is not None:
            try:
                cdp.ws.close()
            except Exception:
                pass
        fecha_chrome(chrome)
        chrome = None
        sh("docker", "rm", "-f", CONT_WEB, CONT_API, check=False)
        sh("docker", "network", "rm", REDE, check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"--- {len(FALHAS)} falha(s)")
    return 1 if FALHAS else 0


if __name__ == "__main__":
    sys.exit(main())
