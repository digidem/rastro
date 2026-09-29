"""Sonda e2e do simulador CapRover (roda DENTRO da rede interna do sim).

Publica um registro sintético como `gateway` (TLS, pelo nome srv-captain--sim-broker),
confere a linha no PostgreSQL (papel viewer), confere /api/nodes/latest pelo contêiner
web com token + X-Forwarded-Proto https, roda os negativos e limpa (papel maint).
Coordenada sintética no oceano; nada de dado real. Saída: linhas PASS/FAIL; exit 1 se
algum FAIL.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import paho.mqtt.client as mqtt
import psycopg

from rastro_gateway.common.records import PositionRecord

E = os.environ
NODE_NUM = 0xAAAA0F5E
NODE_ID = "!aaaa0f5e"
FALHAS: list[str] = []


def ok(cond: bool, nome: str, detalhe: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


def pg(user: str, pw: str):
    return psycopg.connect(host=E["SIM_PG_HOST"], dbname=E["SIM_DB"], user=user,
                           password=pw, connect_timeout=5, autocommit=True)


def mqtt_client(user: str, pw: str, host: str, ca: str | None) -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"probe-{user}-{time.time_ns()}")
    c.username_pw_set(user, pw)
    c.tls_set(ca_certs=ca)
    return c


def conecta(user: str, pw: str, host: str, ca: str | None) -> str:
    """Resultado REAL da conexão: espera o CONNACK (senha errada só aparece nele)."""
    c = mqtt_client(user, pw, host, ca)
    resultado: dict = {}
    c.on_connect = lambda cl, ud, flags, rc, props=None: resultado.setdefault("rc", rc)
    try:
        c.connect(host, 8883, 10)
    except ssl.SSLError as exc:
        return f"tls:{exc.reason}"
    except (OSError, ValueError) as exc:
        return f"erro:{type(exc).__name__}"
    c.loop_start()
    for _ in range(50):
        if "rc" in resultado:
            break
        time.sleep(0.1)
    c.loop_stop()
    c.disconnect()
    rc = resultado.get("rc")
    if rc is None:
        return "sem-connack"
    return "ok" if not rc.is_failure else f"recusado:{rc}"


def http(path: str, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(E["SIM_WEB_URL"] + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    ca = E["SIM_CA"]
    broker = E["SIM_BROKER"]
    import socket
    broker_ip = socket.gethostbyname(broker)

    # --- publicação real como gateway -------------------------------------------------
    rec = PositionRecord(node_num=NODE_NUM, node_id=NODE_ID, time=int(time.time()),
                         time_source="device", lat_i=101234000, lon_i=-301234000,
                         rx_time=int(time.time()))
    c = mqtt_client("gateway", E["SIM_PW_GATEWAY"], broker, ca)
    c.connect(broker, 8883, 10)
    c.loop_start()
    info = c.publish(f"{E['SIM_PREFIX']}/positions/{NODE_ID[1:]}", rec.to_mqtt_payload(), qos=1)
    info.wait_for_publish(10)
    ok(info.is_published(), "gateway publica QoS1 via TLS no nome interno")
    c.loop_stop(); c.disconnect()

    # --- linha no banco ----------------------------------------------------------------
    linhas = 0
    for _ in range(30):
        with pg(E["SIM_DB"] + "_viewer", E["SIM_PW_VIEWER"]) as con:
            linhas = con.execute("SELECT count(*) FROM positions WHERE node_num = %s",
                                 (NODE_NUM,)).fetchone()[0]
        if linhas:
            break
        time.sleep(1)
    ok(linhas == 1, "ingest gravou a posição no schema dedicado", f"linhas={linhas}")

    # --- API pelo web ------------------------------------------------------------------
    tok = {"Authorization": "Bearer " + E["SIM_API_TOKEN"]}
    st, corpo = http("/api/nodes/latest", {**tok, "X-Forwarded-Proto": "https"})
    ok(st == 200 and NODE_ID in corpo, "GET /api/nodes/latest com token e https", f"status={st}")
    st, _ = http("/api/nodes/latest", {"X-Forwarded-Proto": "https"})
    ok(st == 401, "sem token (https) → 401", f"status={st}")
    st, _ = http("/api/nodes/latest", tok)
    ok(st == 403, "sem X-Forwarded-Proto → 403 (antes do token)", f"status={st}")
    st, _ = http("/api/nodes/latest", {**tok, "X-Forwarded-Proto": "http"})
    ok(st == 403, "X-Forwarded-Proto http → 403", f"status={st}")
    st, _ = http("/api/healthz", {})
    ok(st == 200, "/api/healthz sem token nem https → 200", f"status={st}")
    st, corpo = http("/config.json", {})
    ok(st == 200 and json.loads(corpo).keys() == {"title"}, "/config.json só com o título")
    # CA pública do broker servida pelo web (o gateway a baixa daqui); igual à do volume pki
    st, corpo = http("/ca.crt", {})
    ok(st == 200 and corpo == open(E["SIM_CA"], encoding="utf-8").read()
       and "PRIVATE KEY" not in corpo, "/ca.crt serve a CA pública do broker", f"status={st}")
    # API direta pelo nome interno, sem token
    req = urllib.request.Request(f"http://srv-captain--{E['SIM_APP']}-api:8080/api/nodes/latest",
                                 headers={"X-Forwarded-Proto": "https"})
    try:
        urllib.request.urlopen(req, timeout=10); st = 200
    except urllib.error.HTTPError as exc:
        st = exc.code
    ok(st == 401, "API direta (nome interno) sem token → 401", f"status={st}")

    # --- caminho HTTPS real (frente TLS como o nginx do CapRover) -------------------------
    ctx = ssl.create_default_context(cafile=E["SIM_FRONT_CA"])
    jar = CookieJar()
    abre = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                       urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(E["SIM_FRONT_URL"] + "/api/auth/sessao", method="POST",
                                 data=b'{"lembrar": false}',
                                 headers={**tok, "Content-Type": "application/json"})
    try:
        r = abre.open(req, timeout=10); st, cookie = r.status, r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        st, cookie = exc.code, ""
    ok(st == 204 and "secure" in cookie.lower(), "HTTPS pela frente: sessão criada com cookie Secure",
       f"status={st}")
    try:
        r = abre.open(E["SIM_FRONT_URL"] + "/api/nodes/latest", timeout=10); st = r.status
    except urllib.error.HTTPError as exc:
        st = exc.code
    ok(st == 200, "HTTPS pela frente: só com o cookie (sem Bearer) → 200 (X-Forwarded-Proto passa)",
       f"status={st}")

    # --- negativos do broker ------------------------------------------------------------
    ok(conecta("gateway", E["SIM_PW_GATEWAY"], broker, ca) == "ok", "controle: conexão válida")
    ok(conecta("gateway", "senha-errada-xxxxxxxxxxxxxxxx", broker, ca) != "ok", "senha errada recusada")
    ok(conecta("gateway", E["SIM_PW_GATEWAY"], broker_ip, ca) != "ok",
       "conexão por IP (fora do SAN) recusada")
    ok(conecta("gateway", E["SIM_PW_GATEWAY"], broker, None) != "ok",
       "sem a CA própria (store do sistema) recusada")
    try:
        s = socket.create_connection((broker, 1883), timeout=3); s.close(); aberto = True
    except OSError:
        aberto = False
    ok(not aberto, "porta 1883 fechada")

    # --- privilégios -------------------------------------------------------------------
    def negado(user: str, pw: str, sql: str) -> bool:
        try:
            with pg(user, pw) as con:
                con.execute(sql)
            return False
        except psycopg.errors.InsufficientPrivilege:
            return True
    db = E["SIM_DB"]
    ok(negado(db + "_viewer", E["SIM_PW_VIEWER"], "INSERT INTO nodes (node_num, node_id) VALUES (1, '!00000001')"),
       "viewer não insere")
    ok(negado(db + "_ingest", E["SIM_PW_INGEST"], "DELETE FROM positions"), "ingest não apaga")
    ok(negado(db + "_ingest", E["SIM_PW_INGEST"], "SELECT lat_i FROM positions"), "ingest não lê coordenadas")
    with pg(db + "_viewer", E["SIM_PW_VIEWER"]) as con:
        donos = con.execute("SELECT schemaname, tableowner FROM pg_tables WHERE tablename IN "
                            "('nodes','positions','device_telemetry')").fetchall()
    ok(len(donos) == 3 and all(s == db and o == db + "_owner" for s, o in donos),
       "tabelas no schema dedicado, dono <db>_owner", str(donos))

    # --- limpeza (maint) --------------------------------------------------------------
    with pg(db + "_maint", E["SIM_PW_MAINT"]) as con:
        con.execute("DELETE FROM positions WHERE node_num = %s", (NODE_NUM,))
        con.execute("DELETE FROM device_telemetry WHERE node_num = %s", (NODE_NUM,))
        con.execute("DELETE FROM nodes WHERE node_num = %s", (NODE_NUM,))
        resto = con.execute("SELECT count(*) FROM nodes WHERE node_num = %s", (NODE_NUM,)).fetchone()[0]
    ok(resto == 0, "limpeza do registro sintético (maint)")

    print(f"--- {len(FALHAS)} falha(s)")
    return 1 if FALHAS else 0


NODE_FILA = 0xAAAA0F5F
ID_FILA = "!aaaa0f5f"


def publica_fila(n: int) -> int:
    """Publica n posições QoS1 como gateway (usado com o ingest PARADO)."""
    ca, broker = E["SIM_CA"], E["SIM_BROKER"]
    c = mqtt_client("gateway", E["SIM_PW_GATEWAY"], broker, ca)
    c.connect(broker, 8883, 10)
    c.loop_start()
    base = int(time.time()) - 3600
    for i in range(n):
        rec = PositionRecord(node_num=NODE_FILA, node_id=ID_FILA, time=base + i * 60,
                             time_source="device", lat_i=101234000 + i, lon_i=-301234000,
                             rx_time=base + i * 60)
        info = c.publish(f"{E['SIM_PREFIX']}/positions/{ID_FILA[1:]}", rec.to_mqtt_payload(), qos=1)
        info.wait_for_publish(10)
        ok(info.is_published(), f"fila: publicação {i + 1}/{n} aceita pelo broker")
    c.loop_stop(); c.disconnect()
    return 1 if FALHAS else 0


def confere_fila(n: int) -> int:
    """Espera as n posições chegarem ao banco (fila QoS1 sobreviveu) e limpa."""
    db = E["SIM_DB"]
    linhas = 0
    for _ in range(60):
        with pg(db + "_viewer", E["SIM_PW_VIEWER"]) as con:
            linhas = con.execute("SELECT count(*) FROM positions WHERE node_num = %s",
                                 (NODE_FILA,)).fetchone()[0]
        if linhas >= n:
            break
        time.sleep(1)
    ok(linhas == n, f"fila QoS1 sobreviveu ao restart do broker ({n} mensagens)", f"linhas={linhas}")
    with pg(db + "_maint", E["SIM_PW_MAINT"]) as con:
        con.execute("DELETE FROM positions WHERE node_num = %s", (NODE_FILA,))
        con.execute("DELETE FROM nodes WHERE node_num = %s", (NODE_FILA,))
    return 1 if FALHAS else 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "sonda"
    if modo == "publica":
        sys.exit(publica_fila(int(sys.argv[2])))
    if modo == "conta":
        sys.exit(confere_fila(int(sys.argv[2])))
    sys.exit(main())
