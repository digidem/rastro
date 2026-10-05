#!/usr/bin/env python3
"""Rig ponta a ponta do ingest nativo (WP-G).

Sobe, localmente em Docker, a cadeia completa do ingest nativo: broker
Mosquitto (imagem nativetest, TLS + contas + ACL) + PostgreSQL descartável +
os processos REAIS ``ingest`` and ``outbox`` do venv com uma PSK de teste
aleatória de runtime. Clientes nó falsos (paho MQTT v5, TLS com a CA) publicam
ServiceEnvelopes Meshtastic cifrados com a PSK de teste.

Verificações (linhas PASS/FAIL, exit != 0 em falha):
 1. posição do nó a1 via gateway !f0000001 grava positions com packet_id/gateway_num
 2. mesmo (from,id) via gateway G2 é deduplicado (raw duplicate=true, 1 linha em positions)
 3. replay de pacote mais velho não troca vw_ultima_posicao
 4. horário inválido (0) grava sinalizado (time_flag='invalid_zero')
 5. texto 'ajuda' grava chat_messages com is_alert=true
 6. restart do ingest no mid-stream: mensagens publicadas enquanto esteve fora
    são entregues (sessão persistente) sem perda e sem duplicação
 7. ingest nunca publica: PUBACK 135 na conta dele + monitor (read univaja/#)
    não vê nada fora do ledger do que clientes/outbox publicaram
 8. outbox publica SÓ para o barco alvo, texto certo ao decifrar, status 'sent'
 9. outbox row com expires_at no passado vira 'expired' sem publicar
10. logs do ingest/outbox sem PSK (base64/hex) e sem texto de mensagem

NUNCA loga payload, chave/PSK ou texto de mensagem. Coordenadas sintéticas.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from typing import Any, Callable, TypeVar

import psycopg
from psycopg import sql

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parents[1]
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import native_rig_test  # noqa: E402  (mesmo diretório: reutiliza MqttTestClient)

from meshtastic.protobuf import mesh_pb2, mqtt_pb2  # noqa: E402
from rastro_gateway.native import crypto  # noqa: E402

# --- Constantes do cenário ---------------------------------------------------

ROOT = "univaja/mesh"
CANAL = "EVU"
PORTNUM_TEXTO = 1
PORTNUM_POSICAO = 3
BROADCAST = 0xFFFFFFFF
DB = "rastro"
ESPERA_BD = 30.0
GRACA_SUBSCRIBE = 2.0  # pausa pós-SUBACK para o broker consolidar a assinatura

# 6 barcos b1..b6; nó do barco bi = !a000000i (a1..a6); vgw do barco = !f000000i
BARCOS = [f"b{i}" for i in range(1, 7)]
NOS = {b: f"!a{i:07d}" for i, b in enumerate(BARCOS, start=1)}
VGWS = {b: f"!f{i:07d}" for i, b in enumerate(BARCOS, start=1)}
NUM_A = {b: int(NOS[b][1:], 16) for b in BARCOS}
NUM_VGW = {b: int(VGWS[b][1:], 16) for b in BARCOS}

# Coordenadas sintéticas delimitadas (não são de campo): lat -22.5°±, lon -40.1°±
LAT = {b: -(22_500_000 + i * 10_000) for i, b in enumerate(BARCOS, start=1)}
LON = {b: -(40_100_000 + i * 10_000) for i, b in enumerate(BARCOS, start=1)}

# Ledger do que clientes de teste e outbox publicaram: conjunto de (topic, payload)
LEDGER: set[tuple[str, bytes]] = set()

T = TypeVar("T")


# --- Utilitários ---------------------------------------------------------------


class Verificacoes:
    """Coletor de checagens PASS/FAIL com motivo de falha."""

    def __init__(self) -> None:
        self.falhas: list[str] = []

    def passou(self, nome: str, condicao: bool, detalhe: str = "") -> bool:
        if condicao:
            msg = f"PASS: {nome}"
            if detalhe:
                msg += f" ({detalhe})"
            print(msg, flush=True)
        else:
            msg = f"FAIL: {nome}"
            if detalhe:
                msg += f" ({detalhe})"
            print(msg, flush=True)
            self.falhas.append(f"{nome} ({detalhe})" if detalhe else nome)
        return condicao

    def ok(self) -> bool:
        return not self.falhas


def _resumo(exc: BaseException) -> str:
    """Resumo de exceção para falhas (tipo + mensagem, sem material de payload)."""
    return f"{type(exc).__name__}: {exc}"


def esperar(
    desc: str,
    fn: Callable[[], T],
    timeout: float = ESPERA_BD,
    intervalo: float = 0.5,
) -> T:
    """Consulta fn() até retornar truthy ou estourar o timeout (AssertionError)."""
    prazo = time.monotonic() + timeout
    while time.monotonic() < prazo:
        v = fn()
        if v:
            return v
        time.sleep(intervalo)
    raise AssertionError(f"timeout: {desc}")


def agora() -> int:
    return int(time.time())


class Processo:
    """Processo real do venv com log em arquivo; SIGTERM com escalonamento p/ kill."""

    def __init__(
        self,
        nome: str,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        log_path: Path,
    ) -> None:
        self.nome = nome
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.log_path = log_path
        self._proc: subprocess.Popen[bytes] | None = None

    def iniciar(self) -> None:
        """Abre o log em anexo e dispara o processo (stdout+stderr no arquivo)."""
        marcador = f"\n=== início {self.nome} {time.strftime('%Y-%m-%dT%H:%M:%S')} ===\n"
        with open(self.log_path, "ab") as f:
            f.write(marcador.encode("utf-8"))
        handle = open(self.log_path, "ab")
        self._proc = subprocess.Popen(
            self.argv,
            env=self.env,
            cwd=str(self.cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )

    def aguardar_log(self, fragmento: str, timeout: float = 60.0) -> bool:
        """Espera o fragmento aparecer no log (leitura desde o início do arquivo)."""
        prazo = time.monotonic() + timeout
        while time.monotonic() < prazo:
            try:
                dados = self.log_path.read_bytes()
            except OSError:
                dados = b""
            if fragmento.encode("utf-8") in dados:
                return True
            time.sleep(0.3)
        return False

    def parar(self, timeout: float = 30.0) -> None:
        """SIGTERM, espera sair; SIGKILL se persistir. Idempotente."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        handle = proc.stdout  # handle do log aberto no iniciar()
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


