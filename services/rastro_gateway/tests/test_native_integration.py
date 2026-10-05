"""Integração do ingest nativo (WP-D): roteamento, subscrições e "nunca publica".

Mesmos fakes leves de tests/test_native_service.py (cliente que registra
acks/subscribes/publicações; dbs programáveis). O teste de grep varre os fontes
do ingest para provar que nenhum caminho de ingestão chama ``publish`` — a
publicação é do outbox (WP-E), outro processo e outra conta.
"""
from __future__ import annotations

import base64
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_gateway.ingest import __main__ as main_mod
from rastro_gateway.ingest import mqtt_in
from rastro_gateway.native import alerts, service

TEST_ROOT = "univaja/mesh"
TEST_PSK = bytes(range(32))

NOW = 1_800_000_000


class ClienteFalso:
    """Cliente paho minimal: registra subscrições, acks e publicações."""

    def __init__(self):
        self.subscribes = []
        self.acks = []
        self.publish_calls = []

    def subscribe(self, topic, qos=0):
        self.subscribes.append((topic, qos))
        return (0, 1)  # MQTT_ERR_SUCCESS

    def ack(self, mid, qos):
        self.acks.append((mid, qos))

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.publish_calls.append((topic, payload, qos, retain))


class DbLegadoFalso:
    """store_batch (legado) e store_native (nativo) contados separadamente."""

    falso = None

    def __init__(self):
        self.calls = []
        self.native_calls = 0

    def store_batch(self, records, names, fleet_ids):
        self.calls.append(len(records))
        return len(records), 0, []

    def store_native(self, envelopes):
        self.native_calls += len(envelopes)
        return {k: 0 for k in ("raw", "positions", "duplicates", "chat",
                               "nodeinfo", "telemetry")}


def _ingester(client, db, native):
    cfg = mqtt_in.MqttConfig(
        host="b.teste", port=8883, username=None, password=None,
        ca_cert=None, client_id="t", topic_prefix="rastro", keepalive_secs=60,
    )
    return mqtt_in.Ingester(client, db, {}, {}, cfg=cfg, native=native)


def _msg(mid, payload, topic, attempt=1):
    msg = type("M", (), {})()
    msg.mid = mid
    msg.payload = payload
    msg.topic = topic
    msg.qos = 1
    return msg


# --- subscrição e roteamento ---------------------------------------------------

def test_on_connect_assina_legado_e_nativo():
    client = ClienteFalso()
    db = DbLegadoFalso()
    cfg = service.NativeConfig(root=TEST_ROOT, psk=TEST_PSK)
    nativo = service.NativeIngest(client, db, cfg=cfg)
    ing = _ingester(client, db, nativo)
    ing.on_connect(client, None, {}, 0, None)
    assinados = dict(client.subscribes)
    assert assinados.get(f"{TEST_ROOT}/2/e/#") == 1
    for sufixo in ("positions/#", "telemetry/#", "status/#"):
        assert assinados.get(f"rastro/{sufixo}") == 1


def test_nativo_desligado_nao_assina_root_nativo():
    client = ClienteFalso()
    db = DbLegadoFalso()
    ing = _ingester(client, db, native=None)
    ing.on_connect(client, None, {}, 0, None)
    assert all(not t.startswith(TEST_ROOT) for t, _ in client.subscribes)
    assert len(client.subscribes) == 3  # só o legado



# --- roteamento de mensagens ---------------------------------------------------

def _payload_posicao(packet_id):
    """ServiceEnvelope cifrado (position, portnum 3) — mesmo builder do service."""
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    from rastro_gateway.native.crypto import crypt, expand_psk

    envelope = mqtt_pb2.ServiceEnvelope()
    packet = envelope.packet
    setattr(packet, "from", 0xA0000001)
    packet.id = packet_id
    pos = mesh_pb2.Position(latitude_i=-13572468, longitude_i=-24681357,
                            time=NOW - 60)
    data = mesh_pb2.Data(portnum=3, payload=pos.SerializeToString())
    packet.encrypted = crypt(expand_psk(TEST_PSK), packet_id, 0xA0000001,
                             data.SerializeToString())
    envelope.channel_id = "EVU"
    envelope.gateway_id = "!a0000001"
    return envelope.SerializeToString()


