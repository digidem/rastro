"""Filtro PURO: packet dict do SerialInterface → Record | None.

Sem I/O, sem logging e NUNCA levanta exceção — entrada boa vira Record,
tudo que não é posição/telemetria reconhecível vira None.

Forma do packet, verificada contra o meshtastic 2.7.11 (``MessageToDict``
camelCase; a lib injeta ``fromId``/``toId`` no TOPO do dict — ver
``.skills/meshtastic/references/python-recipes.md`` e ``mesh_interface.py``:

    {"fromId": "!0c0ffee0", "from": 202374880,
     "decoded": {"portnum": "POSITION_APP",
                 "position": {"latitudeI": -345678901, ...}}}

Telemetria: a 2.7.11 serializa ``deviceMetrics``/``batteryLevel``/
``channelUtilization``/``airUtilTx``/``uptimeSeconds`` (camelCase); as
variantes snake_case (``device_metrics`` etc.) são aceitas também.
"""
from __future__ import annotations

from rastro_gateway.common.records import (
    PositionRecord,
    TelemetryRecord,
    qualidade_do_firmware,
)

Record = PositionRecord | TelemetryRecord


def filter_packet(packet: dict) -> Record | None:
    """Packet bruto → Record | None. Nunca levanta, nunca loga, nunca faz I/O."""
    try:
        return _filter(packet)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _filter(packet: dict) -> Record | None:
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        return None
    portnum = decoded.get("portnum")
    if portnum == "POSITION_APP":
        return _position(packet, decoded)
    if portnum == "TELEMETRY_APP":
        return _telemetry(packet, decoded)
    return None


def _int(value) -> int | None:
    """int verdadeiro (bool NÃO vale — em Python True == 1); resto → None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _node_num(packet: dict) -> int | None:
    return _int(packet.get("from"))


def _node_id(packet: dict, node_num: int) -> str:
    from_id = packet.get("fromId")
    if isinstance(from_id, str) and from_id:
        return from_id
    # lib injeta fromId=None até conhecer o nó — cai no `from`
    return f"!{node_num:08x}"


def _position(packet: dict, decoded: dict) -> PositionRecord | None:
    pos = decoded.get("position")
    if not isinstance(pos, dict):
        return None
    lat_i = _int(pos.get("latitudeI"))
    lon_i = _int(pos.get("longitudeI"))
    if lat_i is None or lon_i is None:
        return None
    if lat_i == 0 and lon_i == 0:
        return None  # 0,0 = sem fix
    node_num = _node_num(packet)
    if node_num is None:
        return None
    t = _int(pos.get("time"))
    qualidade = qualidade_do_firmware(
        _int(pos.get("PDOP")),
        _int(pos.get("HDOP")),
        _int(pos.get("groundSpeed")),
        _int(pos.get("groundTrack")),
        _int(pos.get("precisionBits")),
    )
    return PositionRecord(
        node_num=node_num,
        node_id=_node_id(packet, node_num),
        time=t,
        time_source="device" if t is not None else "gateway",
        lat_i=lat_i,
        lon_i=lon_i,
        altitude_m=_int(pos.get("altitude")),
        sats=_int(pos.get("satsInView")),
        snr=_float(packet.get("snr")),
        rssi=_int(packet.get("rssi")),
        hop_limit=_int(packet.get("hopLimit")),  # hopLimit do topo (gate F3 NIT)
        **qualidade,
        # friendly_name fica None — quem resolve é o gateway.py (fleet.json)
    )


def _telemetry(packet: dict, decoded: dict) -> TelemetryRecord | None:
    telem = decoded.get("telemetry")
    if not isinstance(telem, dict):
        return None
    metrics = telem.get("deviceMetrics")
    if not isinstance(metrics, dict):
        metrics = telem.get("device_metrics")  # grafia snake_case (brief)
    if not isinstance(metrics, dict):
        return None
    node_num = _node_num(packet)
    if node_num is None:
        return None
    t = _int(telem.get("time"))
    return TelemetryRecord(
        node_num=node_num,
        node_id=_node_id(packet, node_num),
        time=t,
        time_source="device" if t is not None else "gateway",
        battery_level=_float(metrics.get("batteryLevel", metrics.get("battery_level"))),
        voltage=_float(metrics.get("voltage")),
        channel_util=_float(
            metrics.get("channelUtilization", metrics.get("channel_utilization"))
        ),
        air_util_tx=_float(metrics.get("airUtilTx", metrics.get("air_util_tx"))),
        uptime_s=_int(metrics.get("uptimeSeconds", metrics.get("uptime_seconds"))),
    )