# --- Envelopes Meshtastic ------------------------------------------------------


def _data_cifrada(
    psk: bytes,
    packet_id: int,
    from_num: int,
    portnum: int,
    payload: bytes,
) -> bytes:
    """Data protobuf cifrada com AES-CTR (nonce id+from)."""
    data = mesh_pb2.Data(portnum=portnum, payload=payload)
    chave = crypto.expand_psk(psk)
    return crypto.crypt(chave, packet_id, from_num, data.SerializeToString())


def _pacote(
    psk: bytes,
    packet_id: int,
    from_num: int,
    portnum: int,
    payload: bytes,
    hop_limit: int = 3,
) -> mesh_pb2.MeshPacket:
    """MeshPacket com canal EVU, to=broadcast e encrypted do _data_cifrada."""
    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", from_num)
    mp.to = BROADCAST
    mp.id = packet_id
    mp.channel = crypto.channel_hash(CANAL, psk)
    mp.hop_limit = hop_limit
    mp.hop_start = hop_limit
    mp.rx_time = agora()  # gateway recebe agora (realista; alimenta observed_at)
    mp.encrypted = _data_cifrada(psk, packet_id, from_num, portnum, payload)
    return mp


def envelope_posicao(
    psk: bytes,
    packet_id: int,
    from_num: int,
    lat_i: int,
    lon_i: int,
    tempo: int | None = None,
) -> bytes:
    """ServiceEnvelope de POSITION (portnum 3), `tempo` = relógio do dispositivo."""
    pos = mesh_pb2.Position(
        latitude_i=lat_i,
        longitude_i=lon_i,
        altitude=50,
        sats_in_view=8,
        time=agora() if tempo is None else tempo,
    )
    mp = _pacote(psk, packet_id, from_num, PORTNUM_POSICAO, pos.SerializeToString())
    return mqtt_pb2.ServiceEnvelope(
        packet=mp,
        channel_id=CANAL,
        gateway_id=f"!{from_num:08x}",
    ).SerializeToString()


def envelope_texto(
    psk: bytes,
    packet_id: int,
    from_num: int,
    texto: str,
) -> bytes:
    """ServiceEnvelope de TEXT_MESSAGE (portnum 1) com o texto em UTF-8."""
    mp = _pacote(
        psk,
        packet_id,
        from_num,
        PORTNUM_TEXTO,
        texto.encode("utf-8"),
    )
    return mqtt_pb2.ServiceEnvelope(
        packet=mp,
        channel_id=CANAL,
        gateway_id=f"!{from_num:08x}",
    ).SerializeToString()


def decifrar(payload: bytes, psk: bytes) -> tuple[str, int, str]:
    """Decifra um ServiceEnvelope recebido; nunca levanta.

    Retorna (tipo, from_num, texto):
      ("texto", from_num, texto) — portnum 1
      ("nodeinfo", from_num, "") — portnum 4 (User.id = !hex do from_num)
      ("outro", 0, "")           — outro portnum
      ("ilegível", 0, "")        — bytes não decifráveis com esta PSK
    """
    try:
        env = mqtt_pb2.ServiceEnvelope.FromString(payload)
        pkt = env.packet
        chave = crypto.expand_psk(psk)
        claro = crypto.crypt(chave, pkt.id, getattr(pkt, "from"), pkt.encrypted)
        data = mesh_pb2.Data.FromString(claro)
        if data.portnum == PORTNUM_TEXTO:
            return ("texto", int(getattr(pkt, "from")), data.payload.decode("utf-8"))
        if data.portnum == 4:
            user = mesh_pb2.User.FromString(data.payload)
            return ("nodeinfo", int(user.id[1:], 16), "")
        return ("outro", 0, "")
    except Exception:
        return ("ilegível", 0, "")


