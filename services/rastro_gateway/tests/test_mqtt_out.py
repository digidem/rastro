"""mqtt_out: janela de early-ack (B1) e tratamento de NO_CONN (B2) — gate F3.

Fake client sem rede: exercita _publish/_on_publish/_after_publish diretamente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

import paho.mqtt.client as mqtt

from rastro_gateway.bridge.mqtt_out import MqttOut
from rastro_gateway.common.records import PositionRecord


class FakeInfo:
    def __init__(self, mid, rc):
        self.mid = mid
        self.rc = rc


class FakeClient:
    def __init__(self, rc=mqtt.MQTT_ERR_SUCCESS):
        self.rc = rc
        self._next_mid = 0
        self.published = []
        self.connected = True

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos=1):
        self._next_mid += 1
        self.published.append((topic, payload))
        return FakeInfo(self._next_mid, self.rc)


def make_out(tmp_path, spool):
    from rastro_gateway.bridge.mqtt_out import MqttConfig
    cfg = MqttConfig(
        host="x", port=1, username=None, password=None, ca_cert=None,
        client_id="t", topic_prefix="rastro", keepalive_secs=60, tls_insecure=False,
    )
    return MqttOut(cfg, spool)


def make_record(i=0):
    return PositionRecord(
        node_num=0xAAAA0001, node_id="!aaaa0001", time=1727184000 + i,
        time_source="device", lat_i=-40000000 + i, lon_i=-70000000 + i,
    )


def test_no_conn_registra_e_nao_reenfileira(tmp_path, spool):
    """B2: MQTT_ERR_NO_CONN = mensagem ficou na fila do paho (reenvia sozinho).
    O span PERMANECE registrado — sem unregister, sem duplicação por retry."""
    out = make_out(tmp_path, spool)
    out._client = FakeClient(rc=mqtt.MQTT_ERR_NO_CONN)
    rec = make_record()
    span = spool.append(rec)
    out._publish(span, rec.to_mqtt_payload())
    # registrado na FIFO do spool e mid mapeado (não voltou pra re-tentativa)
    assert spool.position() == (span[0], 0)  # watermark não moveu
    assert not out._pending
    assert len(out._mids) == 1
    # PUBACK chega depois (paho reenviou na reconexão) → watermark avança
    mid = next(iter(out._mids))
    out._on_publish(out._client, None, mid, None, None)
    assert spool.position() == (span[0], span[2])


def test_early_ack_fora_da_janela_e_descartado(tmp_path, spool):
    """B1: PUBACK de mid desconhecido SEM publish em voo = mid velho/estrangeiro
    (o paho reusa mids). Precisa ser DESCARTADO — guardá-lo confirmaria um
    registro futuro sem PUBACK real."""
    out = make_out(tmp_path, spool)
    out._client = FakeClient()
    out._on_publish(out._client, None, 777, None, None)  # ninguém publicando
    assert 777 not in out._early_acks  # descartado, não guardado

    # agora um publish REAL que recebe mid 777 por reuso:
    rec = make_record()
    span = spool.append(rec)
    fake = FakeClient()
    fake._next_mid = 776  # próximo publish vai usar mid 777
    out._client = fake
    out._publish(span, rec.to_mqtt_payload())
    assert 777 in out._mids  # registrado, aguardando o PUBACK DELE
    out._on_publish(fake, None, 777, None, None)
    assert spool.position() == (span[0], span[2])


def test_rc_real_de_enqueue_retenta_sem_avancar(tmp_path, spool):
    """rc != SUCCESS/NO_CONN (ex. MQTT_ERR_QUEUE_SIZE antigo): unregister + retry."""
    import threading
    import time
    out = make_out(tmp_path, spool)
    out._client = FakeClient(rc=mqtt.MQTT_ERR_QUEUE_SIZE)
    rec = make_record()
    span = spool.append(rec)
    t = threading.Thread(target=out._publish, args=(span, rec.to_mqtt_payload()), daemon=True)
    t.start()
    time.sleep(0.4)  # uma tentativa acontece
    assert not out._mids  # nada registrado
    assert spool.position() == (span[0], 0)  # watermark intacta
    out._stop.set()  # encerra o loop de retry
    t.join(timeout=10)


def test_puback_de_status_nao_move_watermark(tmp_path, spool):
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    info = fake.publish("rastro/status/gateway", "{}", qos=1)
    with out._lock:
        out._ignore_mids.add(info.mid)
    antes = spool.position()
    out._on_publish(fake, None, info.mid, None, None)
    assert spool.position() == antes
    assert not out._ignore_mids  # consumido


# (test_rc_real_de_enqueue_retenta_sem_avancar vive acima, com thread + stop)


# --- fixture spool compartilhada -----------------------------------------

import pytest


@pytest.fixture
def spool(tmp_path):
    from rastro_gateway.bridge.spool import Spool
    return Spool(tmp_path / "spool", rotate_bytes=8 * 1024 * 1024, max_bytes=50 * 1024 * 1024)


def test_replay_pula_spans_em_voo(tmp_path, spool):
    """r2 BLOCKER: spans já publicados (aguardando PUBACK na fila do paho) NÃO
    podem ser republicados pelo replay — senão cada reconexão duplica o backlog."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    for i in range(3):
        r = make_record(i)
        span = spool.append(r)
        out._spool.register(span)  # em voo
    out._do_replay()
    assert len(fake.published) == 0  # todos em voo: nada republicado
    r = make_record(9)  # um span NOVO (não registrado)
    spool.append(r)
    out._do_replay()
    assert len(fake.published) == 1  # só o novo


