"""Decodificação pura de envelopes MQTT nativos do Meshtastic (sem I/O).

Ver docs/native-ingest-design.md (§1 Protocolo, WP-A). `decode_envelope` nunca
levanta: qualquer entrada de bytes resulta em um `DecodedEnvelope`; erro vira
`kind="malformed"` com código de motivo fixo — nunca conteúdo do payload.
"""
from __future__ import annotations

import os
import unicodedata

from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, telemetry_pb2

from rastro_gateway.native import crypto
from rastro_gateway.native.model import (
    DecodedEnvelope,
    NodeInfo,
    PositionFix,
    TelemetryFix,
    TextMessage,
)

# Portnums do domínio (meshtastic.protobuf.portnums_pb2.PortNum).
_PORTNUM_TEXTO = 1  # TEXT_MESSAGE_APP
_PORTNUM_POSICAO = 3  # POSITION_APP
_PORTNUM_NODEINFO = 4  # NODEINFO_APP
_PORTNUM_TELEMETRIA = 67  # TELEMETRY_APP

# Validação do relógio do dispositivo (design §1).
_EPOCH_MINIMO = 1_600_000_000
_TOLERANCIA_FUTURO_S = 300
_JANELA_GATEWAY_S = 7 * 24 * 3600

# Alerta de ajuda no chat: palavras sem acento, caixa baixa, lidas do ambiente
# a cada chamada (testes sobrescrevem sem reiniciar o processo).
_ENV_PALAVRAS_AJUDA = "RASTRO_CHAT_HELP_KEYWORDS"
_PALAVRAS_AJUDA_PADRAO = "ajuda,socorro,sos,help,emergencia,urgente"


def parse_topic(root: str, topic: str) -> tuple[str, str] | None:
    """Extrai (canal, gateway_id) de `<root>/2/e/<canal>/<gateway_id>`; None se não casa."""
    prefixo = root.strip("/") + "/2/e/"
    if not topic.startswith(prefixo):
        return None
    partes = topic[len(prefixo) :].split("/")
    if len(partes) != 2 or not partes[0] or not partes[1]:
        return None
    return partes[0], partes[1]


def decode_envelope(
    payload: bytes,
    *,
    channel: str,
    gateway_id_topic: str,
    psk: bytes,
    now: float,
) -> DecodedEnvelope:
    """Decodifica o ServiceEnvelope bruto de um uplink nativo. Nunca levanta.

    O gateway informado no tópico tem precedência sobre o campo do envelope.
    Pacote com `pki_encrypted` ou canal `PKI` vira `opaque` (não decifra).
    """
    try:
        return _decodificar(payload, channel=channel, gateway_id_topic=gateway_id_topic, psk=psk, now=now)
    except Exception:  # noqa: BLE001 — contrato WP-A: nunca propagar exceção
        return DecodedEnvelope(kind="malformed", channel=channel, reason="erro_interno")


def _decodificar(
    payload: bytes,
    *,
    channel: str,
    gateway_id_topic: str,
    psk: bytes,
    now: float,
) -> DecodedEnvelope:
    try:
        envelope = mqtt_pb2.ServiceEnvelope.FromString(bytes(payload))
    except Exception:  # protobuf aceito só se decodificar inteiro
        return DecodedEnvelope(
            kind="malformed",
            channel=channel,
            gateway_id=gateway_id_topic,
            gateway_num=_gateway_num(gateway_id_topic),
            reason="envelope_invalido",
        )

    # Tópico vence; campo do envelope é apenas fallback.
    gateway_id = gateway_id_topic if gateway_id_topic else envelope.gateway_id
    packet = envelope.packet
    dec = DecodedEnvelope(
        kind="malformed",  # sobrescrito abaixo; base segura se nada for reconhecido
        channel=channel,
        gateway_id=gateway_id,
        gateway_num=_gateway_num(gateway_id),
    )
    _preencher_meta(dec, packet)

    if packet.pki_encrypted or channel == "PKI":
        dec.kind = "opaque"
        return dec

    if packet.HasField("decoded"):
        data = packet.decoded
    elif packet.HasField("encrypted") and packet.encrypted:
        data = _decifrar(dec, packet, psk)
        if data is None:
            return dec  # dec.reason já preenchido (psk_invalido / protobuf_invalido)
    else:
        dec.reason = "pacote_sem_payload"
        return dec

    dec.portnum = _zero_para_none(data.portnum)
    _decodificar_conteudo(dec, packet, data, now)
    return dec


def _decifrar(dec: DecodedEnvelope, packet: mesh_pb2.MeshPacket, psk: bytes) -> mesh_pb2.Data | None:
    """Expande o PSK, decifra `encrypted` e analisa o `Data`; None em falha (motivo em `dec`)."""
    try:
        chave = crypto.expand_psk(psk)
    except ValueError:
        dec.reason = "psk_invalido"
        return None
    try:
        texto_plano = crypto.crypt(chave, packet.id, getattr(packet, "from", 0), packet.encrypted)
        return mesh_pb2.Data.FromString(texto_plano)
    except DecodeError:
        # Chave errada quase sempre produz protobuf inválido (design WP-A).
        dec.kind = "undecryptable"
        dec.reason = "protobuf_invalido"
        return None