# --- Banco de dados -------------------------------------------------------------


SQL_ROLES = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rastro_owner') THEN
        CREATE ROLE rastro_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rastro_ingest') THEN
        CREATE ROLE rastro_ingest LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rastro_viewer') THEN
        CREATE ROLE rastro_viewer LOGIN;
    END IF;
END
$$;
"""


def preparar_banco(
    pg_host: str,
    pg_port: int,
    admin_pw: str,
    ingest_pw: str,
    viewer_pw: str,
) -> None:
    """Cria banco rastro, aplica schema 01+02 e replica os GRANTs do bootstrap."""
    """Cria banco rastro, aplica schema 01+02 e replica os GRANTs do bootstrap."""
    admin = f"host={pg_host} port={pg_port} user=postgres password={admin_pw} dbname=postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{DB}"')
            # Papéis de aplicação (mesmos nomes do bootstrap-existing.sh)
            cur.execute(SQL_ROLES)
            cur.execute(sql.SQL("ALTER ROLE rastro_ingest WITH LOGIN PASSWORD {}").format(sql.Literal(ingest_pw)))
            cur.execute(sql.SQL("ALTER ROLE rastro_viewer WITH LOGIN PASSWORD {}").format(sql.Literal(viewer_pw)))
            cur.execute("GRANT rastro_owner TO postgres")
            cur.execute(f'GRANT CONNECT ON DATABASE "{DB}" TO rastro_ingest, rastro_viewer')
            cur.execute(f'ALTER DATABASE "{DB}" SET search_path = rastro, public')
    # Schema + migração nativa no banco recém-criado (mesmo padrão de test_native_db.py)
    alvo = f"host={pg_host} port={pg_port} user=postgres password={admin_pw} dbname={DB}"
    with psycopg.connect(alvo, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS rastro")
            cur.execute("SET search_path = rastro, public")
            cur.execute("SELECT to_regclass('rastro.nodes')")
            if not cur.fetchone()[0]:
                cur.execute((REPO / "deploy/postgres/init/01-schema.sql").read_text(encoding="utf-8"))
            cur.execute((REPO / "deploy/postgres/init/02-native.sql").read_text(encoding="utf-8"))
    # GRANTs mínimos — réplica das linhas do bootstrap-existing.sh (roles rastro_*)
    with psycopg.connect(alvo, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("GRANT USAGE ON SCHEMA rastro TO rastro_ingest, rastro_viewer")
            cur.execute("GRANT INSERT ON rastro.positions, rastro.device_telemetry TO rastro_ingest")
            cur.execute("GRANT SELECT (node_num, pos_time) ON rastro.positions TO rastro_ingest")
            cur.execute("GRANT SELECT (node_num, telem_time) ON rastro.device_telemetry TO rastro_ingest")
            cur.execute("GRANT INSERT ON rastro.nodes TO rastro_ingest")
            cur.execute("GRANT UPDATE (node_id, friendly_name, fleet_id, last_seen, updated_at) ON rastro.nodes TO rastro_ingest")
            cur.execute("GRANT SELECT (node_num, node_id, friendly_name, fleet_id) ON rastro.nodes TO rastro_ingest")
            cur.execute("GRANT INSERT, SELECT, UPDATE ON rastro.raw_envelopes, rastro.packet_seen, rastro.gateway_status, rastro.node_info, rastro.node_power, rastro.chat_messages, rastro.virtual_gateways, rastro.boat_devices, rastro.alert_state TO rastro_ingest")
            cur.execute("GRANT SELECT, UPDATE ON rastro.chat_outbox TO rastro_ingest")
            cur.execute("GRANT SELECT ON rastro.nodes, rastro.positions, rastro.device_telemetry, rastro.vw_ultima_posicao TO rastro_viewer")
            cur.execute("GRANT SELECT ON rastro.chat_messages, rastro.chat_outbox, rastro.alert_state, rastro.virtual_gateways, rastro.node_info, rastro.node_power, rastro.boat_devices TO rastro_viewer")
            cur.execute("GRANT INSERT (boat_id, text, created_by, expires_at) ON rastro.chat_outbox TO rastro_viewer")
            # Gateways virtuais da frota de teste (1 por barco, ativos)
            for i, barco in enumerate(BARCOS, start=1):
                cur.execute(
                    "INSERT INTO rastro.virtual_gateways (boat_id, gateway_id, virtual_node_num, active) VALUES (%s, %s, %s, true)",
                    (barco, VGWS[barco], NUM_VGW[barco]),
                )


# --- Consultas -------------------------------------------------------------------

def consultar(pg: dict, sql: str, params: tuple = ()) -> list[tuple]:
    """Consulta de verificação como postgres (admin) no banco rastro."""
    conn = f"host={pg['host']} port={pg['port']} user=postgres password={pg['admin_password']} dbname={DB}"
    with psycopg.connect(conn) as c:
        with c.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(sql, params)
            return cur.fetchall()


def inserir_outbox(pg: dict, barco: str, texto: str, expira_em: float) -> int:
    """INSERT em chat_outbox com a conta viewer (grant de colunas exatas) e retorna o id."""
    conn = f"host={pg['host']} port={pg['port']} user=rastro_viewer password={pg['viewer_password']} dbname={DB}"
    with psycopg.connect(conn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SET search_path = rastro, public")
            cur.execute(
                "INSERT INTO chat_outbox (boat_id, text, created_by, expires_at) VALUES (%s, %s, %s, %s) RETURNING id",
                (barco, texto, "rig-e2e", datetime.datetime.fromtimestamp(expira_em, tz=datetime.timezone.utc)),
            )
            return int(cur.fetchone()[0])


# --- Clientes MQTT -----------------------------------------------------------------

def publicar(cliente: native_rig_test.MqttTestClient, topico: str, payload: bytes) -> object:
    """Publica com QoS 1, registra no ledger e exige PUBACK de sucesso (rc < 128)."""
    LEDGER.add((topico, payload))
    rc = cliente.publish(topico, payload, qos=1, timeout=10.0)
    if getattr(rc, "is_failure", False):
        raise AssertionError(f"publicação recusada pelo broker em {topico}")
    return rc


def cliente_no(
    nome: str,
    usuario: str,
    senha: str,
    ca: str,
    host: str,
    port: int,
    topico_frota: str,
) -> native_rig_test.MqttTestClient:
    """Conecta um nó fake (MQTT v5, TLS) e assina o tópico de frota do seu barco."""
    cli = native_rig_test.MqttTestClient(
        client_id=f"e2e-node-{nome}",
        username=usuario,
        password=senha,
        ca_certs=ca,
        host=host,
        port=port,
    )
    rc = cli.connect(timeout=10.0)
    if getattr(rc, "is_failure", False):
        raise AssertionError(f"CONNACK de nó fake {nome} recusado")
    cli.subscribe(topico_frota, qos=1, timeout=10.0)
    time.sleep(GRACA_SUBSCRIBE)
    return cli


# --- Processos reais -----------------------------------------------------------------

def env_comum(psk_b64: str, pg: dict, ca: str, root: str) -> dict[str, str]:
    """Variáveis compartilhadas por ingest e outbox (sem segredo no log)."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "RASTRO_LOG_LEVEL": "INFO",
        "RASTRO_MQTT_HOST": pg["broker_host"],
        "RASTRO_MQTT_PORT": str(pg["broker_port"]),
        "RASTRO_MQTT_CA_CERT": ca,
        "RASTRO_MQTT_CA_WAIT_SECS": "30",
        "RASTRO_MQTT_CLIENT_ID": "",
        "RASTRO_NATIVE_ROOT": root,
        "RASTRO_EVU_PSK_B64": psk_b64,
        "RASTRO_PG_HOST": pg["host"],
        "RASTRO_PG_PORT": str(pg["port"]),
        "RASTRO_PG_DB": DB,
    }