def test_topico_legado_vai_para_o_caminho_legado():
    client = ClienteFalso()
    db = DbLegadoFalso()
    cfg = service.NativeConfig(root=TEST_ROOT, psk=TEST_PSK)
    nativo = service.NativeIngest(client, db, cfg=cfg)
    ing = _ingester(client, db, nativo)
    # payload fora do schema legado: o handler legado loga e acka na hora
    ing.on_message(client, None, _msg(1, b"nao-e-schema-legado",
                                      "rastro/positions/x"))
    assert client.acks == [(1, 1)]  # ack imediato = caminho legado
    assert db.calls == []
    assert db.native_calls == 0
    assert client.publish_calls == []


def test_topico_nativo_vai_para_o_caminho_nativo():
    client = ClienteFalso()
    db = DbLegadoFalso()
    cfg = service.NativeConfig(root=TEST_ROOT, psk=TEST_PSK)
    nativo = service.NativeIngest(client, db, cfg=cfg)
    ing = _ingester(client, db, nativo)
    ing.on_message(client, None, _msg(2, _payload_posicao(301),
                                      f"{TEST_ROOT}/2/e/EVU/!a0000001"))
    assert client.acks == []  # nativo: ack só após flush/commit
    assert nativo.pending() == 1
    assert nativo.flush() is True
    assert client.acks == [(2, 1)]
    assert db.native_calls == 1
    assert db.calls == []  # nada foi para o legado
    assert client.publish_calls == []


# --- boot: exit 2 sem PSK, ciclo de alertas off por padrão ----------------------

def test_main_sem_psk_com_nativo_ligado_sai_2(monkeypatch, caplog):
    monkeypatch.setenv("RASTRO_PG_HOST", "pg.teste")
    monkeypatch.setenv("RASTRO_PG_PASSWORD", "x")
    monkeypatch.delenv("RASTRO_MQTT_CA_B64", raising=False)
    monkeypatch.delenv("RASTRO_MQTT_CA_CERT", raising=False)
    monkeypatch.setenv("RASTRO_NATIVE_ENABLED", "1")
    monkeypatch.delenv("RASTRO_EVU_PSK_B64", raising=False)
    with caplog.at_level(logging.ERROR):
        codigo = main_mod.main()
    assert codigo == main_mod.EXIT_CONFIG == 2
    assert "ingest nativo" in caplog.text
    assert "RASTRO_EVU_PSK_B64" in caplog.text  # nome da env pode
    assert base64.b64encode(TEST_PSK).decode() not in caplog.text


def test_iniciar_flushers_sobe_legado_e_nativo():
    """Achado 1: native.start() nunca era chamado — flusher nativo agora sobe junto."""

    class ConsumidorFalso:
        def __init__(self):
            self.starts = 0

        def start(self):
            self.starts += 1

    ing, nativo = ConsumidorFalso(), ConsumidorFalso()
    main_mod._iniciar_flushers(ing, nativo)
    assert ing.starts == 1
    assert nativo.starts == 1


def test_iniciar_flushers_sem_nativo_sobe_so_legado():
    """Modo só-legado (RASTRO_NATIVE_ENABLED ausente): nenhum start no nativo."""

    class ConsumidorFalso:
        def __init__(self):
            self.starts = 0

        def start(self):
            self.starts += 1

    ing = ConsumidorFalso()
    main_mod._iniciar_flushers(ing, None)
    assert ing.starts == 1


def test_iniciar_ciclo_alertas_desligado_por_padrao(monkeypatch):
    monkeypatch.delenv(alerts.ENV_ALERTS_ENABLED, raising=False)
    assert main_mod._iniciar_ciclo_alertas(object(), threading.Event()) is None


def test_iniciar_ciclo_alertas_para_com_o_stop_event(monkeypatch):
    monkeypatch.setenv(alerts.ENV_ALERTS_ENABLED, "1")
    monkeypatch.setenv(alerts.ENV_INTERVALO_SECS, "60")
    parar = threading.Event()
    parar.set()  # já nasce parado: a thread sai no primeiro wait
    th = main_mod._iniciar_ciclo_alertas(object(), parar)
    assert th is not None
    th.join(timeout=5)
    assert not th.is_alive()


# --- "nunca publica" ---------------------------------------------------------

def test_ingest_nunca_chama_publish():
    """Grep nos fontes: nenhum caminho de ingestão tem saída para o broker."""
    base = Path(__file__).resolve().parents[1]  # rastro_gateway/
    fontes = sorted(base.glob("ingest/*.py")) + [base / "native" / "service.py"]
    fontes.append(base / "native" / "alerts.py")
    assert fontes, "fontes do ingest não encontrados"
    for arq in fontes:
        texto = arq.read_text(encoding="utf-8")
        assert "publish(" not in texto, arq.name
