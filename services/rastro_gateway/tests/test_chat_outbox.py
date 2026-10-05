"""Testes da fila de saída do chat (WP-E).

Verifica os invariantes do protocolo Meshtastic e as regras de arquitetura:
1. Bytes publicados decodificam para texto com to=0xffffffff, from=virtual node,
   hop_limit=3, channel hash exato, tópico exato, QoS 1 e retain=False.
2. Expiração por TTL não publica nada.
3. Falha no PUBACK mantém mensagem na fila (status queued).
4. No-echo: chat.outbox não importa módulos de assinatura e ingest não importa chat.
5. Seis barcos -> seis tópicos distintos sem vazamento entre barcos.
6. Unicidade de packet id em 10k gerações com rng semeado.
7. Logs nunca contêm payload, texto, chave ou hex de chave.
8. Limite de taxa (rate limit) por barco.
9. Ausência de gateway virtual ativo marca 'failed' com 'sem gateway virtual'.
"""
from __future__ import annotations

import ast
import base64
import logging
from pathlib import Path
import random
import time
from typing import Any

import pytest
from meshtastic.protobuf import mesh_pb2, mqtt_pb2
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from rastro_gateway.chat import __main__ as chat_main
from rastro_gateway.chat.outbox import (
    Outbox,
    build_text_envelope,
    build_text_packet,
)
from rastro_gateway.native import crypto, envelope

TEST_PSK = bytes(range(32))
NOW = 1_760_000_000.0
ROOT = "univaja/mesh"


class FakeDb:
    """Implementação em memória de banco de dados para testes unitários do Outbox."""

    def __init__(self) -> None:
        self.outbox_rows: list[dict[str, Any]] = []
        self.virtual_gateways: dict[str, dict[str, Any]] = {}
        self.marked_calls: list[dict[str, Any]] = []
        self.next_id = 1

    def add_outbox_row(
        self,
        boat_id: str,
        text: str,
        expires_at: float | None = None,
        status: str = "queued",
        created_by: str = "operador",
    ) -> int:
        row_id = self.next_id
        self.next_id += 1
        self.outbox_rows.append(
            {
                "id": row_id,
                "boat_id": boat_id,
                "text": text,
                "created_by": created_by,
                "created_at": NOW - 10.0,
                "expires_at": expires_at if expires_at is not None else NOW + 600.0,
                "status": status,
                "sent_at": None,
                "packet_id": None,
                "error": None,
            }
        )
        return row_id

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
        tempo_ref = NOW
        if now is not None:
            if hasattr(now, "timestamp"):
                tempo_ref = now.timestamp()
            elif isinstance(now, (int, float)):
                tempo_ref = float(now)

        claimed = []
        for r in self.outbox_rows:
            if r["status"] == "queued":
                if r["expires_at"] is not None and r["expires_at"] <= tempo_ref:
                    r["status"] = "expired"
                else:
                    if len(claimed) < limit:
                        claimed.append(dict(r))
        return claimed

    def mark_outbox(
        self,
        outbox_id: int,
        status: str,
        packet_id: int | None = None,
        error: str | None = None,
    ) -> bool:
        self.marked_calls.append(
            {
                "id": outbox_id,
                "status": status,
                "packet_id": packet_id,
                "error": error,
            }
        )
        for r in self.outbox_rows:
            if r["id"] == outbox_id:
                r["status"] = status
                if packet_id is not None:
                    r["packet_id"] = packet_id
                if error is not None:
                    r["error"] = error
                if status == "sent":
                    r["sent_at"] = NOW
                return True
        return False


# -----------------------------------------------------------------------------
# 1. Invariante: Decodificação e Roundtrip
# -----------------------------------------------------------------------------


