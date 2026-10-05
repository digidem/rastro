"""Testes de geração e publicação de NodeInfo virtual (WP-E).

Verifica o empacotamento do User (portnum=4), cifra AES-CTR,
formatação do ServiceEnvelope e publicação no tópico do gateway virtual.
"""
from __future__ import annotations

import logging
import random
from typing import Any

import pytest
from meshtastic.protobuf import mesh_pb2, mqtt_pb2

from rastro_gateway.chat.outbox import (
    Outbox,
    build_nodeinfo_envelope,
    build_nodeinfo_packet,
)
from rastro_gateway.native import crypto, envelope

TEST_PSK = bytes(range(32))
NOW = 1_760_000_000.0
ROOT = "univaja/mesh"


class FakeDb:
    def __init__(self) -> None:
        self.virtual_gateways: dict[str, dict[str, Any]] = {}

    def add_virtual_gateway(
        self,
        boat_id: str,
        gateway_id: str,
        virtual_node_num: int,
        active: bool = True,
    ) -> None:
        self.virtual_gateways[boat_id] = {
            "boat_id": boat_id,
            "gateway_id": gateway_id,
            "virtual_node_num": virtual_node_num,
            "active": active,
        }

    def claim_outbox(self, limit: int = 10, now: Any = None) -> list[dict[str, Any]]:
        return []

    def mark_outbox(self, *args: Any, **kwargs: Any) -> bool:
        return True


def test_build_nodeinfo_envelope_manual_decrypt() -> None:
    """Verifica empacotamento manual e decodificação do NodeInfo (User, portnum=4)."""
    virtual_num = 0xF0000001
    gw_id = "!f0000001"
    packet_id = 554433

    raw_bytes = build_nodeinfo_envelope(
        from_num=virtual_num,
        packet_id=packet_id,
        psk=TEST_PSK,
        gateway_id=gw_id,
    )

    # 1. Estrutura do ServiceEnvelope
    env = mqtt_pb2.ServiceEnvelope.FromString(raw_bytes)
    assert env.channel_id == "EVU"
    assert env.gateway_id == gw_id

    # 2. Estrutura do MeshPacket
    mp = env.packet
    assert mp.to == 0xFFFFFFFF
    assert getattr(mp, "from") == virtual_num
    assert mp.id == packet_id
    assert mp.hop_limit == 3
    assert mp.want_ack is False
    assert mp.channel == crypto.channel_hash("EVU", TEST_PSK)

    # 3. Decifra e validação do payload Data -> User
    chave = crypto.expand_psk(TEST_PSK)
    plaintext = crypto.crypt(chave, mp.id, getattr(mp, "from"), mp.encrypted)
    data = mesh_pb2.Data.FromString(plaintext)
    assert data.portnum == 4  # NODEINFO_APP

    user = mesh_pb2.User.FromString(data.payload)
    assert user.id == gw_id
    assert user.long_name == "Rastro"
    assert user.short_name == "RSTR"
    assert user.hw_model == mesh_pb2.HardwareModel.PRIVATE_HW


def test_build_nodeinfo_envelope_decode_envelope_roundtrip() -> None:
    """Valida decodificação de NodeInfo via decode_envelope do envelope.py."""
    virtual_num = 0xF0000002
    gw_id = "!f0000002"
    packet_id = 887766

    raw_bytes = build_nodeinfo_envelope(
        from_num=virtual_num,
        packet_id=packet_id,
        psk=TEST_PSK,
        gateway_id=gw_id,
    )

    decoded = envelope.decode_envelope(
        raw_bytes,
        channel="EVU",
        gateway_id_topic=gw_id,
        psk=TEST_PSK,
        now=NOW,
    )
    assert decoded.kind == "nodeinfo"
    assert decoded.from_num == virtual_num
    assert decoded.to_num == 0xFFFFFFFF
    assert decoded.packet_id == packet_id
    assert decoded.nodeinfo is not None
    assert decoded.nodeinfo.user_id == gw_id
    assert decoded.nodeinfo.long_name == "Rastro"
    assert decoded.nodeinfo.short_name == "RSTR"
    assert decoded.nodeinfo.hw_model == "PRIVATE_HW"


