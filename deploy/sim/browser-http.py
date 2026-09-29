#!/usr/bin/env python3
"""F5 — navegador real: viewer em HTTP puro num host NÃO local não envia credencial.

Sobe a imagem web (ghcr.io/digidem/rastro-web:<tag>) com uma API falsa, publica em
127.0.0.1 e abre no Chrome headless por um nome não local (rastro.test, via
--host-resolver-rules). Injeta um cookie de sessão NÃO-Secure para /api (pior caso) e
confere pelo CDP que nenhuma requisição leva Authorization nem Cookie, e que a tela
mostra o aviso de HTTPS no lugar do campo de token. Controle: por http://localhost o
campo de token aparece.
Uso: deploy/sim/browser-http.py [tag]   (precisa de docker e google-chrome)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket  # websocket-client

TAG = sys.argv[1] if len(sys.argv) > 1 else "simtest"
PORTA_WEB, PORTA_CDP = 18080, 19222
REDE = "rastro-browser-test"
AVISO = "Este endereço não usa HTTPS"
FALHAS = []


def ok(cond, nome, detalhe=""):
    print(("PASS " if cond else "FAIL ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


def sh(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


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

    def drena(self, segundos):
        fim = time.time() + segundos
        self.ws.settimeout(0.5)
        while time.time() < fim:
            try:
                self.eventos.append(json.loads(self.ws.recv()))
            except websocket.WebSocketTimeoutException:
                pass
        self.ws.settimeout(10)


def visita(cdp, url):
    cdp.eventos.clear()
    cdp.call("Page.navigate", url=url)
    cdp.drena(5)
    texto = cdp.call("Runtime.evaluate", expression="document.body.innerText")["result"]["value"]
    campo = cdp.call("Runtime.evaluate",
                     expression="!!document.querySelector('input[type=password]')")["result"]["value"]
    api = []
    for e in cdp.eventos:
        if e.get("method") == "Network.requestWillBeSentExtraInfo":
            hdr = {k.lower(): v for k, v in e["params"].get("headers", {}).items()}
            api.append(hdr)
        if e.get("method") == "Network.requestWillBeSent":
            req = e["params"]["request"]
            if "/api/" in req["url"]:
                api.append({k.lower(): v for k, v in req.get("headers", {}).items()} | {"_url": req["url"]})
    return texto, campo, api


def main():
    tmp = tempfile.mkdtemp()
    chrome = None
    try:
        sh("docker", "network", "create", REDE, check=False)
        stub = ('while true; do printf \'HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n'
                'Content-Length: 55\\r\\n\\r\\n{"exigida":true,"autenticado":false,"dias_lembrar":30}\\n\''
                ' | nc -l -p 8080 >/dev/null 2>&1; done')
        sh("docker", "run", "-d", "--rm", "--name", "bt-api", "--network", REDE,
           "--network-alias", "api-falsa", "busybox:1.36", "sh", "-c", stub)
        sh("docker", "run", "-d", "--rm", "--name", "bt-web", "--network", REDE,
           "-p", f"127.0.0.1:{PORTA_WEB}:80", "-e", "RASTRO_API_UPSTREAM=api-falsa:8080",
           f"ghcr.io/digidem/rastro-web:{TAG}")
        time.sleep(2)
        chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", f"--remote-debugging-port={PORTA_CDP}",
             f"--user-data-dir={tmp}", "--no-first-run", "--disable-extensions",
             f"--remote-allow-origins=http://127.0.0.1:{PORTA_CDP}",
             f"--host-resolver-rules=MAP rastro.test 127.0.0.1", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                alvos = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORTA_CDP}/json"))
                pagina = next(a for a in alvos if a["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        cdp = Cdp(pagina["webSocketDebuggerUrl"])  # origin padrão do websocket-client = http://127.0.0.1:PORTA
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        # pior caso: um cookie de sessão NÃO-Secure já existe para rastro.test/api
        cdp.call("Network.setCookie", name="rastro_sessao", value="cookie-de-teste",
                 domain="rastro.test", path="/api", secure=False, httpOnly=True)

        texto, campo, api = visita(cdp, f"http://rastro.test:{PORTA_WEB}/")
        ok(AVISO in texto, "http://rastro.test (não local): aviso de HTTPS na tela")
        ok(not campo, "http://rastro.test: sem campo de token")
        vazou = [h.get("_url", "?") for h in api if "authorization" in h or "cookie" in h]
        ok(len(api) > 0, "http://rastro.test: o viewer chamou a API (teste significativo)", str(len(api)))
        ok(not vazou, "http://rastro.test: nenhuma requisição levou Authorization/Cookie", str(vazou))

        texto, campo, _ = visita(cdp, f"http://localhost:{PORTA_WEB}/")
        ok(campo and AVISO not in texto, "controle http://localhost: campo de token aparece")
    finally:
        if chrome:
            chrome.terminate()
        sh("docker", "rm", "-f", "bt-web", "bt-api", check=False)
        sh("docker", "network", "rm", REDE, check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"--- {len(FALHAS)} falha(s)")
    return 1 if FALHAS else 0


if __name__ == "__main__":
    sys.exit(main())