def test_build_text_packet_and_envelope_roundtrip() -> None:
    """Verifica que os bytes gerados decodificam para o texto com metadados exatos."""
    text = "Coordenadas recebidas, iniciando patrulha no rio."
    from_num = 0xF0000001
    gateway_id = "!f0000001"
    packet_id = 998877

    raw_bytes = build_text_envelope(
        text=text,
        from_num=from_num,
        packet_id=packet_id,
        psk=TEST_PSK,
        gateway_id=gateway_id,
    )

    # 1. Inspeção manual dos bytes do ServiceEnvelope e MeshPacket
    envelope_proto = mqtt_pb2.ServiceEnvelope.FromString(raw_bytes)
    assert envelope_proto.channel_id == "EVU"
    assert envelope_proto.gateway_id == gateway_id

    mp = envelope_proto.packet
    assert mp.to == 0xFFFFFFFF
    assert getattr(mp, "from") == from_num
    assert mp.id == packet_id
    assert mp.hop_limit == 3
    assert mp.hop_start == 3
    assert mp.want_ack is False
    assert mp.channel == crypto.channel_hash("EVU", TEST_PSK)

    # Decifra payload AES-CTR
    chave = crypto.expand_psk(TEST_PSK)
    plaintext = crypto.crypt(chave, mp.id, getattr(mp, "from"), mp.encrypted)
    data = mesh_pb2.Data.FromString(plaintext)
    assert data.portnum == 1  # TEXT_MESSAGE_APP
    assert data.payload.decode("utf-8") == text

    # 2. Decodificação via decode_envelope oficial
    decoded = envelope.decode_envelope(
        raw_bytes,
        channel="EVU",
        gateway_id_topic=gateway_id,
        psk=TEST_PSK,
        now=NOW,
    )
    assert decoded.kind == "text"
    assert decoded.text is not None
    assert decoded.text.text == text
    assert decoded.from_num == from_num
    assert decoded.to_num == 0xFFFFFFFF
    assert decoded.packet_id == packet_id


def test_outbox_publishes_to_exact_topic_qos1_retain_false() -> None:
    """Outbox publica em <root>/2/e/EVU/<vgw>, QoS 1, retain=False e registra outbox."""
    db = FakeDb()
    db.add_virtual_gateway("barco-alpha", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("barco-alpha", "Mensagem de teste")

    published = []

    def mock_publish(topic: str, payload: bytes, qos: int = 1, retain: bool = False) -> bool:
        published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return True

    recorded_outgoing = []

    def mock_record_outgoing(boat_id: str, from_num: int, packet_id: int, text: str) -> None:
        recorded_outgoing.append(
            {"boat_id": boat_id, "from_num": from_num, "packet_id": packet_id, "text": text}
        )

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
        rng=random.Random(42),
        record_outgoing=mock_record_outgoing,
    )

    sent = outbox.run_once()
    assert sent == 1
    assert len(published) == 1

    pub = published[0]
    assert pub["topic"] == f"{ROOT}/2/e/EVU/!f0000001"
    assert pub["qos"] == 1
    assert pub["retain"] is False

    # Validação do status no DB (queued com packet_id antes de enviar, e sent após confirmação)
    assert len(db.marked_calls) == 2
    call_queued = db.marked_calls[0]
    assert call_queued["id"] == msg_id
    assert call_queued["status"] == "queued"
    assert call_queued["packet_id"] is not None

    call_sent = db.marked_calls[1]
    assert call_sent["id"] == msg_id
    assert call_sent["status"] == "sent"
    assert call_sent["packet_id"] == call_queued["packet_id"]

    # Validação da gravação da mensagem de saída em chat_messages
    assert len(recorded_outgoing) == 1
    out_rec = recorded_outgoing[0]
    assert out_rec["boat_id"] == "barco-alpha"
    assert out_rec["from_num"] == 0xF0000001
    assert out_rec["packet_id"] == call_sent["packet_id"]
    assert out_rec["text"] == "Mensagem de teste"


# -----------------------------------------------------------------------------
# 2. Invariante: Expiração por TTL
# -----------------------------------------------------------------------------


def test_ttl_expiry_means_nothing_published() -> None:
    """Mensagens vencidas pelo TTL não são publicadas."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    # Mensagem com expires_at no passado
    msg_id = db.add_outbox_row("b1", "Mensagem expirada", expires_at=NOW - 1.0)

    published = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published.append(topic)
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    sent = outbox.run_once()
    assert sent == 0
    assert len(published) == 0

    # Confere que o registro foi marcado como expirado
    row = next(r for r in db.outbox_rows if r["id"] == msg_id)
    assert row["status"] == "expired"


# -----------------------------------------------------------------------------
# 3. Invariante: Falha de Publicação Mantém Mensagem Enfileirada
# -----------------------------------------------------------------------------


def test_failed_publish_leaves_row_queued() -> None:
    """Se publish_fn retornar False ou falhar, a mensagem permanece queued."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("b1", "Mensagem que não receberá PUBACK")

    def mock_publish_failure(topic: str, payload: bytes, **kwargs: Any) -> bool:
        return False

    recorded_outgoing = []

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish_failure,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
        record_outgoing=lambda *args, **kwargs: recorded_outgoing.append(args),
    )

    sent = outbox.run_once()
    assert sent == 0
    # packet_id é persistido e mensagem gravada antes da tentativa de publicação
    assert len(db.marked_calls) == 1
    assert db.marked_calls[0]["id"] == msg_id
    assert db.marked_calls[0]["status"] == "queued"
    assert db.marked_calls[0]["packet_id"] is not None
    assert len(recorded_outgoing) == 1

    row = next(r for r in db.outbox_rows if r["id"] == msg_id)
    assert row["status"] == "queued"
    assert row["packet_id"] == db.marked_calls[0]["packet_id"]