def test_custom_nodeinfo_fields() -> None:
    """Valida customização de nomes e modelos de hardware no NodeInfo."""
    virtual_num = 0xF0000003
    gw_id = "!f0000003"
    packet_id = 112233

    raw_bytes = build_nodeinfo_envelope(
        from_num=virtual_num,
        packet_id=packet_id,
        psk=TEST_PSK,
        gateway_id=gw_id,
        long_name="Rastro Estação",
        short_name="EST",
        hw_model=mesh_pb2.HardwareModel.TBEAM,
    )

    decoded = envelope.decode_envelope(
        raw_bytes,
        channel="EVU",
        gateway_id_topic=gw_id,
        psk=TEST_PSK,
        now=NOW,
    )
    assert decoded.kind == "nodeinfo"
    assert decoded.nodeinfo is not None
    assert decoded.nodeinfo.long_name == "Rastro Estação"
    assert decoded.nodeinfo.short_name == "EST"
    assert decoded.nodeinfo.hw_model == "TBEAM"


def test_outbox_publish_nodeinfo() -> None:
    """Outbox publica NodeInfo no tópico correto com QoS 1 e retain=False."""
    db = FakeDb()
    published = []

    def mock_publish(topic: str, payload: bytes, qos: int = 1, retain: bool = False) -> bool:
        published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        rng=random.Random(123),
    )

    ok = outbox.publish_nodeinfo(0xF0000005, gateway_id="!f0000005")
    assert ok is True
    assert len(published) == 1

    item = published[0]
    assert item["topic"] == f"{ROOT}/2/e/EVU/!f0000005"
    assert item["qos"] == 1
    assert item["retain"] is False

    decoded = envelope.decode_envelope(
        item["payload"],
        channel="EVU",
        gateway_id_topic="!f0000005",
        psk=TEST_PSK,
        now=NOW,
    )
    assert decoded.kind == "nodeinfo"
    assert decoded.from_num == 0xF0000005