def iniciar_ingest(
    venv_py: str,
    repo: Path,
    psk_b64: str,
    pg: dict,
    ca: str,
    root: str,
    log_dir: Path,
    rodada: int,
) -> Processo:
    """Sobe o processo REAL do ingest (RASTRO_NATIVE_ENABLED=1) com log em arquivo."""
    env = env_comum(psk_b64, pg, ca, root)
    env.update(
        {
            "RASTRO_MQTT_USERNAME": "ingest",
            "RASTRO_MQTT_PASSWORD": pg["ingest_mqtt_password"],
            "RASTRO_MQTT_CLIENT_ID": "rastro-e2e-ingest",
            "RASTRO_PG_PASSWORD": pg["ingest_db_password"],
        }
    )
    env["RASTRO_NATIVE_ENABLED"] = "1"
    proc = Processo(
        nome="ingest",
        argv=[venv_py, "-m", "rastro_gateway.ingest"],
        env=env,
        cwd=repo,
        log_path=log_dir / f"ingest-{rodada}.log",
    )
    proc.iniciar()
    if not proc.aguardar_log("Conectado ao broker MQTT", timeout=60.0):
        raise AssertionError("ingest não confirmou conexão MQTT no log")
    return proc


def iniciar_outbox(venv_py: str, repo: Path, psk_b64: str, pg: dict, ca: str, root: str, log_dir: Path) -> Processo:
    """Sobe o processo REAL do outbox (rastro_gateway.chat) com republish desligado."""
    env = env_comum(psk_b64, pg, ca, root)
    env.update(
        {
            "RASTRO_MQTT_USERNAME": "outbox",
            "RASTRO_MQTT_PASSWORD": pg["outbox_mqtt_password"],
            "RASTRO_MQTT_CLIENT_ID": "rastro-e2e-outbox",
            "RASTRO_PG_PASSWORD": pg["ingest_db_password"],
            "RASTRO_NODEINFO_REPUBLISH_SECS": "999999",
        }
    )
    proc = Processo(
        nome="outbox",
        argv=[venv_py, "-m", "rastro_gateway.chat"],
        env=env,
        cwd=repo,
        log_path=log_dir / "outbox.log",
    )
    proc.iniciar()
    if not proc.aguardar_log("Serviço Outbox em execução", timeout=60.0):
        if proc._proc is not None and proc._proc.poll() is not None:
            raise AssertionError("outbox morreu no boot (ver log)")
        raise AssertionError("outbox não registrou 'Serviço Outbox em execução'")
    return proc


