"""Contrato de payload (schema 1): serialização MQTT, GeoJSON e parse tolerante."""
import json

from rastro_gateway.common.records import (
    SCHEMA_VERSION,
    PositionRecord,
    StatusRecord,
    TelemetryRecord,
    parse_mqtt_payload,
)


def make_position(**over):
    base = dict(
        node_num=202374880,
        node_id="!0c0ffee0",
        time=1727184000,
        time_source="device",
        lat_i=-400000000,  # -40.0°
        lon_i=-700000000,  # -70.0°
        altitude_m=95,
        sats=8,
        hop_limit=3,
        snr=9.5,
        rssi=-81,
        friendly_name="Meshtastic e5d0",
    )
    base.update(over)
    return PositionRecord(**base)


def make_telemetry(**over):
    base = dict(
        node_num=202374880,
        node_id="!0c0ffee0",
        time=1727184000,
        time_source="device",
        battery_level=87.0,
        voltage=4.13,
        channel_util=12.4,
        air_util_tx=8.1,
        uptime_s=3600,
        friendly_name="Meshtastic e5d0",
    )
    base.update(over)
    return TelemetryRecord(**base)


# --- position ---------------------------------------------------------------

def test_position_payload_schema_fields_and_order():
    payload = make_position().to_mqtt_payload()
    obj = json.loads(payload)
    assert list(obj)[:2] == ["schema", "type"]  # schema primeiro, depois type
    assert obj["schema"] == SCHEMA_VERSION == 1
    assert obj["type"] == "position"
    assert obj["node_num"] == 202374880
    assert obj["node_id"] == "!0c0ffee0"
    assert obj["lat_i"] == -400000000
    assert obj["lon_i"] == -700000000
    assert obj["altitude_m"] == 95
    assert obj["sats"] == 8
    assert obj["hop_limit"] == 3
    assert obj["snr"] == 9.5
    assert obj["rssi"] == -81
    assert obj["friendly_name"] == "Meshtastic e5d0"
    assert obj["time"] == 1727184000


def test_position_payload_keeps_null_time():
    obj = json.loads(make_position(time=None).to_mqtt_payload())
    assert "time" in obj
    assert obj["time"] is None


def test_position_geojson_feature():
    feature = make_position().to_geojson_feature()
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    # GeoJSON: [lon, lat] como float = inteiro × 1e-7
    assert feature["geometry"]["coordinates"] == [-70.0, -40.0]
    props = feature["properties"]
    assert props["node_num"] == 202374880
    assert props["node_id"] == "!0c0ffee0"
    assert props["friendly_name"] == "Meshtastic e5d0"
    assert props["time"] == 1727184000
    assert props["time_source"] == "device"
    assert props["altitude_m"] == 95
    assert props["sats"] == 8
    assert props["hop_limit"] == 3
    assert props["snr"] == 9.5
    assert props["rssi"] == -81


# --- telemetry --------------------------------------------------------

def test_telemetry_payload_and_geojson():
    payload = make_telemetry().to_mqtt_payload()
    obj = json.loads(payload)
    assert list(obj)[:2] == ["schema", "type"]
    assert obj["type"] == "telemetry"
    assert obj["battery_level"] == 87.0
    assert obj["voltage"] == 4.13
    assert obj["channel_util"] == 12.4
    assert obj["air_util_tx"] == 8.1
    assert obj["uptime_s"] == 3600

    feature = make_telemetry().to_geojson_feature()
    assert feature["type"] == "Feature"
    assert feature["geometry"] is None  # telemetria não tem posição
    props = feature["properties"]
    assert props["node_num"] == 202374880
    assert props["battery_level"] == 87.0
    assert props["uptime_s"] == 3600
    assert props["time_source"] == "device"


# --- status -----------------------------------------------------------

def test_status_roundtrip():
    for state in ("online", "offline"):
        obj = json.loads(StatusRecord(state=state).to_mqtt_payload())
        assert obj == {"schema": 1, "type": "status", "state": state}
        parsed = parse_mqtt_payload(StatusRecord(state=state).to_mqtt_payload())
        assert parsed == StatusRecord(state=state)
    assert parse_mqtt_payload(b'{"schema":1,"type":"status","state":"talvez"}') is None


# --- parse ------------------------------------------------------------

def test_parse_roundtrip_stable():
    for rec in (make_position(), make_telemetry(time=None), StatusRecord(state="offline")):
        payload = rec.to_mqtt_payload()
        parsed = parse_mqtt_payload(payload)
        assert parsed == rec
        assert parsed.to_mqtt_payload() == payload  # ida-e-volta estável


