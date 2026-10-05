"""native/service: disciplina de ack do ingest nativo (WP-D).

Fakes leves (cliente que registra acks/subscribe, db programável com dedupe),
sem rede e sem banco real. Payloads reais de protobuf (ServiceEnvelope cifrado)
montados com meshtastic.protobuf + crypto. Mesmo estilo de tests/test_ingest.py.
"""
from __future__ import annotations

import base64
import logging
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from meshtastic.protobuf import mesh_pb2, mqtt_pb2

from rastro_gateway.native import service
from rastro_gateway.native.crypto import crypt, expand_psk

TEST_PSK = bytes(range(32))
TEST_PSK_B64 = base64.b64encode(TEST_PSK).decode()
TEST_ROOT = "univaja/mesh"
_GATEWAY = "!a0000001"

# Coordenadas e textos FICTÍCIOS marcantes — o caplog é grepado para provar
# que nada disso sai no log (nunca logar payload/coords/texto/chave).
_LAT_I = -13572468
_LON_I = -24681357
_TEXTO_SECRETO = "socorro-barco-999xyz"

_NOW = 1_800_000_000


def _cifrar(packet, data, from_num, packet_id):
    packet.encrypted = crypt(expand_psk(TEST_PSK), packet_id, from_num,
                             data.SerializeToString())


def _payload_posicao(from_num=0xA0000001, packet_id=101, lat_i=_LAT_I,
                     lon_i=_LON_I, *, channel="EVU", gateway_id=_GATEWAY):
    """ServiceEnvelope cifrado com position (portnum 3) — caminho feliz."""
    envelope = mqtt_pb2.ServiceEnvelope()
    packet = envelope.packet
    setattr(packet, "from", from_num)
    packet.id = packet_id
    packet.rx_time = _NOW
    pos = mesh_pb2.Position(latitude_i=lat_i, longitude_i=lon_i, time=_NOW - 60)
    _cifrar(packet, mesh_pb2.Data(portnum=3, payload=pos.SerializeToString()),
            from_num, packet_id)
    envelope.channel_id = channel
    envelope.gateway_id = gateway_id
    return envelope.SerializeToString()


def _payload_texto(texto, from_num=0xA0000001, packet_id=201):
    """ServiceEnvelope cifrado com texto utf-8 (portnum 1)."""
    envelope = mqtt_pb2.ServiceEnvelope()
    packet = envelope.packet
    setattr(packet, "from", from_num)
    packet.id = packet_id
    packet.rx_time = _NOW
    _cifrar(packet, mesh_pb2.Data(portnum=1, payload=texto.encode()),
            from_num, packet_id)
    envelope.channel_id = "EVU"
    envelope.gateway_id = _GATEWAY
    return envelope.SerializeToString()


class MqttFake:
    """client mínimo: acks/subscribe registrados, publish sempre gravado para
    provar que o ingest NUNCA manda nada ao broker."""

    def __init__(self, fail_from=None):
        self.acks = []          # (mid, qos) na ordem
        self.subscribes = []    # (topic, qos)
        self.publish_calls = [] # prova do "nunca publica"
        self.fail_from = fail_from
        self.fail_count = 0

    def ack(self, mid, qos):
        if self.fail_from is not None and len(self.acks) >= self.fail_from:
            self.fail_count += 1
            raise RuntimeError("broker não aceita ack agora")
        self.acks.append((mid, qos))

    def subscribe(self, topic, qos=0):
        self.subscribes.append((topic, qos))
        return (0, 1)  # MQTT_ERR_SUCCESS

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.publish_calls.append((topic, payload, qos, retain))
        return None