# -----------------------------------------------------------------------------
# 4. Invariante: No Echo (Isolamento de Pacotes)
# -----------------------------------------------------------------------------


def test_no_echo_chat_outbox_does_not_import_subscribers() -> None:
    """chat.outbox não deve importar nada que assine o broker (ex.: mqtt_in/Ingester)."""
    outbox_file = Path(__file__).resolve().parents[1] / "chat" / "outbox.py"
    content = outbox_file.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(outbox_file))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "mqtt_in" not in alias.name, f"chat.outbox importa {alias.name}"
                assert "Ingester" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "mqtt_in" not in mod, f"chat.outbox importa de {mod}"
            for alias in node.names:
                assert "Ingester" not in alias.name
                assert "NativeIngest" not in alias.name


def test_no_echo_ingest_does_not_import_chat() -> None:
    """Nenhum arquivo dentro do pacote ingest/ pode importar chat."""
    ingest_dir = Path(__file__).resolve().parents[1] / "ingest"
    py_files = list(ingest_dir.glob("*.py"))
    assert len(py_files) > 0

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "chat" not in alias.name, f"{py_file.name} importa {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "chat" not in mod, f"{py_file.name} importa de {mod}"
                for alias in node.names:
                    assert "chat" not in alias.name, f"{py_file.name} importa símbolo {alias.name}"


# -----------------------------------------------------------------------------
# 5. Invariante: 6 Barcos -> 6 Tópicos Distintos (Sem Vazamento)
# -----------------------------------------------------------------------------


def test_six_boats_six_distinct_topics_no_cross_river() -> None:
    """Seis barcos recebem mensagens em seus respectivos tópicos, sem cruzamento."""
    db = FakeDb()
    boats = [f"barco-{i}" for i in range(1, 7)]
    for i, b in enumerate(boats, start=1):
        db.add_virtual_gateway(b, f"!f000000{i}", 0xF0000000 + i, active=True)
        db.add_outbox_row(b, f"Texto exclusivo para {b}")

    published_by_topic: dict[str, list[bytes]] = {}

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_by_topic.setdefault(topic, []).append(payload)
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    sent = outbox.run_once(limit=10)
    assert sent == 6
    assert len(published_by_topic) == 6

    # Confere que cada barco recebeu EXATAMENTE sua respectiva mensagem
    for i, b in enumerate(boats, start=1):
        expected_topic = f"{ROOT}/2/e/EVU/!f000000{i}"
        assert expected_topic in published_by_topic
        payloads = published_by_topic[expected_topic]
        assert len(payloads) == 1

        decoded = envelope.decode_envelope(
            payloads[0],
            channel="EVU",
            gateway_id_topic=f"!f000000{i}",
            psk=TEST_PSK,
            now=NOW,
        )
        assert decoded.text is not None
        assert decoded.text.text == f"Texto exclusivo para {b}"
        assert decoded.from_num == 0xF0000000 + i


# -----------------------------------------------------------------------------
# 6. Invariante: 10k IDs Únicos com RNG Semeado
# -----------------------------------------------------------------------------


def test_duplicate_id_never_produced_across_10k_builds() -> None:
    """10.000 gerações consecutivas de packet_id não produzem duplicata com RNG semeado."""
    db = FakeDb()
    rng = random.Random(123456)
    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
        rng=rng,
    )

    from_node = 0xF0000001
    generated_ids = [outbox._generate_packet_id(from_node) for _ in range(10_000)]
    assert len(generated_ids) == 10_000
    assert len(set(generated_ids)) == 10_000

    # Todos válidos como inteiros de 32 bits sem sinal
    for pid in generated_ids:
        assert 1 <= pid <= 0xFFFFFFFF


