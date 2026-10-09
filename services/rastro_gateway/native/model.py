"""Tipos compartilhados da decodificação nativa (sem I/O). Ver docs/native-ingest-design.md."""
from __future__ import annotations

from dataclasses import dataclass, field

# kind: 'position' | 'telemetry' | 'nodeinfo' | 'text' | 'unhandled' | 'opaque' |
#       'undecryptable' | 'malformed'


@dataclass
class PositionFix:
    lat_i: int
    lon_i: int
    altitude_m: int | None
    sats: int | None
    time: int  # epoch s efetivo (válido ou fallback)
    time_source: str  # 'device' | 'gateway'
    time_flag: str | None  # None | 'invalid_zero' | 'invalid_past' | 'invalid_future'
    # qualidade do fix do firmware (None = ausente ou 0 no protobuf)
    pdop: float | None = None
    hdop: float | None = None
    ground_speed_ms: float | None = None
    ground_track_deg: float | None = None
    precision_bits: int | None = None


@dataclass
class TelemetryFix:
    time: int
    time_source: str
    time_flag: str | None
    battery_level: float | None = None
    voltage: float | None = None
    channel_util: float | None = None
    air_util_tx: float | None = None
    uptime_s: int | None = None


@dataclass
class NodeInfo:
    user_id: str
    long_name: str
    short_name: str
    hw_model: str | None = None


@dataclass
class TextMessage:
    text: str
    is_alert: bool


@dataclass
class DecodedEnvelope:
    kind: str
    channel: str = ""
    gateway_id: str = ""  # '!xxxxxxxx' (do tópico/envelope)
    gateway_num: int | None = None
    from_num: int | None = None
    to_num: int | None = None
    packet_id: int | None = None
    rx_time: int | None = None  # MeshPacket.rx_time (observação no gateway), 0/None = ausente
    hop_limit: int | None = None
    snr: float | None = None
    rssi: int | None = None
    portnum: int | None = None
    position: PositionFix | None = None
    telemetry: TelemetryFix | None = None
    nodeinfo: NodeInfo | None = None
    text: TextMessage | None = None
    reason: str = ""  # só para malformed/undecryptable; sem conteúdo do payload
    extra: dict = field(default_factory=dict)