class DbNativeFake:
    """store_native programável com dedupe (packet_seen) emulado em memória.

    ``error`` levanta (erro operacional simulado → sem ack). Envelopes ficam em
    ``calls``; agregados em ``totais`` para as asserções de domínio.
    """

    CHAVES = ("raw", "positions", "duplicates", "chat", "nodeinfo", "telemetry")

    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.seen = set()
        self.totais = {k: 0 for k in self.CHAVES}

    def store_native(self, envelopes):
        self.calls.append(list(envelopes))
        if self.error is not None:
            erro, self.error = self.error, None  # levanta UMA vez (banco volta)
            raise erro
        counts = {k: 0 for k in self.CHAVES}
        for env in envelopes:
            counts["raw"] += 1
            self.totais["raw"] += 1
            chave = (env.from_num, env.packet_id)
            if env.from_num is not None and env.packet_id is not None:
                if chave in self.seen:
                    counts["duplicates"] += 1
                    self.totais["duplicates"] += 1
                    continue  # domínio só na primeira ocorrência
                self.seen.add(chave)
            if env.kind == "position":
                counts["positions"] += 1
                self.totais["positions"] += 1
            elif env.kind == "text":
                counts["chat"] += 1
                self.totais["chat"] += 1
        return counts


def _nativo(client, db, root=TEST_ROOT, batch_max=20):
    cfg = service.NativeConfig(root=root, psk=TEST_PSK)
    return service.NativeIngest(client, db, cfg=cfg, batch_max=batch_max)


def _msg(mid, payload, topic, qos=1):
    msg = type("M", (), {})()
    msg.mid = mid
    msg.payload = payload
    msg.topic = topic
    msg.qos = qos
    return msg


# --- config ------------------------------------------------------------------

def test_config_sem_psk_levanta():
    with pytest.raises(ValueError) as exc:
        service.NativeConfig.from_env({service.ENV_ENABLED: "1"})
    assert "RASTRO_EVU_PSK_B64" in str(exc.value)
    assert str(TEST_PSK) not in str(exc.value)


def test_config_psk_base64_invalida_levanta():
    with pytest.raises(ValueError):
        service.NativeConfig.from_env({service.ENV_PSK: "### nao-b64 ###"})


def test_config_psk_indice_0_rejeitada():
    b64 = base64.b64encode(bytes([0])).decode()
    with pytest.raises(ValueError) as exc:
        service.NativeConfig.from_env({service.ENV_PSK: b64})
    assert "rejeitada" in str(exc.value)


def test_config_psk_16_bytes_valida_e_root_padrao():
    psk = bytes(range(16))
    cfg = service.NativeConfig.from_env(
        {service.ENV_PSK: base64.b64encode(psk).decode()}
    )
    assert cfg.psk == psk
    assert cfg.root == "univaja/mesh"
    assert repr(cfg) == "NativeConfig(root='univaja/mesh')"  # psk fora do repr


def test_config_root_customizada_e_normalizada():
    cfg = service.NativeConfig.from_env(
        {service.ENV_PSK: TEST_PSK_B64, service.ENV_ROOT: "/frota/x/"}
    )
    assert cfg.root == "frota/x"
    assert service.NativeConfig(root="frota/x", psk=TEST_PSK).handles(
        "frota/x/2/e/EVU/!a0000001"
    )


# --- ack / lote --------------------------------------------------------------

def test_replay_mesma_mensagem_uma_linha_de_dominio_e_ack_duplo_seguro():
    """Redelivery/replay: mesma mensagem 2x → bruto 2x, domínio 1x, ack 2x."""
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db)
    payload = _payload_posicao(packet_id=301)
    topico = f"{TEST_ROOT}/2/e/EVU/!a0000009"
    nativo.on_message(client, _msg(5, payload, topico))
    nativo.on_message(client, _msg(5, payload, topico))  # redelivery (mesmo mid)
    assert nativo.pending() == 2
    assert client.acks == []  # nada ackado antes do commit
    assert nativo.flush() is True
    assert client.acks == [(5, 1), (5, 1)]  # ack duplo é seguro
    assert len(db.calls) == 1
    assert db.totais == {
        "raw": 2, "positions": 1, "duplicates": 1, "chat": 0, "nodeinfo": 0,
        "telemetry": 0,
    }