def test_id_in_use_callable_skips_colliding_id() -> None:
    """O callable id_in_use faz o gerador descartar IDs já registrados no banco."""
    db = FakeDb()
    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
        rng=random.Random(999),
        id_in_use=lambda from_num, cand: cand == 12345,
    )

    # Força candidate 12345 como primeiro sorteio
    class RiggedRng:
        def __init__(self) -> None:
            self.vals = [12345, 67890]

        def randint(self, a: int, b: int) -> int:
            return self.vals.pop(0)

    outbox.rng = RiggedRng()
    pid = outbox._generate_packet_id(0xF0000001)
    assert pid == 67890


# -----------------------------------------------------------------------------
# 7. Invariante: Logs Nunca Contêm Texto, Chave ou Hex
# -----------------------------------------------------------------------------


def test_log_output_never_contains_text_key_or_key_hex(caplog: pytest.LogCaptureFixture) -> None:
    """Nenhum log gerado pelo chat outbox deve conter texto de mensagem ou segredos."""
    secret_text = "CONFIDENCIAL_MISSÃO_SECRETA_987654"
    secret_psk = b"CHAVE_SUPER_SECRETA_32_BYTES_001"
    assert len(secret_psk) == 32

    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_outbox_row("b1", secret_text)

    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: True,
        psk=secret_psk,
        root=ROOT,
        clock=lambda: NOW,
    )

    with caplog.at_level(logging.DEBUG):
        outbox.run_once()

    full_logs = caplog.text
    assert secret_text not in full_logs
    assert secret_psk.hex() not in full_logs
    assert str(secret_psk) not in full_logs
    assert "MISSÃO" not in full_logs


# -----------------------------------------------------------------------------
# 8. Limite de Taxa (Rate Limit) por Barco
# -----------------------------------------------------------------------------