def test_outbox_publish_all_nodeinfos_only_active() -> None:
    """publish_all_nodeinfos publica apenas para gateways virtuais ativos."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_virtual_gateway("b2", "!f0000002", 0xF0000002, active=True)
    db.add_virtual_gateway("b3", "!f0000003", 0xF0000003, active=False)  # inativo

    published_topics = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_topics.append(topic)
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
    )

    count = outbox.publish_all_nodeinfos()
    assert count == 2
    assert len(published_topics) == 2
    assert f"{ROOT}/2/e/EVU/!f0000001" in published_topics
    assert f"{ROOT}/2/e/EVU/!f0000002" in published_topics
    assert f"{ROOT}/2/e/EVU/!f0000003" not in published_topics


def test_nodeinfo_logging_does_not_leak_key(caplog: pytest.LogCaptureFixture) -> None:
    """A publicação de NodeInfo não deve logar chave ou segredos."""
    db = FakeDb()
    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
    )

    with caplog.at_level(logging.DEBUG):
        outbox.publish_nodeinfo(0xF0000001, gateway_id="!f0000001")

    logs = caplog.text
    assert TEST_PSK.hex() not in logs
    assert str(TEST_PSK) not in logs


def test_nodeinfo_user_id_matches_from_num_not_gateway_id() -> None:
    """NodeInfo User.id deve ser igual a f'!{virtual_node_num:08x}', e NÃO o gateway_id."""
    virtual_num = 0x12345678
    gw_id = "!aabbccdd"
    packet_id = 998877

    raw_bytes = build_nodeinfo_envelope(
        from_num=virtual_num,
        packet_id=packet_id,
        psk=TEST_PSK,
        gateway_id=gw_id,
    )

    env = mqtt_pb2.ServiceEnvelope.FromString(raw_bytes)
    assert env.gateway_id == gw_id
    assert env.channel_id == "EVU"

    mp = env.packet
    assert getattr(mp, "from") == virtual_num
    assert mp.id == packet_id
    assert mp.hop_limit == 3
    assert mp.hop_start == 3

    # Decodifica User no payload
    chave = crypto.expand_psk(TEST_PSK)
    plaintext = crypto.crypt(chave, mp.id, getattr(mp, "from"), mp.encrypted)
    data = mesh_pb2.Data.FromString(plaintext)
    assert data.portnum == 4
    user = mesh_pb2.User.FromString(data.payload)

    # CRÍTICO: user.id deve ser o virtual_node_num formatado, e não o gateway_id
    assert user.id == f"!{virtual_num:08x}"
    assert user.id == "!12345678"
    assert user.id != gw_id


def test_publish_newly_active_nodeinfos_detects_activation_each_loop() -> None:
    """publish_newly_active_nodeinfos detecta nós ativados a cada rodada do loop."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_virtual_gateway("b2", "!f0000002", 0xF0000002, active=True)
    db.add_virtual_gateway("b3", "!f0000003", 0xF0000003, active=False)

    published_topics: list[str] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_topics.append(topic)
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
    )

    # 1. Republicação periódica inicial: publica b1 e b2
    initial_count = outbox.publish_all_nodeinfos()
    assert initial_count == 2
    assert len(published_topics) == 2
    published_topics.clear()

    # 2. Próximo ciclo do loop: nenhum novo ativo
    assert outbox.publish_newly_active_nodeinfos() == 0
    assert len(published_topics) == 0

    # 3. Novo barco b4 cadastrado como ativo: detectado no loop
    db.add_virtual_gateway("b4", "!f0000004", 0xF0000004, active=True)
    assert outbox.publish_newly_active_nodeinfos() == 1
    assert len(published_topics) == 1
    assert f"{ROOT}/2/e/EVU/!f0000004" in published_topics
    published_topics.clear()

    # 4. Outro ciclo sem mudanças: zero
    assert outbox.publish_newly_active_nodeinfos() == 0

    # 5. Barco b3 (inativo) é ativado: detectado no loop
    db.virtual_gateways["b3"]["active"] = True
    assert outbox.publish_newly_active_nodeinfos() == 1
    assert len(published_topics) == 1
    assert f"{ROOT}/2/e/EVU/!f0000003" in published_topics
    published_topics.clear()

    # 6. Barco b2 é desativado e depois reativado
    db.virtual_gateways["b2"]["active"] = False
    assert outbox.publish_newly_active_nodeinfos() == 0

    db.virtual_gateways["b2"]["active"] = True
    assert outbox.publish_newly_active_nodeinfos() == 1
    assert len(published_topics) == 1
    assert f"{ROOT}/2/e/EVU/!f0000002" in published_topics


def test_nodeinfo_publish_does_not_pass_chat_expiry_property() -> None:
    """A publicação de NodeInfo não deve definir propriedade MessageExpiryInterval de chat."""
    db = FakeDb()
    published_calls: list[dict[str, Any]] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_calls.append({"topic": topic, "payload": payload, "kwargs": kwargs})
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
    )

    ok = outbox.publish_nodeinfo(0xF0000009, gateway_id="!f0000009")
    assert ok is True
    assert len(published_calls) == 1

    call = published_calls[0]
    props = call["kwargs"].get("properties")
    if props is not None:
        assert getattr(props, "MessageExpiryInterval", None) is None




def test_chat_desligado_fica_ocioso_e_sai_no_sigterm(monkeypatch, caplog):
    """RASTRO_CHAT_ENABLED=0: não exige PSK, não conecta, sai com 0 no SIGTERM."""
    import os
    import signal
    import threading

    from rastro_gateway.chat import __main__ as chat_main

    monkeypatch.setenv("RASTRO_CHAT_ENABLED", "0")
    monkeypatch.delenv("RASTRO_EVU_PSK_B64", raising=False)
    guardados = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: guardados.__setitem__(sig, h))
    resultado = {}
    t = threading.Thread(target=lambda: resultado.__setitem__("rc", chat_main.main()))
    t.start()
    for _ in range(100):
        if signal.SIGTERM in guardados:
            break
        threading.Event().wait(0.02)
    guardados[signal.SIGTERM](signal.SIGTERM, None)
    t.join(timeout=5)
    assert resultado.get("rc") == 0