def test_commit_levanta_nao_acka_e_reenfileira():
    client = MqttFake()
    db = DbNativeFake(error=RuntimeError("banco fora"))
    nativo = _nativo(client, db)
    nativo.on_message(client, _msg(7, _payload_posicao(packet_id=401),
                                   f"{TEST_ROOT}/2/e/EVU/!a0000002"))
    assert nativo.flush() is False
    assert client.acks == []
    assert nativo.pending() == 1  # requeue preservado
    assert nativo.flush() is True  # banco voltou: commita e acka
    assert client.acks == [(7, 1)]


def test_seis_gateways_simultaneos_cada_um_contado():
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db, batch_max=6)
    gateways = [f"!a00000{i:02d}" for i in range(1, 7)]
    for i, gw in enumerate(gateways, start=1):
        payload = _payload_posicao(packet_id=500 + i, gateway_id=gw)
        nativo.on_message(client, _msg(600 + i, payload,
                                       f"{TEST_ROOT}/2/e/EVU/{gw}"))
    assert nativo.pending() == 0  # 6º atinge o limite: auto-flush do lote cheio
    assert len(db.calls) == 1
    assert nativo.flush() is True  # flush vazio é ok
    guardados = db.calls[-1]
    assert {e.gateway_id for e in guardados} == set(gateways)
    assert {e.gateway_num for e in guardados} == {0xA0000001 + i for i in range(6)}
    assert client.acks == [(600 + i, 1) for i in range(1, 7)]


def test_payload_malformado_gravado_bruto_e_ackado():
    """Payload que não é protobuf: grava bruto (kind=malformed) + ack — sem crash."""
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db)
    lixo = b"nao-e-protobuf-\x00\xff"
    nativo.on_message(client, _msg(9, lixo, f"{TEST_ROOT}/2/e/EVU/!a0000003"))
    assert nativo.flush() is True
    assert client.acks == [(9, 1)]
    env = db.calls[-1][0]
    assert env.kind == "malformed"
    assert env.extra["raw"] == lixo
    assert env.extra["topic"] == f"{TEST_ROOT}/2/e/EVU/!a0000003"


def test_topico_invalido_sob_root_grava_bruto_e_acka():
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db)
    nativo.on_message(client, _msg(10, b"x", f"{TEST_ROOT}/2/e/so Canal EVU"))
    assert nativo.flush() is True
    assert client.acks == [(10, 1)]
    assert db.calls[-1][0].reason == "topico_invalido"


def test_pki_opaco_grava_so_bruto():
    """PKI é opaco: bruto gravado, nada de domínio, ack normal."""
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db)
    payload = _payload_posicao(packet_id=701)
    nativo.on_message(client, _msg(11, payload,
                                   f"{TEST_ROOT}/2/e/PKI/!a0000004"))
    assert nativo.flush() is True
    assert client.acks == [(11, 1)]
    env = db.calls[-1][0]
    assert env.kind == "opaque"
    assert env.extra["raw"] == payload


def test_log_nao_vaza_payload_coordenadas_nem_chave(caplog):
    """caplog: só tópico/contagens/kinds — nada de coords, texto ou PSK."""
    client = MqttFake()
    db = DbNativeFake()
    nativo = _nativo(client, db)
    with caplog.at_level(logging.DEBUG):
        nativo.on_message(client, _msg(12, _payload_posicao(packet_id=801),
                                       f"{TEST_ROOT}/2/e/EVU/!a0000005"))
        nativo.on_message(client, _msg(13, _payload_texto(_TEXTO_SECRETO, packet_id=802),
                                       f"{TEST_ROOT}/2/e/EVU/!a0000005"))
        nativo.on_message(client, _msg(14, b"lixo-bruto-protobuf",
                                       f"{TEST_ROOT}/2/e/EVU/!a0000005"))
        assert nativo.flush() is True
        psk_b64 = TEST_PSK_B64
    for proibido in (str(_LAT_I), str(_LON_I), _TEXTO_SECRETO, psk_b64,
                     "lixo-bruto-protobuf"):
        assert proibido not in caplog.text
    assert "lote nativo: 3 msgs" in caplog.text  # contagens/kinds podem
