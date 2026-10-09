"""Feature GeoJSON do spool → Record (caminho de VOLTA do replay).

``common/records.py`` é o contrato e não pode ser tocado; este módulo novo
faz só a reconstrução para o replay do spool. Ida:
``PositionRecord.to_geojson_feature()`` / ``TelemetryRecord.to_geojson_feature()``.

Feature desconhecida/malformada → None (nunca levanta; replay segue).
"""
from __future__ import annotations

from rastro_gateway.common.records import PositionRecord, TelemetryRecord

Record = PositionRecord | TelemetryRecord

_TIME_SOURCES = ("device", "gateway")
# chaves que só existem em feature de telemetria (to_geojson_feature grava
# mesmo quando o valor é null, então presença de chave == tipo telemetria)
_TELEMETRY_KEYS = ("battery_level", "voltage", "channel_util", "air_util_tx", "uptime_s")


def from_geojson_feature(feature: dict) -> PositionRecord | TelemetryRecord | None:
    """Feature GeoJSON → Record; feature desconhecida → None."""
    try:
        return _from_feature(feature)
    except Exception:  # OverflowError/RecursionError em spool corrompido (gate F2 r2)
        return None


def _from_feature(feature: dict) -> Record | None:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return None
    props = feature.get("properties")
    if not isinstance(props, dict):
        return None
    node_num = _req_int(props, "node_num")
    node_id = _req_str(props, "node_id")
    # Simetria com parse_mqtt_payload (R1b-5): spool adulterado não pode virar
    # record publicado — identidade coerente e nome limitado, igual ao caminho MQTT.
    if node_id != f"!{node_num & 0xFFFFFFFF:08x}":
        return None
    fname = props.get("friendly_name")
    if fname is not None and (not isinstance(fname, str) or len(fname) > 64):
        return None
    time_source = props.get("time_source", "device")
    if time_source not in _TIME_SOURCES:
        return None
    common = dict(
        node_num=node_num,
        node_id=node_id,
        time=_opt_int(props, "time"),
        time_source=time_source,
        rx_time=_opt_int(props, "rx_time"),
        friendly_name=fname,
    )
    if any(key in props for key in _TELEMETRY_KEYS):
        return TelemetryRecord(
            **common,
            battery_level=_opt_float(props, "battery_level"),
            voltage=_opt_float(props, "voltage"),
            channel_util=_opt_float(props, "channel_util"),
            air_util_tx=_opt_float(props, "air_util_tx"),
            uptime_s=_opt_int(props, "uptime_s"),
        )
    return _position(feature, common)


def _position(feature: dict, common: dict) -> PositionRecord | None:
    geom = feature.get("geometry")
    if not isinstance(geom, dict) or geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) != 2:
        return None
    lat_i = _coord(coords[1])  # GeoJSON: [lon, lat]
    lon_i = _coord(coords[0])
    if lat_i is None or lon_i is None:
        return None
    # Faixa e Null-Island — mesma defesa do parse MQTT e do CHECK do banco
    if not (-900000000 <= lat_i <= 900000000 and -1800000000 <= lon_i <= 1800000000):
        return None
    if lat_i == 0 and lon_i == 0:
        return None
    props = feature["properties"]
    return PositionRecord(
        **common,
        lat_i=lat_i,
        lon_i=lon_i,
        altitude_m=_opt_int(props, "altitude_m"),
        sats=_opt_int(props, "sats"),
        hop_limit=_opt_int(props, "hop_limit"),
        snr=_opt_float(props, "snr"),
        rssi=_opt_int(props, "rssi"),
        pdop=_opt_float(props, "pdop"),
        hdop=_opt_float(props, "hdop"),
        ground_speed_ms=_opt_float(props, "ground_speed_ms"),
        ground_track_deg=_opt_float(props, "ground_track_deg"),
        precision_bits=_opt_int(props, "precision_bits"),
    )


def _coord(value) -> int | None:
    """graus → graus × 1e7 (int); round() é exato na faixa de coordenadas."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value * 1e7)


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


def _opt_float(obj: dict, key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key)
    return float(value)
