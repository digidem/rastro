"""Contrato de payload (schema 1) da ponte Meshtastic → MQTT → PostgreSQL.

Tópicos: ``<prefix>/positions/<hexid>``, ``<prefix>/telemetry/<hexid>`` e
``<prefix>/status/gateway`` — hexid = node_id sem o ``!`` inicial, minúsculo.

SENSÍVEL: payloads de posição carregam coordenadas. Nunca logue o payload;
logue tópico e contagens (ver AGENTS.md).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

SCHEMA_VERSION = 1

_TIME_SOURCES = ("device", "gateway")
_GATEWAY_STATES = ("online", "offline")


@dataclass
class PositionRecord:
    TYPE = "position"  # atributo de classe — não é campo do dataclass

    node_num: int
    node_id: str  # com o '!' inicial, ex. '!0c0ffee0'
    time: int | None  # epoch s, relógio do dispositivo; None = sem hora do fix
    time_source: str  # 'device' | 'gateway'
    lat_i: int  # graus × 1e7 (latitudeI)
    lon_i: int  # graus × 1e7 (longitudeI)
    altitude_m: int | None = None
    sats: int | None = None
    hop_limit: int | None = None
    snr: float | None = None
    rssi: int | None = None
    # epoch s do RECEBIMENTO no gateway (schema 1; eixo de received_at — R5 do gate F2).
    # Produtor preenche sempre; None só em payload antigo (db cai para now() com AVISO).
    rx_time: int | None = None
    friendly_name: str | None = None
    # qualidade do fix (meshtastic.Position); None = ausente (ver qualidade_do_firmware)
    pdop: float | None = None
    hdop: float | None = None
    ground_speed_ms: float | None = None
    ground_track_deg: float | None = None
    precision_bits: int | None = None

    def to_mqtt_payload(self) -> str:
        return json.dumps(
            {"schema": SCHEMA_VERSION, "type": self.TYPE, **asdict(self)},
            ensure_ascii=False,
        )

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.lon_i / 1e7, self.lat_i / 1e7],
            },
            "properties": {
                "node_num": self.node_num,
                "node_id": self.node_id,
                "friendly_name": self.friendly_name,
                "time": self.time,
                "time_source": self.time_source,
                "altitude_m": self.altitude_m,
                "sats": self.sats,
                "hop_limit": self.hop_limit,
                "snr": self.snr,
                "rssi": self.rssi,
                "rx_time": self.rx_time,
                "pdop": self.pdop,
                "hdop": self.hdop,
                "ground_speed_ms": self.ground_speed_ms,
                "ground_track_deg": self.ground_track_deg,
                "precision_bits": self.precision_bits,
            },
        }


@dataclass
class TelemetryRecord:
    TYPE = "telemetry"

    node_num: int
    node_id: str
    time: int | None
    time_source: str  # 'device' | 'gateway'
    battery_level: float | None = None
    voltage: float | None = None
    channel_util: float | None = None
    air_util_tx: float | None = None
    uptime_s: int | None = None
    rx_time: int | None = None  # epoch s do recebimento no gateway (idem PositionRecord)
    friendly_name: str | None = None

    def to_mqtt_payload(self) -> str:
        return json.dumps(
            {"schema": SCHEMA_VERSION, "type": self.TYPE, **asdict(self)},
            ensure_ascii=False,
        )

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "node_num": self.node_num,
                "node_id": self.node_id,
                "friendly_name": self.friendly_name,
                "time": self.time,
                "time_source": self.time_source,
                "battery_level": self.battery_level,
                "voltage": self.voltage,
                "channel_util": self.channel_util,
                "air_util_tx": self.air_util_tx,
                "uptime_s": self.uptime_s,
                "rx_time": self.rx_time,
            },
        }


@dataclass
class StatusRecord:
    TYPE = "status"

    state: str  # 'online' | 'offline'

    def to_mqtt_payload(self) -> str:
        return json.dumps(
            {"schema": SCHEMA_VERSION, "type": self.TYPE, "state": self.state},
            ensure_ascii=False,
        )


Record = PositionRecord | TelemetryRecord | StatusRecord


def parse_mqtt_payload(payload: str | bytes) -> Record | None:
    """Payload → Record; payload ruim → None (nunca levanta exceção).

    Só transporta os campos. A decisão device-vs-gateway é do PRODUTOR
    (gateway.py preenche time/rx_time/time_source — gate F2 B2); o banco grava
    o que chegar e só força 'gateway' quando `time` é nulo (payload legado).
    """
    try:
        obj = json.loads(payload)
        if not isinstance(obj, dict):
            return None
    except Exception:
        # RecursionError (aninhamento infinito) também conta (gate F2 r2 NIT)
        return None
    schema = obj.get("schema")
    # `isinstance(schema, bool)` primeiro: em Python, True == 1 passaria pelo == 1.
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SCHEMA_VERSION:
        return None
    try:
        rtype = obj.get("type")
        if rtype == "status":
            return _parse_status(obj)
        # Formato/tamanho no consume-side (gate F2 r2 RISK): node_id DEVE ser
        # '!'+8 hex coerente com node_num — fecha spoof e o caminho do btree
        # gigante (ProgramLimitExceeded é OperationalError e travaria a fila);
        # friendly_name limitado a 64. Só position/telemetry carregam nó.
        node_num = obj.get("node_num")
        node_id = obj.get("node_id")
        if isinstance(node_num, bool) or not isinstance(node_num, int):
            return None
        if not isinstance(node_id, str) or node_id != f"!{node_num & 0xFFFFFFFF:08x}":
            return None
        fname = obj.get("friendly_name")
        if fname is not None and (not isinstance(fname, str) or len(fname) > 64):
            return None
        if rtype == "position":
            return _parse_position(obj)
        if rtype == "telemetry":
            return _parse_telemetry(obj)
    except Exception:  # OverflowError etc. — a promessa é NUNCA levantar (gate F2 R1)
        return None
    return None


def _req_int(obj: dict, key: str) -> int:
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(key)
    return value


def _req_str(obj: dict, key: str) -> str:
    value = obj[key]
    if not isinstance(value, str):
        raise ValueError(key)
    return value


def _opt_str(obj: dict, key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    return value


def _opt_int(obj: dict, key: str) -> int | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(key)
    return value



def _time_source(obj: dict) -> str:
    value = obj.get("time_source", "device")
    if value not in _TIME_SOURCES:
        raise ValueError("time_source")
    return value
def _opt_float(obj: dict, key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key)
    return float(value)


def qualidade_do_firmware(
    pdop: int | None,
    hdop: int | None,
    ground_speed: int | None,
    ground_track: int | None,
    precision_bits: int | None,
) -> dict:
    """Crus do meshtastic.Position → unidades do domínio (chaves de PositionRecord).

    PDOP/HDOP vêm em centésimos; ground_speed em m/s; ground_track em 1e-5 graus;
    precision_bits é inteiro puro. Zero = ausente: proto3 não distingue 0 de campo
    não enviado, então 0 vira None (inclusive para rumo 0 graus, limitação aceita).
    """

    def ausente(v: int | None) -> int | None:
        return v if v else None

    pdop_c, hdop_c = ausente(pdop), ausente(hdop)
    speed, track = ausente(ground_speed), ausente(ground_track)
    bits = ausente(precision_bits)
    return {
        "pdop": None if pdop_c is None else pdop_c / 100.0,
        "hdop": None if hdop_c is None else hdop_c / 100.0,
        "ground_speed_ms": None if speed is None else float(speed),
        "ground_track_deg": None if track is None else track / 1e5,
        "precision_bits": bits,
    }


def _opt_time(obj: dict) -> int | None:
    return _opt_int(obj, "time")


def _parse_position(obj: dict) -> PositionRecord:
    lat_i = _req_int(obj, "lat_i")
    lon_i = _req_int(obj, "lon_i")
    # Sanidade no consume-side (defesa em profundidade com o CHECK do banco):
    # faixa válida e nunca (0,0) — Null Island não é fix (gate F2 B1/B2).
    if not (-900000000 <= lat_i <= 900000000 and -1800000000 <= lon_i <= 1800000000):
        raise ValueError("lat_i/lon_i fora da faixa")
    if lat_i == 0 and lon_i == 0:
        raise ValueError("lat/lon ambos zero")
    return PositionRecord(
        node_num=_req_int(obj, "node_num"),
        node_id=_req_str(obj, "node_id"),
        time=_opt_time(obj),
        time_source=_time_source(obj),
        lat_i=lat_i,
        lon_i=lon_i,
        altitude_m=_opt_int(obj, "altitude_m"),
        sats=_opt_int(obj, "sats"),
        hop_limit=_opt_int(obj, "hop_limit"),
        snr=_opt_float(obj, "snr"),
        rssi=_opt_int(obj, "rssi"),
        rx_time=_opt_int(obj, "rx_time"),
        friendly_name=_opt_str(obj, "friendly_name"),
        pdop=_opt_float(obj, "pdop"),
        hdop=_opt_float(obj, "hdop"),
        ground_speed_ms=_opt_float(obj, "ground_speed_ms"),
        ground_track_deg=_opt_float(obj, "ground_track_deg"),
        precision_bits=_opt_int(obj, "precision_bits"),
    )


def _parse_telemetry(obj: dict) -> TelemetryRecord:
    return TelemetryRecord(
        node_num=_req_int(obj, "node_num"),
        node_id=_req_str(obj, "node_id"),
        time=_opt_time(obj),
        time_source=_time_source(obj),
        battery_level=_opt_float(obj, "battery_level"),
        voltage=_opt_float(obj, "voltage"),
        channel_util=_opt_float(obj, "channel_util"),
        air_util_tx=_opt_float(obj, "air_util_tx"),
        uptime_s=_opt_int(obj, "uptime_s"),
        rx_time=_opt_int(obj, "rx_time"),
        friendly_name=_opt_str(obj, "friendly_name"),
    )


def _parse_status(obj: dict) -> StatusRecord:
    state = _req_str(obj, "state")
    if state not in _GATEWAY_STATES:
        raise ValueError("state")
    return StatusRecord(state=state)
