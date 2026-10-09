"""Testes da decodificação pura de ServiceEnvelope (WP-A).

Monta envelopes reais (MeshPacket cifrado + protobuf de domínio) e verifica o
contrato de `decode_envelope`: nunca levanta, metadados completos e validação
de relógio com fallback sinalizado. Dados 100% sintéticos.
"""
from __future__ import annotations

import random

import pytest
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, telemetry_pb2

from rastro_gateway.native import crypto, envelope
from rastro_gateway.native.model import DecodedEnvelope

TEST_PSK = bytes(range(32))
NOW = 1_760_000_000

ROOT = "univaja/mesh"
CANAL = "EVU"
GATEWAY_ID = "!a0000001"
FROM = 0x1A2B3C4D
TO = 0xFFFFFFFF
PACKET_ID = 0x11223344
RX_TIME = NOW - 10


def _montar_envelope(
    data: mesh_pb2.Data,
    *,
    from_num: int = FROM,
    to_num: int = TO,
    packet_id: int = PACKET_ID,
    rx_time: int = RX_TIME,
    rx_snr: float = 9.5,
    rx_rssi: int = -109,
    hop_limit: int = 3,
    pki_encrypted: bool = False,
    gateway_id_env: str = GATEWAY_ID,
    canal: str = CANAL,
    psk: bytes = TEST_PSK,
) -> bytes:
    """MeshPacket cifrado (AES-CTR) dentro de ServiceEnvelope → bytes."""
    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", from_num)
    mp.to = to_num
    mp.id = packet_id
    mp.rx_time = rx_time
    mp.rx_snr = rx_snr
    mp.rx_rssi = rx_rssi
    mp.hop_limit = hop_limit
    mp.pki_encrypted = pki_encrypted
    mp.encrypted = crypto.crypt(
        crypto.expand_psk(psk), packet_id, from_num, data.SerializeToString()
    )
    env = mqtt_pb2.ServiceEnvelope(packet=mp, channel_id=canal, gateway_id=gateway_id_env)
    return env.SerializeToString()


def _decodificar(
    payload: bytes,
    *,
    channel: str = CANAL,
    gateway_id_topic: str = GATEWAY_ID,
    psk: bytes = TEST_PSK,
    now: float = NOW,
) -> DecodedEnvelope:
    return envelope.decode_envelope(
        payload, channel=channel, gateway_id_topic=gateway_id_topic, psk=psk, now=now
    )


def _envelope_posicao(device_time: int, **kwargs) -> bytes:
    pos = mesh_pb2.Position(
        latitude_i=-23_540_000,
        longitude_i=-46_630_000,
        altitude=120,
        sats_in_view=8,
        time=device_time,
    )
    return _montar_envelope(mesh_pb2.Data(portnum=3, payload=pos.SerializeToString()), **kwargs)


# ---------------------------------------------------------------- parse_topic


def test_parse_topic_extrai_canal_e_gateway() -> None:
    assert envelope.parse_topic(ROOT, f"{ROOT}/2/e/EVU/!a0000001") == ("EVU", "!a0000001")


def test_parse_topic_prefixo_errado_e_niveis_invalidos() -> None:
    assert envelope.parse_topic(ROOT, "outro/root/2/e/EVU/!a0000001") is None
    assert envelope.parse_topic(ROOT, f"{ROOT}/2/e/EVU") is None  # sem gateway
    assert envelope.parse_topic(ROOT, f"{ROOT}/2/e/EVU/!a0000001/extra") is None
    assert envelope.parse_topic(ROOT, f"{ROOT}/2/e//!a0000001") is None


# ------------------------------------------------------------------- posição


def test_posicao_tempo_valido_do_dispositivo_e_metadados() -> None:
    dec = _decodificar(_envelope_posicao(NOW - 60))
    assert dec.kind == "position"
    assert dec.position is not None
    assert dec.position.lat_i == -23_540_000
    assert dec.position.lon_i == -46_630_000
    assert dec.position.altitude_m == 120
    assert dec.position.sats == 8
    assert dec.position.time == NOW - 60
    assert dec.position.time_source == "device"
    assert dec.position.time_flag is None
    # Metadados do MeshPacket e do tópico.
    assert dec.channel == CANAL
    assert dec.gateway_id == GATEWAY_ID
    assert dec.gateway_num == 0xA0000001
    assert dec.from_num == FROM
    assert dec.to_num == TO
    assert dec.packet_id == PACKET_ID
    assert dec.rx_time == RX_TIME
    assert dec.hop_limit == 3
    assert dec.snr == pytest.approx(9.5)
    assert dec.rssi == -109
    assert dec.portnum == 3