def test_rate_limit_per_boat_stays_queued() -> None:
    """Mensagens excedentes ao rate limit por minuto permanecem na fila sem perda."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)

    # 8 mensagens para o barco b1 com limite padrão de 6 por minuto
    for i in range(8):
        db.add_outbox_row("b1", f"Mensagem {i}")

    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
        rate_limit_per_min=6,
    )

    # 1ª rodada: 6 enviadas, 2 permanecem queued
    res = outbox.run_once(limit=10)
    assert res.sent == 6
    assert res.rate_limited == 2

    # Verifica status das mensagens
    sent_msgs = [r for r in db.outbox_rows if r["status"] == "sent"]
    queued_msgs = [r for r in db.outbox_rows if r["status"] == "queued"]
    assert len(sent_msgs) == 6
    assert len(queued_msgs) == 2

    # Avança o relógio em 61 segundos: o restante deve ser liberado
    outbox.clock = lambda: NOW + 61.0
    res2 = outbox.run_once(limit=10)
    assert res2.sent == 2

    sent_total = [r for r in db.outbox_rows if r["status"] == "sent"]
    assert len(sent_total) == 8


# -----------------------------------------------------------------------------
# 9. Ausência de Gateway Virtual
# -----------------------------------------------------------------------------


def test_missing_or_inactive_virtual_gateway_marks_failed() -> None:
    """Barco sem gateway virtual ativo é marcado como failed com erro 'sem gateway virtual'."""
    db = FakeDb()
    db.add_virtual_gateway("b_inativo", "!f0000002", 0xF0000002, active=False)
    id1 = db.add_outbox_row("b_sem_gw", "Sem gateway cadastrado")
    id2 = db.add_outbox_row("b_inativo", "Com gateway inativo")

    published = []
    outbox = Outbox(
        db=db,
        publish_fn=lambda *args, **kwargs: published.append(args) or True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    res = outbox.run_once(limit=10)
    assert res.sent == 0
    assert res.failed == 2
    assert len(published) == 0

    assert len(db.marked_calls) == 2
    for call in db.marked_calls:
        assert call["status"] == "failed"
        assert call["error"] == "sem gateway virtual"


# -----------------------------------------------------------------------------
# 10. Testes do Ponto de Entrada (__main__)
# -----------------------------------------------------------------------------


def test_main_load_psk_validations() -> None:
    """Valida leitura e integridade da chave EVU em __main__."""
    # 1. Ausente -> SystemExit 2
    with pytest.raises(SystemExit) as exc:
        chat_main.load_psk({})
    assert exc.value.code == 2

    # 2. Base64 inválido -> SystemExit 2
    with pytest.raises(SystemExit) as exc:
        chat_main.load_psk({"RASTRO_EVU_PSK_B64": "!!!not_b64!!!"})
    assert exc.value.code == 2

    # 3. Tamanho incorreto (ex.: 5 bytes) -> SystemExit 2
    bad_len = base64.b64encode(b"12345").decode("ascii")
    with pytest.raises(SystemExit) as exc:
        chat_main.load_psk({"RASTRO_EVU_PSK_B64": bad_len})
    assert exc.value.code == 2

    # 4. Chave válida de 32 bytes -> Sucesso
    valid_b64 = base64.b64encode(TEST_PSK).decode("ascii")
    loaded = chat_main.load_psk({"RASTRO_EVU_PSK_B64": valid_b64})
    assert loaded == TEST_PSK


def test_mqtt_publisher_ack_handling() -> None:
    """Testa lógica de confirmação e timeout do MqttPublisher."""
    class FakeInfo:
        def __init__(self, mid: int, rc: int = 0) -> None:
            self.mid = mid
            self.rc = rc

    class FakeMqttClient:
        def __init__(self) -> None:
            self.next_mid = 1
            self.published: list[tuple[str, bytes]] = []

        def publish(self, topic: str, payload: bytes, **kwargs: Any) -> FakeInfo:
            mid = self.next_mid
            self.next_mid += 1
            self.published.append((topic, payload))
            return FakeInfo(mid=mid, rc=0)

    client = FakeMqttClient()
    pub = chat_main.MqttPublisher(client, timeout=0.5)

    # 1. Publicação confirmada via on_publish (reason_code 0)
    import threading

    def ack_thread():
        time.sleep(0.05)
        pub.on_publish(client, None, 1, 0, None)

    t = threading.Thread(target=ack_thread)
    t.start()
    assert pub.publish("topic/1", b"payload") is True
    t.join()

    # 2. Publicação recusada pelo broker (reason_code 135)
    def nack_thread():
        time.sleep(0.05)
        pub.on_publish(client, None, 2, 135, None)

    t = threading.Thread(target=nack_thread)
    t.start()
    assert pub.publish("topic/2", b"payload") is False
    t.join()

    # 3. Timeout sem resposta do broker
    assert pub.publish("topic/3", b"payload") is False


def test_mqtt_publisher_early_ack_and_no_deadlock() -> None:
    """Verifica que on_publish síncrono durante client.publish não causa deadlock (early ack)."""
    class FakeInfo:
        def __init__(self, mid: int, rc: int = 0) -> None:
            self.mid = mid
            self.rc = rc

    class EarlyAckClient:
        def __init__(self) -> None:
            self.next_mid = 1
            self.publisher: chat_main.MqttPublisher | None = None
            self.fail_reason: int = 0

        def publish(self, topic: str, payload: bytes, **kwargs: Any) -> FakeInfo:
            mid = self.next_mid
            self.next_mid += 1
            # Simula on_publish disparado antes de client.publish retornar
            if self.publisher is not None:
                self.publisher.on_publish(self, None, mid, self.fail_reason, None)
            return FakeInfo(mid=mid, rc=0)

    client = EarlyAckClient()
    pub = chat_main.MqttPublisher(client, timeout=1.0)
    client.publisher = pub

    # Early ack com sucesso (reason_code 0)
    client.fail_reason = 0
    assert pub.publish("topic/early_ok", b"payload") is True

    # Early ack com recusa (reason_code 135 >= 128)
    client.fail_reason = 135
    assert pub.publish("topic/early_fail", b"payload") is False


def test_retry_reuses_persisted_packet_id() -> None:
    """Retentativa de envio de mensagem reutiliza o packet_id previamente persistido."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("b1", "Mensagem de retentativa")

    published_packets: list[int] = []

    def mock_publish_fail(topic: str, payload: bytes, **kwargs: Any) -> bool:
        env_proto = mqtt_pb2.ServiceEnvelope.FromString(payload)
        published_packets.append(env_proto.packet.id)
        return False

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish_fail,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    # 1ª tentativa: falha na publicação
    res1 = outbox.run_once()
    assert res1.sent == 0
    assert len(published_packets) == 1
    first_pid = published_packets[0]

    # Confere que o packet_id foi persistido na linha do outbox
    row = next(r for r in db.outbox_rows if r["id"] == msg_id)
    assert row["status"] == "queued"
    assert row["packet_id"] == first_pid

    # 2ª tentativa: agora publicação tem sucesso
    def mock_publish_ok(topic: str, payload: bytes, **kwargs: Any) -> bool:
        env_proto = mqtt_pb2.ServiceEnvelope.FromString(payload)
        published_packets.append(env_proto.packet.id)
        return True

    outbox.publish_fn = mock_publish_ok
    res2 = outbox.run_once()
    assert res2.sent == 1
    assert len(published_packets) == 2
    assert published_packets[1] == first_pid  # O MESMO packet_id foi reutilizado!

    assert row["status"] == "sent"
    assert row["packet_id"] == first_pid


