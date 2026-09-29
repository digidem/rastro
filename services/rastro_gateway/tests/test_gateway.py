"""gateway.py: ciclo de vida e sanitização — sem hardware (fakes de interface).

Cobre os achados R1b-2/3/4/10 e R8-S1 da revisão completa (2026-09-28):
exit codes, subscrição antes da interface, spool não-gravável = fatal,
clamp de intervalo e nome de nó sanitizado em log.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_gateway.bridge import gateway as gw_mod
from rastro_gateway.bridge.gateway import EXIT_CONFIG, EXIT_SERIAL, Gateway, _nome_log


def _position_packet(num=0x0badf00a, lat_i=-30000000, lon_i=-60000000, t=None):
    """Packet Meshtastic decodificado mínimo que passa no packet_filter."""
    return {
        "from": num,
        "fromId": f"!{num:08x}",
        "decoded": {
            "portnum": "POSITION_APP",
            "position": {
                "latitudeI": lat_i,
                "longitudeI": lon_i,
                **({"time": t} if t is not None else {}),
            },
        },
    }


class FakeSpool:
    def __init__(self, fail_with=None):
        self.records = []
        self.fail_with = fail_with

    def append(self, record):
        if self.fail_with is not None:
            raise self.fail_with
        self.records.append(record)
        return ("spool-000001.geojsonl", 0, 10)


class FakeMqtt:
    def __init__(self):
        self.published = []
        self.cfg = type(
            "C", (), {"host": "h", "port": 1, "topic_prefix": "rastro"}
        )()

    def connect(self):
        pass

    def stop(self):
        pass

    def publish_record(self, span, payload):
        self.published.append((span, payload))


def make_gateway(monkeypatch, tmp_path, spool=None, interval=300, serial="/dev/ttyFAKE"):
    monkeypatch.setenv("RASTRO_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("RASTRO_GW_SERIAL_PORT", serial)
    monkeypatch.setenv("RASTRO_GW_POSITION_INTERVAL_ASSUMED_SECS", str(interval))
    monkeypatch.setattr(gw_mod.fleet_names, "load_fleet_names", lambda: {})
    g = Gateway.__new__(Gateway)  # __init__ cria Spool/MqttOut reais; injetamos fakes
    g._names = {}
    g._serial_port = serial
    g._spool_dir = tmp_path / "spool"
    g._max_bytes = 1000
    g._rotate_bytes = 500
    g._pos_interval = interval
    g.spool = spool or FakeSpool()
    g.mqtt = FakeMqtt()
    g._stop = __import__("threading").Event()
    g._pos_lock = __import__("threading").Lock()
    g._last_position = {}
    g._labels = {}
    g._silence_warned = set()
    g._watchdog = None
    g.interface = None
    g._exit_code = 0
    return g


# --- _nome_log (R8-S1) -------------------------------------------------------


def test_nome_log_preserva_nome_normal_ptbr():
    assert _nome_log("Barco Rio 03") == "Barco Rio 03"
    assert _nome_log(None) == "<sem nome>"


def test_nome_log_saniza_coordenada_chave_e_multilinha():
    assert _nome_log("-4.21, -30.05") == "<nome não-imprimível>"
    assert _nome_log("fixo -4.21; -30.05 rio") == "<nome não-imprimível>"
    assert _nome_log("psk: AAAAAAAAAAAAAAAAAAAAAA==") == "<nome não-imprimível>"
    assert _nome_log("linha1\nlinha2") == "<nome não-imprimível>"


# --- on_packet ---------------------------------------------------------------


def test_on_packet_preenche_rx_time_e_time_source_gateway(monkeypatch, tmp_path):
    g = make_gateway(monkeypatch, tmp_path)
    antes = int(time.time())
    g.on_packet(_position_packet(t=None))
    rec = g.spool.records[0]
    assert rec.rx_time >= antes
    assert rec.time == rec.rx_time  # sem hora do dispositivo → hora do gateway
    assert rec.time_source == "gateway"


def test_on_packet_spool_nao_gravavel_eh_fatal_exit3(monkeypatch, tmp_path):
    g = make_gateway(monkeypatch, tmp_path, spool=FakeSpool(fail_with=OSError(28, "cheio")))
    g.on_packet(_position_packet())
    assert g._exit_code == EXIT_SERIAL
    assert g._stop.is_set()
    assert g.mqtt.published == []  # nada publicado sem durabilidade


def test_on_packet_falha_generica_nao_mata_captura(monkeypatch, tmp_path):
    g = make_gateway(monkeypatch, tmp_path)
    g.on_packet({"lixo": True})  # filter_packet → None, silencioso
    g.on_packet(_position_packet())  # normal segue vivo
    assert len(g.spool.records) == 1


# --- ciclo de vida ------------------------------------------------------------


def test_run_rejeita_intervalo_menor_que_30(monkeypatch, tmp_path):
    g = make_gateway(monkeypatch, tmp_path, interval=10, serial=None)
    assert g.run() == EXIT_CONFIG


def test_run_sem_porta_serial_exit3(monkeypatch, tmp_path):
    g = make_gateway(monkeypatch, tmp_path, serial=None)
    assert g.run() == EXIT_SERIAL


def test_connection_lost_exit3_e_loop_termina(monkeypatch, tmp_path):
    import threading

    g = make_gateway(monkeypatch, tmp_path)

    class FakeIface:
        def close(self):
            pass

    monkeypatch.setattr(gw_mod, "SerialInterface", lambda devPath: FakeIface())
    monkeypatch.setattr(gw_mod.signal, "signal", lambda *a, **k: None)
    resultado = {}

    def _roda():
        resultado["code"] = g.run()

    t = threading.Thread(target=_roda)
    t.start()
    time.sleep(0.2)  # run() já subscriveu (antes da interface) e está no wait
    g._on_connection_lost()
    t.join(timeout=5)
    assert not t.is_alive()
    assert resultado["code"] == EXIT_SERIAL