@pytest.mark.parametrize(
    ("device_time", "flag"),
    [
        (0, "invalid_zero"),
        (31_536_000, "invalid_past"),  # 1971-01-01
        (NOW + 4000, "invalid_future"),
    ],
)
def test_posicao_tempo_invalido_cai_para_rx_time(device_time: int, flag: str) -> None:
    dec = _decodificar(_envelope_posicao(device_time))
    assert dec.kind == "position"
    assert dec.position is not None
    assert dec.position.time_flag == flag
    assert dec.position.time_source == "gateway"
    assert dec.position.time == RX_TIME


def test_posicao_sem_rx_time_cai_para_agora() -> None:
    dec = _decodificar(_envelope_posicao(0, rx_time=0))
    assert dec.kind == "position"
    assert dec.position is not None
    assert dec.position.time == NOW
    assert dec.position.time_source == "gateway"
    assert dec.position.time_flag == "invalid_zero"
    assert dec.rx_time is None


def test_posicao_rx_time_fora_da_janela_cai_para_agora() -> None:
    dec = _decodificar(_envelope_posicao(0, rx_time=NOW - 8 * 24 * 3600))
    assert dec.kind == "position"
    assert dec.position is not None
    assert dec.position.time == NOW
    assert dec.position.time_source == "gateway"


# ---------------------------------------------------------------- telemetria


def test_telemetria_device_metrics_mapeados() -> None:
    tel = telemetry_pb2.Telemetry(
        time=NOW - 120,
        device_metrics=telemetry_pb2.DeviceMetrics(
            battery_level=76,
            voltage=3.87,
            channel_utilization=12.5,
            air_util_tx=0.21,
            uptime_seconds=86400,
        ),
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=67, payload=tel.SerializeToString())))
    assert dec.kind == "telemetry"
    t = dec.telemetry
    assert t is not None
    assert t.battery_level == pytest.approx(76)
    assert t.voltage == pytest.approx(3.87)
    assert t.channel_util == pytest.approx(12.5)
    assert t.air_util_tx == pytest.approx(0.21)
    assert t.uptime_s == 86400
    assert t.time == NOW - 120
    assert t.time_source == "device"
    assert t.time_flag is None


def test_telemetria_sem_device_metrics_vira_unhandled() -> None:
    tel = telemetry_pb2.Telemetry(
        environment_metrics=telemetry_pb2.EnvironmentMetrics(temperature=25.0)
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=67, payload=tel.SerializeToString())))
    assert dec.kind == "unhandled"
    assert dec.telemetry is None


def test_telemetria_somente_tensao_ausentes_viram_none() -> None:
    """FIX-2: campo ausente de DeviceMetrics vira None, não 0 (pacote só com tensão)."""
    tel = telemetry_pb2.Telemetry(
        time=NOW - 60,
        device_metrics=telemetry_pb2.DeviceMetrics(voltage=3.9),
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=67, payload=tel.SerializeToString())))
    assert dec.kind == "telemetry"
    t = dec.telemetry
    assert t is not None
    assert t.voltage == pytest.approx(3.9)
    assert t.battery_level is None
    assert t.channel_util is None
    assert t.air_util_tx is None
    assert t.uptime_s is None
    assert t.time == NOW - 60
    assert t.time_source == "device"


def test_telemetria_zero_real_permanece_zero() -> None:
    """FIX-2: zero REAL (campo presente com 0) não vira None — HasField distingue."""
    tel = telemetry_pb2.Telemetry(
        time=NOW - 60,
        device_metrics=telemetry_pb2.DeviceMetrics(battery_level=0, voltage=0.0),
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=67, payload=tel.SerializeToString())))
    assert dec.kind == "telemetry"
    t = dec.telemetry
    assert t is not None
    assert t.battery_level == 0
    assert t.voltage == 0.0


# ------------------------------------------------------------------ nodeinfo