def test_client_disconnected_leaves_row_queued_without_publishing() -> None:
    """Cliente MQTT desconectado mantém mensagens na fila e não publica."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_outbox_row("b1", "Mensagem retida por desconexão")

    published: list[str] = []

    class DisconnectedClient:
        def is_connected(self) -> bool:
            return False

    outbox = Outbox(
        db=db,
        publish_fn=lambda topic, payload, **kwargs: published.append(topic) or True,
        psk=TEST_PSK,
        root=ROOT,
        client=DisconnectedClient(),
        clock=lambda: NOW,
    )

    res = outbox.run_once()
    assert res.sent == 0
    assert len(published) == 0
    row = db.outbox_rows[0]
    assert row["status"] == "queued"


def test_expired_rows_are_never_sent_even_if_claimed() -> None:
    """Linhas com expires_at vencido são marcadas como expired e nunca enviadas."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    row_id = db.add_outbox_row("b1", "Mensagem expirada", expires_at=NOW - 5.0)

    published: list[str] = []
    outbox = Outbox(
        db=db,
        publish_fn=lambda topic, payload, **kwargs: published.append(topic) or True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    res = outbox.run_once()
    assert res.sent == 0
    assert len(published) == 0
    row = next(r for r in db.outbox_rows if r["id"] == row_id)
    assert row["status"] == "expired"


def test_record_outgoing_failure_aborts_attempt() -> None:
    """Falha ao registrar mensagem em chat_messages aborta envio e mantém queued."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_outbox_row("b1", "Mensagem cujo registro falhará")

    published: list[str] = []

    def failing_record_outgoing(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("Erro simulado de banco na gravação de chat_messages")

    outbox = Outbox(
        db=db,
        publish_fn=lambda topic, payload, **kwargs: published.append(topic) or True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
        record_outgoing=failing_record_outgoing,
    )

    res = outbox.run_once()
    assert res.sent == 0
    assert len(published) == 0
    row = db.outbox_rows[0]
    assert row["status"] == "queued"


def test_claim_outbox_handles_optional_per_boat_limit() -> None:
    """claim_outbox passa per_boat_limit=3 se a assinatura aceitar, ou sem kwargs se não aceitar."""
    calls: list[dict[str, Any]] = []

    class DbWithPerBoatLimit(FakeDb):
        def claim_outbox(self, limit: int = 10, now: Any = None, per_boat_limit: int | None = None) -> list[dict[str, Any]]:
            calls.append({"limit": limit, "now": now, "per_boat_limit": per_boat_limit})
            return []

    db1 = DbWithPerBoatLimit()
    outbox1 = Outbox(
        db=db1,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )
    outbox1.run_once()
    assert len(calls) == 1
    assert calls[0]["per_boat_limit"] == 3

    # Agora com DB que NÃO aceita per_boat_limit
    class DbWithoutPerBoatLimit(FakeDb):
        def claim_outbox(self, limit: int = 10, now: Any = None) -> list[dict[str, Any]]:
            calls.append({"limit": limit, "now": now, "has_per_boat": False})
            return []

    db2 = DbWithoutPerBoatLimit()
    outbox2 = Outbox(
        db=db2,
        publish_fn=lambda *args, **kwargs: True,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )
    outbox2.run_once()
    assert len(calls) == 2
    assert calls[1]["has_per_boat"] is False


# -----------------------------------------------------------------------------
# 11. Testes de Regressão FIX-5 (Claims 1, 2 e 3)
# -----------------------------------------------------------------------------


def test_mqtt_publisher_message_expiry_interval_property() -> None:
    """MqttPublisher.publish repassa ou calcula MessageExpiryInterval (mínimo 1s)."""
    class CapturingClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def publish(self, topic: str, payload: bytes, **kwargs: Any) -> Any:
            self.calls.append({"topic": topic, "payload": payload, "kwargs": kwargs})
            class Info:
                mid = 1
                rc = 0
            return Info()

    client = CapturingClient()
    pub = chat_main.MqttPublisher(client, timeout=0.1)

    # Simula ack síncrono para o teste
    original_publish = pub.publish

    def fast_publish(*args: Any, **kwargs: Any) -> bool:
        # Registra resultado positivo simulado para mid=1
        with pub._lock:
            pub._results[1] = True
        return original_publish(*args, **kwargs)

    pub.publish = fast_publish

    # 1. Properties explícito passado com MessageExpiryInterval
    custom_props = Properties(PacketTypes.PUBLISH)
    custom_props.MessageExpiryInterval = 75
    pub.publish("topic/exp1", b"payload", properties=custom_props)
    assert len(client.calls) == 1
    props1 = client.calls[0]["kwargs"].get("properties")
    assert props1 is not None
    assert props1.MessageExpiryInterval == 75

    # 2. expires_at no futuro (calcula TTL restante: 150 s)
    pub.publish("topic/exp2", b"payload", expires_at=NOW + 150.0, now=NOW)
    assert len(client.calls) == 2
    props2 = client.calls[1]["kwargs"].get("properties")
    assert props2 is not None
    assert props2.MessageExpiryInterval == 150

    # 3. expires_at prestes a expirar (< 1s restante): garante mínimo de 1s
    pub.publish("topic/exp3", b"payload", expires_at=NOW + 0.3, now=NOW)
    assert len(client.calls) == 3
    props3 = client.calls[2]["kwargs"].get("properties")
    assert props3 is not None
    assert props3.MessageExpiryInterval == 1


def test_outbox_publish_passes_message_expiry_interval_via_properties() -> None:
    """Outbox.run_once passa Properties(PacketTypes.PUBLISH) com MessageExpiryInterval."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_outbox_row("b1", "Mensagem com TTL", expires_at=NOW + 300.0)

    published_calls: list[dict[str, Any]] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_calls.append({"topic": topic, "payload": payload, "kwargs": kwargs})
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    res = outbox.run_once()
    assert res.sent == 1
    assert len(published_calls) == 1

    call = published_calls[0]
    props = call["kwargs"].get("properties")
    assert props is not None
    assert isinstance(props, Properties)
    assert props.MessageExpiryInterval == 300


def test_outbox_publish_enforces_minimum_one_second_expiry_interval() -> None:
    """Outbox.run_once garante MessageExpiryInterval >= 1 mesmo se TTL restante < 1s."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    db.add_outbox_row("b1", "Mensagem quase no fim", expires_at=NOW + 0.5)

    published_calls: list[dict[str, Any]] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        published_calls.append({"topic": topic, "payload": payload, "kwargs": kwargs})
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: NOW,
    )

    res = outbox.run_once()
    assert res.sent == 1
    assert len(published_calls) == 1

    props = published_calls[0]["kwargs"].get("properties")
    assert props is not None
    assert props.MessageExpiryInterval == 1


def test_outbox_refreshes_clock_between_rows_and_drops_expired() -> None:
    """Outbox atualiza relógio a cada linha e re-checa expiração (Claim 2)."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)

    # Duas mensagens: m1 expira em +10s, m2 expira em +2s
    id1 = db.add_outbox_row("b1", "Mensagem 1", expires_at=NOW + 10.0)
    id2 = db.add_outbox_row("b1", "Mensagem 2", expires_at=NOW + 2.0)

    current_time = NOW

    def simulated_clock() -> float:
        return current_time

    published_topics: list[str] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        nonlocal current_time
        published_topics.append(topic)
        # O envio da mensagem 1 demora 3 segundos, avançando o relógio além de expires_at da mensagem 2
        current_time += 3.0
        return True

    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=simulated_clock,
    )

    res = outbox.run_once()
    # Apenas a mensagem 1 é enviada; a mensagem 2 expira antes de ser publicada
    assert res.sent == 1
    assert len(published_topics) == 1

    r1 = next(r for r in db.outbox_rows if r["id"] == id1)
    r2 = next(r for r in db.outbox_rows if r["id"] == id2)
    assert r1["status"] == "sent"
    assert r2["status"] == "expired"