def _decodificar_conteudo(
    dec: DecodedEnvelope,
    packet: mesh_pb2.MeshPacket,
    data: mesh_pb2.Data,
    now: float,
) -> None:
    """Despacha pelo `Data.portnum` e preenche a variante de domínio."""
    payload = bytes(data.payload)
    portnum = data.portnum

    if portnum == _PORTNUM_TEXTO:
        texto = payload.decode("utf-8", errors="replace")
        dec.kind = "text"
        dec.text = TextMessage(text=texto, is_alert="\x07" in texto or _casa_ajuda(texto))
        return

    if portnum == _PORTNUM_POSICAO:
        try:
            pos = mesh_pb2.Position.FromString(payload)
        except DecodeError:
            dec.reason = "payload_invalido"
            return
        tempo, origem, flag = _resolver_tempo(pos.time, dec.rx_time, now)
        dec.kind = "position"
        dec.position = PositionFix(
            lat_i=pos.latitude_i,
            lon_i=pos.longitude_i,
            altitude_m=pos.altitude,
            sats=pos.sats_in_view,
            time=tempo,
            time_source=origem,
            time_flag=flag,
        )
        return

    if portnum == _PORTNUM_NODEINFO:
        try:
            usuario = mesh_pb2.User.FromString(payload)
        except DecodeError:
            dec.reason = "payload_invalido"
            return
        dec.kind = "nodeinfo"
        dec.nodeinfo = NodeInfo(
            user_id=usuario.id,
            long_name=usuario.long_name,
            short_name=usuario.short_name,
            hw_model=_hw_model(usuario),
        )
        return

    if portnum == _PORTNUM_TELEMETRIA:
        try:
            telemetria = telemetry_pb2.Telemetry.FromString(payload)
        except DecodeError:
            dec.reason = "payload_invalido"
            return
        if not telemetria.HasField("device_metrics"):
            dec.kind = "unhandled"  # design §1: só device_metrics entra no domínio
            return
        metricas = telemetria.device_metrics
        tempo, origem, flag = _resolver_tempo(telemetria.time, dec.rx_time, now)
        dec.kind = "telemetry"
        dec.telemetry = TelemetryFix(
            time=tempo,
            time_source=origem,
            time_flag=flag,
            battery_level=_campo_metrica(metricas, "battery_level"),
            voltage=_campo_metrica(metricas, "voltage"),
            channel_util=_campo_metrica(metricas, "channel_utilization"),
            air_util_tx=_campo_metrica(metricas, "air_util_tx"),
            uptime_s=_campo_metrica(metricas, "uptime_seconds"),
        )
        return

    dec.kind = "unhandled"


def _campo_metrica(metricas: "telemetry_pb2.DeviceMetrics", campo: str) -> float | int | None:
    """Valor de um subcampo de DeviceMetrics; ausente → None (presença explícita).

    Sem ``HasField``, pacote só com tensão (ex.: DeviceMetrics{voltage=3.9})
    viraria bateria=0 — o padrão proto3 do scalar — e o upsert do node_power
    sobrescreveria bateria conhecida com 0.
    """
    return getattr(metricas, campo) if metricas.HasField(campo) else None


def _resolver_tempo(device_time: int, rx_time: int | None, now: float) -> tuple[int, str, str | None]:
    """Valida o relógio do dispositivo e resolve o horário efetivo (design §1).

    Retorna (tempo, time_source, time_flag). Fallback sinalizado: rx_time se
    estiver na janela [agora−7d, agora+300s], senão `agora`; time_source='gateway'.
    """
    if device_time == 0:
        flag = "invalid_zero"
    elif device_time < _EPOCH_MINIMO:
        flag = "invalid_past"
    elif device_time > now + _TOLERANCIA_FUTURO_S:
        flag = "invalid_future"
    else:
        return int(device_time), "device", None
    if rx_time is not None and now - _JANELA_GATEWAY_S <= rx_time <= now + _TOLERANCIA_FUTURO_S:
        return int(rx_time), "gateway", flag
    return int(now), "gateway", flag


def _preencher_meta(dec: DecodedEnvelope, packet: mesh_pb2.MeshPacket) -> None:
    """Copia metadados do MeshPacket; 0 = ausente (proto), exceto snr (bruto)."""
    dec.from_num = _zero_para_none(getattr(packet, "from", 0))
    dec.to_num = _zero_para_none(packet.to)
    dec.packet_id = _zero_para_none(packet.id)
    dec.rx_time = _zero_para_none(packet.rx_time)
    dec.hop_limit = _zero_para_none(packet.hop_limit)
    dec.snr = packet.rx_snr
    dec.rssi = _zero_para_none(packet.rx_rssi)


def _zero_para_none(valor: int) -> int | None:
    return valor if valor else None


def _gateway_num(gateway_id: str) -> int | None:
    """`!xxxxxxxx` → inteiro (hex); outros formatos → None."""
    if not gateway_id.startswith("!"):
        return None
    try:
        return int(gateway_id[1:], 16)
    except ValueError:
        return None


def _hw_model(usuario: mesh_pb2.User) -> str | None:
    valor = usuario.hw_model
    if not valor:  # UNSET (0)
        return None
    try:
        return mesh_pb2.HardwareModel.Name(valor)
    except ValueError:  # valor de enumeração desconhecido (firmware mais novo)
        return None


def _sem_acento_minusculo(texto: str) -> str:
    decomposta = unicodedata.normalize("NFD", texto)
    return "".join(caractere for caractere in decomposta if not unicodedata.combining(caractere)).lower()


def _casa_ajuda(texto: str) -> bool:
    """Casa (substring, sem acento, caixa baixa) com as palavras-chave de ajuda."""
    brutas = os.environ.get(_ENV_PALAVRAS_AJUDA) or _PALAVRAS_AJUDA_PADRAO
    normalizado = _sem_acento_minusculo(texto)
    for bruta in brutas.split(","):
        palavra = _sem_acento_minusculo(bruta).strip()
        if palavra and palavra in normalizado:
            return True
    return False