# --- Principal -----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Rig E2E do ingest nativo (WP-G)")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=18883)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--accounts", required=True, help="JSON de contas do broker")
    parser.add_argument("--pg-host", default="127.0.0.1")
    parser.add_argument("--pg-port", type=int, required=True)
    parser.add_argument("--pg-admin-password", required=True)
    parser.add_argument("--pg-ingest-password", required=True)
    parser.add_argument("--pg-viewer-password", required=True)
    parser.add_argument("--log-dir", required=True)
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    accounts = json.load(open(args.accounts, encoding="utf-8"))

    # PSK de teste: aleatória de runtime, descartável (nunca logada)
    psk = secrets.token_bytes(32)
    psk_b64 = base64.b64encode(psk).decode("ascii")

    pg = {
        "host": args.pg_host,
        "port": args.pg_port,
        "admin_password": args.pg_admin_password,
        "ingest_db_password": args.pg_ingest_password,
        "viewer_password": args.pg_viewer_password,
        "broker_host": args.broker_host,
        "broker_port": args.broker_port,
        "ingest_mqtt_password": accounts["ingest"]["password"],
        "outbox_mqtt_password": accounts["outbox"]["password"],
    }
    preparar_banco(
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        admin_pw=args.pg_admin_password,
        ingest_pw=args.pg_ingest_password,
        viewer_pw=args.pg_viewer_password,
    )

    v = Verificacoes()
    senhas_nos = {}
    for no in accounts["nodes"]:
        senhas_nos[no["user"]] = no["password"]
    senhas_nos["monitor"] = accounts["monitor"]["password"]
    senhas_nos["ingest"] = accounts["ingest"]["password"]
    senhas_nos["outbox"] = accounts["outbox"]["password"]

    ca = args.ca
    broker_host = args.broker_host
    broker_port = args.broker_port
    topicos_frota = {b: f"{ROOT}/2/e/{CANAL}/{VGWS[b]}" for b in BARCOS}
    topicos_up = {b: f"{ROOT}/2/e/{CANAL}/{NOS[b]}" for b in BARCOS}
    clientes: dict[str, native_rig_test.MqttTestClient] = {}
    processos: list[Processo] = []
    monitor: native_rig_test.MqttTestClient | None = None
    venv_py = str(REPO / "services/rastro_gateway/.venv/bin/python")
    try:
        # Monitor: read univaja/# — testemunha tudo que trafega no broker
        monitor = native_rig_test.MqttTestClient(
            client_id="e2e-monitor",
            username="monitor",
            password=senhas_nos["monitor"],
            ca_certs=ca,
            host=broker_host,
            port=broker_port,
        )
        rc = monitor.connect(timeout=10.0)
        if getattr(rc, "is_failure", False):
            raise AssertionError("CONNACK do monitor recusado")
        monitor.subscribe(f"{ROOT}/#", qos=1, timeout=10.0)
        time.sleep(GRACA_SUBSCRIBE)

        # ORDEM: outbox primeiro (burst de NodeInfo acontece antes dos nós conectarem)
        proc_outbox = iniciar_outbox(venv_py, REPO, psk_b64, pg, ca, ROOT, log_dir)
        processos.append(proc_outbox)
        time.sleep(3.0)  # burst de NodeInfo vai a esmo: nós não estão conectados
        proc_ingest = iniciar_ingest(venv_py, REPO, psk_b64, pg, ca, ROOT, log_dir, rodada=1)
        processos.append(proc_ingest)
        time.sleep(GRACA_SUBSCRIBE)
        # Nós fake conectam DEPOIS do burst de NodeInfo (checagem 8 depende disso)
        for barco in BARCOS:
            clientes[barco] = cliente_no(
                nome=barco,
                usuario=NOS[barco],
                senha=senhas_nos[NOS[barco]],
                ca=ca,
                host=broker_host,
                port=broker_port,
                topico_frota=topicos_frota[barco],
            )

        # --- Checagem 1: posição do a1 via vgw1 grava packet_id/gateway_num
        env1 = b""
        try:
            p1 = secrets.randbits(32) | 1
            t1 = agora() - 60
            env1 = envelope_posicao(psk, p1, NUM_A["b1"], LAT["b1"], LON["b1"], tempo=t1)
            publicar(clientes["b1"], topicos_up["b1"], env1)

            def _c1():
                linhas = consultar(
                    pg,
                    "SELECT packet_id, gateway_num, time_source, time_flag, lat_i, lon_i, pos_time, observed_at FROM positions WHERE packet_id = %s",
                    (p1,),
                )
                return linhas[0] if len(linhas) == 1 else None

            r1 = esperar("posição p1 em positions", _c1)
            ok1 = (
                r1[0] == p1
                and r1[1] == NUM_A["b1"]
                and r1[2] == "device"
                and r1[3] is None
                and r1[4] == LAT["b1"]
                and r1[5] == LON["b1"]
                and abs(r1[6].timestamp() - t1) <= 5
                and r1[7] is not None
            )
            v.passou("1. posição via G1 grava packet_id/gateway_num", ok1, f"packet_id={p1}")
        except Exception as exc:
            v.passou("1. posição via G1 grava packet_id/gateway_num", False, _resumo(exc))

        # --- Checagem 2: mesmo (from,id) via G2 deduplicado
        try:
            if not env1:
                raise AssertionError("env1 indisponível (checagem 1 falhou cedo)")
            publicar(clientes["b2"], f"{ROOT}/2/e/{CANAL}/{NOS['b2']}", env1)
            time.sleep(6.0)  # lote nativo fecha por idade (5 s) + margem
            raw = consultar(pg, "SELECT gateway_num, duplicate FROM raw_envelopes WHERE packet_id = %s", (p1,))
            dups = [r for r in raw if r[1]]
            pos = consultar(pg, "SELECT COUNT(*) FROM positions WHERE packet_id = %s", (p1,))
            ok2 = (
                len(raw) == 2
                and len(dups) == 1
                and dups[0][0] == NUM_A["b2"]
                and pos[0][0] == 1
                and consultar(pg, "SELECT first_gateway FROM packet_seen WHERE packet_id = %s", (p1,))[0][0] == NUM_A["b1"]
            )
            v.passou("2. mesmo (from,id) via G2 é deduplicado", ok2, f"raw={len(raw)}")
        except Exception as exc:
            v.passou("2. mesmo (from,id) via G2 é deduplicado", False, _resumo(exc))

        # --- Checagem 3: replay mais velho não troca vw_ultima_posicao
        try:
            p2 = secrets.randbits(32) | 1
            p3 = secrets.randbits(32) | 1
            t2 = agora()
            env2novo = envelope_posicao(psk, p2, NUM_A["b1"], LAT["b1"], LON["b1"])
            env3velho = envelope_posicao(psk, p3, NUM_A["b1"], LAT["b1"], LON["b1"])
            publicar(clientes["b1"], topicos_up["b1"], env2novo)
            esperar("p2 em positions", lambda: consultar(pg, "SELECT 1 FROM positions WHERE packet_id = %s", (p2,)))
            publicar(clientes["b1"], topicos_up["b1"], env3velho)
            time.sleep(6.0)
            w2 = consultar(pg, "SELECT lat, lon, pos_time FROM vw_ultima_posicao WHERE node_num = %s", (NUM_A["b1"],))
            raw3 = consultar(pg, "SELECT COUNT(*) FROM raw_envelopes WHERE packet_id = %s", (p3,))
            raw3n = raw3[0][0]
            raw3dup = consultar(pg, "SELECT COUNT(*) FROM raw_envelopes WHERE packet_id = %s AND duplicate = true", (p3,))[0][0]
            ok3 = (
                len(w2) == 1
                and abs(w2[0][2].timestamp() - t2) <= 5
                and abs(w2[0][0] - LAT["b1"] / 1e7) < 1e-6
                and abs(w2[0][1] - LON["b1"] / 1e7) < 1e-6
                and raw3n == 1
                and raw3dup == 0
            )
            v.passou("3. replay antigo não troca vw_ultima_posicao", ok3, f"pos_time={w2[0][2]}")
        except Exception as exc:
            v.passou("3. replay antigo não troca vw_ultima_posicao", False, _resumo(exc))

        # --- Checagem 4: time=0 grava time_flag='invalid_zero'
        try:
            p4 = secrets.randbits(32) | 1
            env4 = envelope_posicao(psk, p4, NUM_A["b4"], LAT["b4"], LON["b4"], tempo=0)
            publicar(clientes["b4"], topicos_up["b4"], env4)
            def _c4():
                linhas = consultar(pg, "SELECT time_flag, time_source, pos_time, observed_at FROM positions WHERE packet_id = %s", (p4,))
                return linhas[0] if len(linhas) == 1 else None
            r4 = esperar("posição p4 com flag", _c4)
            ok4 = (
                r4[0] == "invalid_zero"
                and r4[1] == "gateway"
                and abs(r4[2].timestamp() - agora()) <= 120
                and r4[3] is not None
            )
            v.passou("4. time=0 grava time_flag='invalid_zero'", ok4, f"flag={r4[0]}")
        except Exception as exc:
            v.passou("4. time=0 grava time_flag='invalid_zero'", False, _resumo(exc))

        # --- Checagem 5: 'ajuda' vira is_alert=true em chat_messages
        try:
            p5t = secrets.randbits(32) | 1
            env5 = envelope_texto(psk, p5t, NUM_A["b5"], "ajuda")
            publicar(clientes["b5"], topicos_up["b5"], env5)
            def _c5():
                linhas = consultar(pg, "SELECT direction, is_alert FROM chat_messages WHERE packet_id = %s", (p5t,))
                return linhas[0] if len(linhas) == 1 else None
            r5 = esperar("chat 'ajuda' em chat_messages", _c5)
            ok5 = r5[0] == "in" and r5[1] is True
            v.passou("5. texto 'ajuda' grava is_alert=true", ok5, "direction=in")
        except Exception as exc:
            v.passou("5. texto 'ajuda' grava is_alert=true", False, _resumo(exc))

        # --- Checagem 6: restart do ingest no meio do fluxo
        try:
            base = consultar(pg, "SELECT COUNT(*) FROM positions WHERE node_num = %s", (NUM_A["b1"],))[0][0]
            proc_ingest.parar()
            processos = [p for p in processos if p is not proc_ingest]
            time.sleep(3.0)
            print("   ingest parado; publicando P4/P5/P6 enquanto fora", flush=True)
            ids_off = []
            for k, (latb, lonb) in enumerate([(LAT["b1"], LON["b1"]), (LAT["b2"], LON["b2"]), (LAT["b3"], LON["b3"])]):
                pid = secrets.randbits(32) | 1
                ids_off.append(pid)
                env_off = envelope_posicao(psk, pid, NUM_A["b1"], latb, lonb, tempo=agora() - 3600 - k)
                publicar(clientes["b1"], topicos_up["b1"], env_off)
            print("   reiniciando ingest (mesma sessão persistente)", flush=True)
            proc_ingest2 = iniciar_ingest(venv_py, REPO, psk_b64, pg, ca, ROOT, log_dir, rodada=2)
            processos.append(proc_ingest2)
            alvo6 = base + 3
            def _c6():
                n = consultar(pg, "SELECT COUNT(*) FROM positions WHERE node_num = %s", (NUM_A["b1"],))[0][0]
                return n >= alvo6
            esperar("3 posições da parada chegarem após restart", _c6, timeout=60.0)
            raw456 = consultar(pg, "SELECT packet_id, COUNT(*) FROM raw_envelopes WHERE packet_id = ANY(%s) GROUP BY packet_id", (ids_off,))
            dup_count = consultar(pg, "SELECT COUNT(*) FROM raw_envelopes WHERE packet_id = ANY(%s) AND duplicate = true", (ids_off,))[0][0]
            ids_pos = {r[0] for r in consultar(pg, "SELECT packet_id FROM positions WHERE node_num = %s", (NUM_A["b1"],))}
            ok6 = (
                set(ids_off).issubset(ids_pos)
                and dup_count == 0
                and all(c == 1 for _, c in raw456)
            )
            v.passou("6. restart sem perda nem duplicação (sessão persistente)", ok6, f"ids_off={len(ids_off)}")
        except Exception as exc:
            v.passou("6. restart sem perda nem duplicação (sessão persistente)", False, _resumo(exc))

        # --- Checagem 8: outbox entrega SÓ ao barco alvo
        texto_b3 = ""  # usada depois na checagem 7 (escopo de função, sem vazamento)
        try:
            texto_b3 = f"rig-e2e-{secrets.token_hex(8)}"
            id_out = inserir_outbox(pg, "b3", texto_b3, time.time() + 900)
            def _c8():
                for t, pl in clientes["b3"].messages:
                    if decifrar(pl, psk) == ("texto", NUM_VGW["b3"], texto_b3):
                        return True
                return False
            esperar("b3 receber mensagem decifrável do outbox", _c8, timeout=45.0)
            linha_out = consultar(pg, "SELECT status, packet_id FROM chat_outbox WHERE id = %s", (id_out,))[0]
            outros = {b: len(clientes[b].messages) for b in BARCOS if b != "b3"}
            chat_b3 = consultar(pg, "SELECT COUNT(*) FROM chat_messages WHERE packet_id = %s AND text = %s", (linha_out[1], texto_b3))
            ok8 = (
                linha_out[0] == "sent"
                and linha_out[1] is not None
                and chat_b3[0][0] == 1
                and all(c == 0 for c in outros.values())
            )
            v.passou(
                "8. outbox publica só para b3, decifra certo, status sent",
                ok8,
                f"status={linha_out[0]}",
            )
        except Exception as exc:
            v.passou("8. outbox publica só para b3, decifra certo, status sent", False, _resumo(exc))

        # --- Checagem 9: expirada nunca é publicada
        try:
            antes9 = len(clientes["b3"].messages)
            id_out2 = inserir_outbox(pg, "b3", f"rig-e2e-exp-{secrets.token_hex(6)}", time.time() - 1)
            time.sleep(12.0)  # ≥ 2 iterações do loop do outbox (2 s) + margem
            linha9 = consultar(pg, "SELECT status FROM chat_outbox WHERE id = %s", (id_out2,))
            ok9 = (
                len(linha9) == 1
                and linha9[0][0] == "expired"
                and len(clientes["b3"].messages) == antes9
            )
            v.passou("9. outbox expirada vira 'expired' sem publicar", ok9, f"status={linha9[0][0] if linha9 else 'ausente'}")
        except Exception as exc:
            v.passou("9. outbox expirada vira 'expired' sem publicar", False, _resumo(exc))

        # --- Checagem 7: ingest nunca publica; monitor vê apenas o ledger
        try:
            # Conta do ingest tenta publicar: broker nega via ACL (PUBACK >= 128)
            cli_ingest = native_rig_test.MqttTestClient(
                client_id="e2e-probe-ingest",
                username="ingest",
                password=senhas_nos["ingest"],
                ca_certs=ca,
                host=broker_host,
                port=broker_port,
            )
            rc7 = cli_ingest.connect(timeout=10.0)
            if getattr(rc7, "is_failure", False):
                raise AssertionError("probe do ingest não conectou")
            rc_pub = cli_ingest.publish(f"{ROOT}/2/e/{CANAL}/{NOS['b1']}", b"probe-acl", qos=1, timeout=10.0)
            cli_ingest.close()
            ingest_denied = getattr(rc_pub, "is_failure", False)
            # Monitor: tudo que NÃO está no ledger tem de ser burst nodeinfo válido
            vistos = list(monitor.messages)
            intrusos: list[str] = []
            hits_ledger = 0
            for t, pl in vistos:
                if (t, pl) in LEDGER:
                    hits_ledger += 1
                    continue
                parte = t.rsplit("/", 1)[-1]
                tipo, num, _ = decifrar(pl, psk)
                if t.startswith(f"{ROOT}/2/e/EVU/") and tipo == "nodeinfo" and f"!{num:08x}" == parte:
                    continue  # burst NodeInfo do outbox: esperado
                if t == topicos_frota["b3"] and tipo == "texto" and num == NUM_VGW["b3"]:
                    continue  # mensagem do outbox ao barco alvo: esperada
                intrusos.append(f"{t} ({tipo})")
            ok7 = ingest_denied and not intrusos and hits_ledger > 0
            v.passou("7. ingest nunca publica; monitor só vê o esperado", ok7, f"intrusos={len(intrusos)}")
            for x in intrusos[:5]:
                print(f"   intruso: {x}", flush=True)
        except Exception as exc:
            v.passou("7. ingest ACL/monitor", False, _resumo(exc))

        # --- Checagem 10: nenhum log contém PSK (b64/hex) nem texto de mensagem
        try:
            material = [psk_b64, psk.hex(), psk.hex().upper(), "ajuda", texto_b3]
            for arq in sorted(log_dir.glob("*.log")):
                dados = arq.read_bytes()
                for segredo in material:
                    if segredo.encode("utf-8") in dados:
                        raise AssertionError(f"log {arq.name} contém material sensível")
            v.passou("10. logs sem PSK nem texto de mensagem", True, f"logs={len(list(log_dir.glob('*.log')))}")
        except Exception as exc:
            v.passou("10. logs sem PSK nem texto", False, _resumo(exc))
    finally:
        print("=== Encerrando: parando processos e fechando clientes ===", flush=True)
        for proc in processos:
            proc.parar()
        for cli in clientes.values():
            cli.close()
        if monitor is not None:
            monitor.close()
    print("", flush=True)
    print("=== RESULTADO ===", flush=True)
    if v.ok():
        print("E2E: TODAS AS CHECAGENS PASSARAM", flush=True)
        return 0
    print(f"E2E: {len(v.falhas)} checagem(ns) falharam: {'; '.join(v.falhas)}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