def test_outbox_rechecks_expiry_immediately_before_publication() -> None:
    """Outbox re-checa expiração imediatamente antes de publicar e não envia se expirou."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("b1", "Mensagem limítrofe", expires_at=NOW + 2.0)

    current_time = NOW

    def advancing_clock() -> float:
        return current_time

    published: list[str] = []

    def mock_record_outgoing(boat_id: str, from_num: int, packet_id: int, text: str) -> bool:
        nonlocal current_time
        # Simula atraso na gravação de chat_messages de forma que o tempo ultrapasse expires_at
        current_time = NOW + 3.0
        return True

    outbox = Outbox(
        db=db,
        publish_fn=lambda topic, payload, **kwargs: published.append(topic) or True,
        psk=TEST_PSK,
        root=ROOT,
        clock=advancing_clock,
        record_outgoing=mock_record_outgoing,
    )

    res = outbox.run_once()
    assert res.sent == 0
    assert len(published) == 0

    row = next(r for r in db.outbox_rows if r["id"] == msg_id)
    assert row["status"] == "expired"


def test_rate_accounting_before_db_mark_and_same_packet_id_on_retry() -> None:
    """Rate accounting ocorre ANTES do mark_outbox; em falha no DB mantém o mesmo packet_id (Claim 3)."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("b1", "Mensagem sujeita a falha de DB mark")

    fail_sent_mark = True
    original_mark = db.mark_outbox

    def flaky_mark_outbox(outbox_id: int, status: str, packet_id: int | None = None, error: str | None = None) -> bool:
        if status == "sent" and fail_sent_mark:
            raise RuntimeError("Falha de conexão com Postgres na confirmação de sent")
        return original_mark(outbox_id, status, packet_id=packet_id, error=error)

    db.mark_outbox = flaky_mark_outbox

    published_packets: list[int] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        env_proto = mqtt_pb2.ServiceEnvelope.FromString(payload)
        published_packets.append(env_proto.packet.id)
        return True

    current_time = NOW
    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: current_time,
        rate_limit_per_min=1,  # Limite rígido de 1 mensagem por minuto
    )

    # 1ª execução: publish confirmado, mas mark_outbox('sent') levanta exceção
    res1 = outbox.run_once()
    assert res1.sent == 0
    assert len(published_packets) == 1
    first_packet_id = published_packets[0]

    # Contabilização de rate limit OCORREU antes da falha no banco!
    assert len(outbox._boat_sent_timestamps["b1"]) == 1
    assert outbox._boat_sent_timestamps["b1"][0] == NOW

    # Mensagem permanece queued na fila com seu packet_id gravado
    row = next(r for r in db.outbox_rows if r["id"] == msg_id)
    assert row["status"] == "queued"
    assert row["packet_id"] == first_packet_id

    # Tentativa imediata no mesmo minuto deve ser BLOQUEADA pelo rate limiter
    res_blocked = outbox.run_once()
    assert res_blocked.rate_limited == 1
    assert len(published_packets) == 1  # Não enviou de novo

    # Avança o relógio além da janela de 60 segundos
    current_time = NOW + 61.0

    # Banco de dados volta a responder
    fail_sent_mark = False

    # 2ª execução (retentativa): publicação e atualização têm sucesso
    res2 = outbox.run_once()
    assert res2.sent == 1
    assert len(published_packets) == 2

    # CRÍTICO: a retentativa enviou EXATAMENTE o mesmo packet_id (nunca um novo)!
    assert published_packets[1] == first_packet_id
    assert row["status"] == "sent"
    assert row["packet_id"] == first_packet_id