def test_worker_descarta_pending_desconectado(tmp_path, spool):
    """r2 BLOCKER: desconectado, o worker DESCARTA o pending — o registro é
    durável no spool e o replay da reconexão cobre; publicar com NO_CONN só
    engordaria a fila do paho (RAM sem teto)."""
    import threading
    import time
    out = make_out(tmp_path, spool)
    fake = FakeClient(rc=mqtt.MQTT_ERR_NO_CONN)
    fake.connected = False
    out._client = fake
    r = make_record(0)
    span = spool.append(r)
    out.publish_record(span, r.to_mqtt_payload())
    t = threading.Thread(target=out._work, daemon=True)
    t.start()
    time.sleep(0.5)
    out._stop.set()
    t.join(timeout=5)
    assert len(fake.published) == 0  # não publicou desconectado
    assert not out._pending  # e não deixou na fila: descartou (spool cobre)


def test_replay_respeita_janela_de_mids(tmp_path, spool):
    """r3 BLOCKER: backlog maior que REPLAY_WINDOW nunca deixa mais que a janela
    de mids em voo (o mid do paho colide/sobrescreve após 65535)."""
    import threading
    import time as _time
    import rastro_gateway.bridge.mqtt_out as mo
    from rastro_gateway.common.records import PositionRecord

    class SlowAckClient(FakeClient):
        """publish sucesso, mas PUBACK só quando o teste liberar."""
        def __init__(self):
            super().__init__()
            self.connected = True

        def publish(self, topic, payload, qos=1):
            info = super().publish(topic, payload, qos)
            return info

    out = make_out(tmp_path, spool)
    fake = SlowAckClient()
    out._client = fake
    n = mo.REPLAY_WINDOW + 200
    for i in range(n):
        r = PositionRecord(node_num=int("eeeeee03", 16), node_id="!eeeeee03",
                           time=1727184000 + i, time_source="device",
                           lat_i=-30000000, lon_i=-60000000)
        spool.append(r)
    t = threading.Thread(target=out._do_replay, daemon=True)
    t.start()
    pico = 0
    for _ in range(40):
        _time.sleep(0.05)
        with out._lock:
            pico = max(pico, len(out._mids))
        if len(fake.published) >= n:
            break
    out._stop.set()
    t.join(timeout=5)
    assert pico <= mo.REPLAY_WINDOW, f"pico de mids em voo: {pico}"