def test_nodeinfo_usuario_mapeado() -> None:
    usuario = mesh_pb2.User(
        id="!f0000001", long_name="Rastro", short_name="RSTR", hw_model=mesh_pb2.HardwareModel.PRIVATE_HW
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=4, payload=usuario.SerializeToString())))
    assert dec.kind == "nodeinfo"
    ni = dec.nodeinfo
    assert ni is not None
    assert ni.user_id == "!f0000001"
    assert ni.long_name == "Rastro"
    assert ni.short_name == "RSTR"
    assert ni.hw_model == "PRIVATE_HW"
    assert dec.portnum == 4


# ---------------------------------------------------------------------- texto


def test_texto_comum_sem_alerta() -> None:
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=1, payload="oi".encode())))
    assert dec.kind == "text"
    assert dec.text is not None
    assert dec.text.text == "oi"
    assert dec.text.is_alert is False


def test_texto_com_sino_vira_alerta() -> None:
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=1, payload="chegamos\x07".encode())))
    assert dec.kind == "text"
    assert dec.text is not None
    assert dec.text.is_alert is True


def test_texto_com_acento_casa_palavra_ajuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RASTRO_CHAT_HELP_KEYWORDS", raising=False)
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=1, payload="Ajúda".encode())))
    assert dec.kind == "text"
    assert dec.text is not None
    assert dec.text.is_alert is True


def test_palavras_ajuda_sobrescritas_pelo_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RASTRO_CHAT_HELP_KEYWORDS", "emergencia")
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=1, payload="socorro".encode())))
    assert dec.kind == "text"
    assert dec.text is not None
    assert dec.text.is_alert is False  # fora da lista configurada


# ------------------------------------------------------------------ unhandled


def test_portnum_desconhecido_vira_unhandled() -> None:
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=999, payload=b"xyz")))
    assert dec.kind == "unhandled"
    assert dec.portnum == 999


# --------------------------------------------------------------------- opaco


def test_canal_pki_vira_opaco_sem_decifrar() -> None:
    dec = _decodificar(_envelope_posicao(NOW - 60), channel="PKI")
    assert dec.kind == "opaque"
    assert dec.position is None
    assert dec.from_num == FROM  # metadados do cabeçalho seguem disponíveis


def test_pki_encrypted_vira_opaco() -> None:
    dec = _decodificar(_envelope_posicao(NOW - 60, pki_encrypted=True))
    assert dec.kind == "opaque"


# ------------------------------------------------- chave errada / lixo / corte


def test_chave_errada_vira_undecryptable() -> None:
    # Combinação (packet_id, from) verificada determinísticamente: o lixo
    # decifrado com a chave errada NÃO analisa como protobuf Data.
    errada = bytes(b ^ 0xFF for b in TEST_PSK)
    payload = _montar_envelope(
        mesh_pb2.Data(portnum=1, payload="socorro".encode()),
        from_num=0xA0000001,
        packet_id=0x11223344,
    )
    dec = _decodificar(payload, psk=errada)
    assert dec.kind == "undecryptable"
    assert dec.reason == "protobuf_invalido"


def test_psk_tamanho_invalido_vira_malformed() -> None:
    payload = _montar_envelope(mesh_pb2.Data(portnum=1, payload="oi".encode()))
    dec = _decodificar(payload, psk=b"\x01\x02")
    assert dec.kind == "malformed"
    assert dec.reason == "psk_invalido"


def test_bytes_aleatorios_viram_malformed() -> None:
    lixo = b"\x00\x09lixo\x01\xff\xff\xff\x02\x03quase"
    dec = _decodificar(lixo)
    assert dec.kind == "malformed"
    assert dec.reason == "envelope_invalido"