def test_parse_rejects_bad_payloads():
    good = make_position().to_mqtt_payload()
    obj = json.loads(good)

    sem_schema = {k: v for k, v in obj.items() if k != "schema"}
    assert parse_mqtt_payload(json.dumps(sem_schema)) is None
    obj["schema"] = 99
    assert parse_mqtt_payload(json.dumps(obj)) is None
    assert parse_mqtt_payload(b'{"schema":true,"type":"position"}') is None  # True == 1!
    assert parse_mqtt_payload(b'{"schema":1,"type":"radio"}') is None
    assert parse_mqtt_payload(b'{"schema":1,quebrado') is None
    assert parse_mqtt_payload(b"[1,2,3]") is None
    assert parse_mqtt_payload("") is None

    sem_lat = {k: v for k, v in obj.items() if k != "lat_i"}
    assert parse_mqtt_payload(json.dumps(sem_lat)) is None

    lat_str = dict(obj, lat_i="-40.0", schema=1)
    assert parse_mqtt_payload(json.dumps(lat_str)) is None

    tsrc_ruim = dict(obj, time_source="sam")
    assert parse_mqtt_payload(json.dumps(tsrc_ruim)) is None


def test_parse_time_null_defaults_time_source_device():
    rec = parse_mqtt_payload(
        b'{"schema":1,"type":"position","node_num":202374880,'
        b'"node_id":"!0c0ffee0","lat_i":-400000000,"lon_i":-700000000,"time":null}'
    )
    assert rec is not None
    assert rec.time is None
    assert rec.time_source == "device"  # parse só transporta; 'gateway' é decisão do db


def test_parse_never_raises_overflow_and_friends():
    # gate F2 R1: OverflowError escapava do except antigo (int de 400 dígitos)
    obj = {"schema": 1, "type": "telemetry", "node_num": 1, "node_id": "!1",
           "voltage": int("9" * 400)}
    assert parse_mqtt_payload(json.dumps(obj)) is None
    pos = {"schema": 1, "type": "position", "node_num": 1, "node_id": "!1",
           "lat_i": 1, "lon_i": 2, "snr": int("9" * 400)}
    assert parse_mqtt_payload(json.dumps(pos)) is None


def test_parse_rejects_null_island_and_out_of_range():
    # gate F2 B1/B2: (0,0) não é fix; fora da faixa ±90/±180 graus×1e7 é lixo
    base = {"schema": 1, "type": "position", "node_num": 1, "node_id": "!1"}
    assert parse_mqtt_payload(json.dumps(dict(base, lat_i=0, lon_i=0))) is None
    assert parse_mqtt_payload(json.dumps(dict(base, lat_i=900000001, lon_i=0))) is None
    assert parse_mqtt_payload(json.dumps(dict(base, lat_i=0, lon_i=-1800000001))) is None


def test_rx_time_roundtrip():
    # gate F2 R5/B2: rx_time (recebimento no gateway) viaja no payload e no GeoJSON
    rec = make_position(rx_time=1727184100)
    obj = json.loads(rec.to_mqtt_payload())
    assert obj["rx_time"] == 1727184100
    assert rec.to_geojson_feature()["properties"]["rx_time"] == 1727184100
    parsed = parse_mqtt_payload(rec.to_mqtt_payload())
    assert parsed is not None and parsed.rx_time == 1727184100


# --- fortalecimento R1b-8: guards anti-spoof ----------------------------------


def test_parse_rejeita_node_id_incoerente_e_nome_longo():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rastro_gateway.common.records import parse_mqtt_payload
    import json

    def payload(**over):
        obj = {
            "schema": 1,
            "type": "position",
            "node_num": 0x0badf00a,
            "node_id": "!0badf00a",
            "time": 1700000000,
            "time_source": "device",
            "lat_i": -30000000,
            "lon_i": -60000000,
        }
        obj.update(over)
        return json.dumps(obj).encode()

    # node_id não-bate com node_num → spoof fechado
    assert parse_mqtt_payload(payload(node_id="!deadbeef")) is None
    # friendly_name > 64 → rejeitado
    assert parse_mqtt_payload(payload(friendly_name="x" * 65)) is None
    # válido com nome no limite passa
    rec = parse_mqtt_payload(payload(friendly_name="y" * 64))
    assert rec is not None and len(rec.friendly_name) == 64