def test_mark_outbox_returning_false_retains_packet_id() -> None:
    """Se mark_outbox('sent') retornar False, packet_id é preservado e reusado na retentativa."""
    db = FakeDb()
    db.add_virtual_gateway("b1", "!f0000001", 0xF0000001, active=True)
    msg_id = db.add_outbox_row("b1", "Mensagem com mark_outbox retornando False")

    fail_sent = True
    original_mark = db.mark_outbox

    def returning_false_mark(outbox_id: int, status: str, packet_id: int | None = None, error: str | None = None) -> bool:
        if status == "sent" and fail_sent:
            return False
        return original_mark(outbox_id, status, packet_id=packet_id, error=error)

    db.mark_outbox = returning_false_mark

    published_packets: list[int] = []

    def mock_publish(topic: str, payload: bytes, **kwargs: Any) -> bool:
        env_proto = mqtt_pb2.ServiceEnvelope.FromString(payload)
        published_packets.append(env_proto.packet.id)
        return True

    current_time = NOW
    outbox = Outbox(
        db=db,
        publish_fn=mock_publish,
        psk=TEST_PSK,
        root=ROOT,
        clock=lambda: current_time,
        rate_limit_per_min=5,
    )

    res1 = outbox.run_once()
    assert res1.sent == 0
    assert len(published_packets) == 1
    first_pid = published_packets[0]

    # Rate accounting registrado
    assert len(outbox._boat_sent_timestamps["b1"]) == 1

    # Permite sucesso na próxima rodada
    fail_sent = False
    current_time = NOW + 1.0

    res2 = outbox.run_once()
    assert res2.sent == 1
    assert len(published_packets) == 2
    # O mesmo packet_id é reutilizado
    assert published_packets[1] == first_pid

