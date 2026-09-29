"""Filtro de packets do SerialInterface: boa entrada → Record, resto → None."""
from rastro_gateway.bridge.packet_filter import filter_packet
from rastro_gateway.common.records import PositionRecord, TelemetryRecord

# `from` de verdade do campo (meshtastic 2.7.11 injeta fromId no topo do dict)
NODE_NUM = 0x0c0ffee0  # 202374880 → "!0c0ffee0"


def pos_packet(**over):
    """Packet POSITION_APP válido; over muda qualquer campo do topo/position."""
    top = {"from": NODE_NUM, "fromId": "!0c0ffee0", "snr": -12.5, "rssi": -81}
    position = dict(latitudeI=-345678901, longitudeI=-62572544, altitude=95, satsInView=6)
    for key in list(over):
        if key in top:
            top[key] = over.pop(key)
    position.update(over)
    return {**top, "decoded": {"portnum": "POSITION_APP", "position": position}}


def telem_packet(metrics=None):
    """Packet TELEMETRY_APP com deviceMetrics camelCase (grafia da 2.7.11)."""
    if metrics is None:
        metrics = dict(batteryLevel=87, voltage=4.15, channelUtilization=12.5,
                       airUtilTx=0.23, uptimeSeconds=3600)
    return {
        "from": NODE_NUM,
        "fromId": "!0c0ffee0",
        "decoded": {
            "portnum": "TELEMETRY_APP",
            "telemetry": {"time": 1727184000, "deviceMetrics": metrics},
        },
    }


# --- lote misto -------------------------------------------------------------

def test_tres_posicoes_um_telem_dois_lixo():
    batch = [
        pos_packet(),
        {"from": NODE_NUM, "fromId": "!0c0ffee0",
         "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "oi"}},  # lixo 1
        pos_packet(latitudeI=-345000000),
        telem_packet(),
        pos_packet(latitudeI=-346000000),
        {"from": NODE_NUM, "fromId": "!0c0ffee0", "encrypted": "deadbeef"},  # lixo 2
    ]
    records = [filter_packet(p) for p in batch]
    assert records[1] is None and records[5] is None  # os 2 lixos
    assert [type(r) for r in records if r is not None] == [
        PositionRecord, PositionRecord, TelemetryRecord, PositionRecord,
    ]
    positions = [r for r in records if isinstance(r, PositionRecord)]
    telemetries = [r for r in records if isinstance(r, TelemetryRecord)]
    assert len(positions) == 3
    assert len(telemetries) == 1


# --- identidade do nó -------------------------------------------------------

def test_posicao_from_id_none_cai_no_from():
    rec = filter_packet(pos_packet(fromId=None))
    assert rec is not None
    assert rec.node_num == NODE_NUM
    assert rec.node_id == f"!{NODE_NUM:08x}"


# --- tempo / origem do tempo ------------------------------------------------

def test_posicao_sem_time_vem_do_gateway():
    rec = filter_packet(pos_packet())
    assert rec is not None
    assert rec.time is None
    assert rec.time_source == "gateway"


def test_posicao_com_time_vem_do_device():
    rec = filter_packet(pos_packet(time=1727184000))
    assert rec is not None
    assert rec.time == 1727184000
    assert rec.time_source == "device"


# --- posições rejeitadas ----------------------------------------------------

def test_posicao_00_e_sem_fix():
    assert filter_packet(pos_packet(latitudeI=0, longitudeI=0)) is None


def test_posicao_sem_longitude_e_descartada():
    packet = pos_packet()
    del packet["decoded"]["position"]["longitudeI"]
    assert filter_packet(packet) is None


def test_posicao_sem_from_e_descartada():
    packet = pos_packet()
    del packet["from"]
    assert filter_packet(packet) is None


def test_posicao_sem_position_e_descartada():
    packet = {"from": NODE_NUM, "fromId": "!0c0ffee0",
              "decoded": {"portnum": "POSITION_APP"}}
    assert filter_packet(packet) is None


# --- telemetria -------------------------------------------------------------

def test_telemetria_camel_case_mapeada():
    rec = filter_packet(telem_packet())
    assert rec is not None
    assert rec.node_num == NODE_NUM
    assert rec.node_id == "!0c0ffee0"
    assert rec.time == 1727184000
    assert rec.time_source == "device"
    assert rec.battery_level == 87
    assert rec.voltage == 4.15
    assert rec.channel_util == 12.5
    assert rec.air_util_tx == 0.23
    assert rec.uptime_s == 3600


def test_telemetria_snake_case_tambem_aceita():
    metrics = dict(battery_level=50, voltage=3.9, channel_utilization=20.0,
                   air_util_tx=0.1, uptime_seconds=60)
    rec = filter_packet(telem_packet(metrics))
    assert rec is not None
    assert rec.battery_level == 50
    assert rec.channel_util == 20.0
    assert rec.uptime_s == 60


def test_telemetria_sem_device_metrics_e_descartada():
    packet = {
        "from": NODE_NUM,
        "fromId": "!0c0ffee0",
        "decoded": {
            "portnum": "TELEMETRY_APP",
            "telemetry": {"environmentMetrics": {"temperature": 30.0}},
        },
    }
    assert filter_packet(packet) is None


# --- nunca levanta ----------------------------------------------------------

def test_packet_nao_dict_devolve_none():
    assert filter_packet(None) is None
    assert filter_packet([1, 2, 3]) is None
    assert filter_packet("packet") is None
    assert filter_packet(42) is None


# --- fortalecimento R1b-7: portnums além dos já cobertos ---------------------


def test_filtro_recusa_nodeinfo_traceroute_e_decoded_nao_dict():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rastro_gateway.bridge.packet_filter import filter_packet

    base = {"from": 1, "fromId": "!00000001"}
    for portnum in ("NODEINFO_APP", "TRACEROUTE_APP", "ROUTING_APP", "ADMIN_APP"):
        assert filter_packet({**base, "decoded": {"portnum": portnum}}) is None
    assert filter_packet({**base, "decoded": "não-dict"}) is None
    assert filter_packet({**base, "decoded": {"portnum": "POSITION_APP", "position": {}}}) is None