def test_envelope_truncado_vira_malformed() -> None:
    completo = _envelope_posicao(NOW - 60)
    dec = _decodificar(completo[: len(completo) // 2])
    assert dec.kind == "malformed"
    assert dec.reason == "envelope_invalido"


# ------------------------------------------------------- pacote já decodificado


def test_pacote_decoded_sem_cifra_aceito_como_esta() -> None:
    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", FROM)
    mp.to = TO
    mp.id = PACKET_ID
    mp.rx_time = RX_TIME
    mp.rx_snr = 4.0
    mp.rx_rssi = -80
    mp.hop_limit = 2
    mp.decoded.CopyFrom(mesh_pb2.Data(portnum=1, payload="sem cifra".encode()))
    payload = mqtt_pb2.ServiceEnvelope(packet=mp, channel_id=CANAL, gateway_id=GATEWAY_ID).SerializeToString()

    dec = _decodificar(payload)
    assert dec.kind == "text"
    assert dec.text is not None
    assert dec.text.text == "sem cifra"
    assert dec.from_num == FROM
    assert dec.packet_id == PACKET_ID


# --------------------------------------------------------------- gateway ids


def test_gateway_do_topico_vence_envelope() -> None:
    payload = _montar_envelope(mesh_pb2.Data(portnum=1, payload=b"x"), gateway_id_env="!b0000002")
    dec = _decodificar(payload, gateway_id_topic="!c0000003")
    assert dec.gateway_id == "!c0000003"
    assert dec.gateway_num == 0xC0000003


def test_gateway_cai_para_campo_do_envelope() -> None:
    payload = _montar_envelope(mesh_pb2.Data(portnum=1, payload=b"x"), gateway_id_env="!b0000002")
    dec = _decodificar(payload, gateway_id_topic="")
    assert dec.gateway_id == "!b0000002"
    assert dec.gateway_num == 0xB0000002


def test_gateway_sem_prefixo_bang_nao_tem_num() -> None:
    payload = _montar_envelope(mesh_pb2.Data(portnum=1, payload=b"x"))
    dec = _decodificar(payload, gateway_id_topic="rastrogw01")
    assert dec.gateway_id == "rastrogw01"
    assert dec.gateway_num is None


# ----------------------------------------------------------------- round-trip


def test_round_trip_posicao_e_texto() -> None:
    pos = mesh_pb2.Position(latitude_i=12_345_678, longitude_i=-87_654_321, altitude=7, sats_in_view=5, time=NOW - 30)
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=3, payload=pos.SerializeToString())))
    assert dec.kind == "position"
    assert dec.position is not None
    assert (dec.position.lat_i, dec.position.lon_i) == (12_345_678, -87_654_321)
    assert dec.position.altitude_m == 7
    assert dec.position.sats == 5
    assert dec.position.time == NOW - 30

    dec_txt = _decodificar(
        _montar_envelope(
            mesh_pb2.Data(portnum=1, payload="tudo ok".encode()),
            packet_id=0x55667788,
        )
    )
    assert dec_txt.kind == "text"
    assert dec_txt.text is not None
    assert dec_txt.text.text == "tudo ok"
    assert dec_txt.text.is_alert is False
    assert dec_txt.packet_id == 0x55667788


# ------------------------------------------------------------- nunca levanta


def test_fuzz_decode_nunca_levanta_e_kind_valido() -> None:
    rng = random.Random(20261005)
    permitidos = {
        "position",
        "telemetry",
        "nodeinfo",
        "text",
        "unhandled",
        "opaque",
        "undecryptable",
        "malformed",
    }
    for _ in range(200):
        dados = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 256)))
        dec = _decodificar(dados)
        assert isinstance(dec, DecodedEnvelope)
        assert dec.kind in permitidos


# ------------------------------------------------------- qualidade do fix (migração 03)


def test_qualidade_do_firmware_decodificada_e_zero_vira_none() -> None:
    pos = mesh_pb2.Position(
        latitude_i=-23_540_000,
        longitude_i=-46_630_000,
        time=NOW - 60,
        PDOP=150,
        HDOP=90,
        ground_speed=3,
        ground_track=12_345_000,
        precision_bits=16,
    )
    dec = _decodificar(_montar_envelope(mesh_pb2.Data(portnum=3, payload=pos.SerializeToString())))
    assert dec.position is not None
    assert dec.position.pdop == pytest.approx(1.5)
    assert dec.position.hdop == pytest.approx(0.9)
    assert dec.position.ground_speed_ms == pytest.approx(3.0)
    assert dec.position.ground_track_deg == pytest.approx(123.45)
    assert dec.position.precision_bits == 16

    dec_sem = _decodificar(_envelope_posicao(NOW - 60))  # campos de qualidade ausentes (0)
    assert dec_sem.position is not None
    assert dec_sem.position.pdop is None
    assert dec_sem.position.hdop is None
    assert dec_sem.position.ground_speed_ms is None
    assert dec_sem.position.ground_track_deg is None
    assert dec_sem.position.precision_bits is None
    assert dec_sem.position.sats == 8  # campos antigos seguem intactos
