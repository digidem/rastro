"""ingest/mqtt_in: disciplina de ack (store→ack), requeue por falha operacional,
veneno e _flush_loop que não morre — gate FIX-R2.

Fakes leves (client que registra acks; db programável), sem rede e sem mocks
pesados — mesmo estilo de tests/test_mqtt_out.py."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_gateway.common.records import PositionRecord, StatusRecord
from rastro_gateway.ingest.mqtt_in import (
    Ingester,
    MqttConfig,
)


def _cfg():
    return MqttConfig(
        host="x",
        port=1,
        username=None,
        password=None,
        ca_cert=None,
        client_id="t",
        topic_prefix="rastro",
        keepalive_secs=60,
        tls_insecure=False,
    )


class FakeMsg:
    """Mensagem MQTT que chega no on_message — suficiente para o caminho testado."""

    def __init__(self, mid, payload, topic="rastro/positions/!aaaa0001", qos=1):
        self.mid = mid
        self.payload = payload if isinstance(payload, bytes) else payload.encode()
        self.topic = topic
        self.qos = qos


class MqttFake:
    """client mínimo: registra acks na ordem. ``fail_from=N`` faz o ack de índice
    N levantar (indisponibilidade do broker no meio do lote — regressão FIX-R2 b).
    ``timeline`` (opcional) é uma lista compartilhada com o DbFake para provar
    a ordem store→ack."""

    def __init__(self, fail_from=None, timeline=None):
        self.acks = []  # (mid, qos) na ordem em que saíram
        self.fail_from = fail_from
        self.fail_count = 0
        self.timeline = timeline if timeline is not None else []

    def ack(self, mid, qos):
        if self.fail_from is not None and len(self.acks) >= self.fail_from:
            self.fail_count += 1
            raise RuntimeError("broker não aceita ack agora")
        self.acks.append((mid, qos))
        self.timeline.append(f"ack:{mid}")


class DbFake:
    """store_batch programável: ``error`` levanta (erro operacional simulado —
    o chamador reenfileira e NÃO acka); ``veneno`` volta na tupla."""

    def __init__(self, error=None, veneno=None, timeline=None):
        self.error = error
        self.veneno = list(veneno or [])
        self.calls = []
        self.timeline = timeline if timeline is not None else []

    def store_batch(self, batch, names, fleet_ids):
        self.timeline.append(f"store:{len(batch)}")
        self.calls.append(list(batch))
        if self.error is not None:
            raise self.error
        novas = len(batch) - len(self.veneno)
        return (novas, len(self.veneno), list(self.veneno))


def make_record(i=1):
    return PositionRecord(
        node_num=2852170113 + i,
        node_id="!aaaa000%d" % i,
        time=1727184000 + i,
        time_source="device",
        lat_i=-40000000 - i,
        lon_i=-70000000 - i,
    )


def make_ingester(client, db):
    return Ingester(
        client=client,
        database=db,
        names={},
        fleet_ids={},
        cfg=_cfg(),
    )


def add(ing, mid, i=0):
    """Enfileira um record de posição (mesma rota do on_message pós-parse)."""
    ing._enqueue(mid, 1, make_record(i))


def test_ack_somente_apos_store_e_na_ordem_do_lote():
    """(1) ack SÓ depois de store_batch retornar; ordem do lote preservada."""
    timeline = []
    db = DbFake(timeline=timeline)
    fake = MqttFake(timeline=timeline)
    ing = make_ingester(fake, db)
    add(ing, 101, 1)
    add(ing, 102, 2)
    assert fake.acks == []  # nada ackado ANTES do store
    assert ing.flush() is True
    assert timeline[0].startswith("store:")  # primeiro evento é o store
    assert [r.node_num for r in db.calls[-1]] == [
        make_record(1).node_num,
        make_record(2).node_num,
    ]
    assert fake.acks == [(101, 1), (102, 1)]
    assert len(timeline) == 3 and timeline[0].startswith("store:")
    assert ing.pending() == 0


def test_store_falha_reenfileira_sem_ack_e_preserva_ordem():
    """(2) store_batch levantando → NENHUM ack e lote reenfileirado (pending
    preserva a ordem); mensagens chegadas durante a falha vão DEPOIS das antigas."""
    db = DbFake(error=RuntimeError("banco fora"))
    fake = MqttFake()
    ing = make_ingester(fake, db)
    add(ing, 201, 1)
    add(ing, 202, 2)
    assert ing.flush() is False
    assert fake.acks == []  # sem commit → sem ack
    assert ing.pending() == 2
    assert [r.node_num for _, _, r in ing._batch] == [
        make_record(1).node_num,
        make_record(2).node_num,
    ]
    # recupera o banco; nova mensagem chegou durante a falha — ordem antiga primeiro
    add(ing, 203, 3)
    db.error = None
    assert ing.flush() is True
    assert [r.node_num for r in db.calls[-1]] == [
        make_record(1).node_num,
        make_record(2).node_num,
        make_record(3).node_num,
    ]
    assert fake.acks == [(201, 1), (202, 1), (203, 1)]
    assert ing.pending() == 0


def test_veneno_e_ackado_e_contabilizado():
    """(3) registro fora do schema → ackado e descartado (não trava a fila)."""
    db = DbFake(veneno=[make_record(9)])
    fake = MqttFake()
    ing = make_ingester(fake, db)
    add(ing, 301, 9)
    assert ing.flush() is True
    assert fake.acks == [(301, 1)]  # veneno foi ackado
    assert ing.pending() == 0  # e não reenfileirado
    # o lote entregue ao banco é contabilizado: 1 veneno no meio dos válidos
    db2 = DbFake(veneno=[make_record(2)])
    fake2 = MqttFake()
    ing2 = make_ingester(fake2, db2)
    add(ing2, 302, 1)
    add(ing2, 303, 2)
    assert ing2.flush() is True
    assert len(db2.calls[-1]) == 2  # lote com 2, 1 veneno
    assert fake2.acks == [(302, 1), (303, 1)]
    assert ing2.pending() == 0


def test_ack_falhando_no_meio_do_lote_nao_propaga():
    """(4) REGRESSÃO FIX-R2 (b): ack() do client levanta no meio do lote →
    flush NÃO propaga; lote segue commitado e é drenado."""
    timeline = []
    db = DbFake(timeline=timeline)
    fake = MqttFake(fail_from=2, timeline=timeline)
    ing = make_ingester(fake, db)
    mids = [400 + i for i in range(5)]
    for mid in mids:
        add(ing, mid, mid - 400 + 1)
    assert ing.flush() is True  # NÃO levanta
    assert timeline == ["store:5", "ack:400", "ack:401"]
    assert [mid for mid, _qos in fake.acks] == mids[:2]
    assert fake.fail_count == 1  # 1º ack que falhou interrompe o loop
    assert ing.pending() == 0  # commitado uma vez; redelivery fica pro broker


def test_status_record_ack_imediato_sem_ir_ao_lote():
    """(5) status/gateway: ack imediato no on_message, sem passar pelo lote/db."""
    db = DbFake()
    fake = MqttFake()
    ing = make_ingester(fake, db)
    msg = FakeMsg(501, StatusRecord(state="online").to_mqtt_payload(),
                  topic="rastro/status/gateway")
    ing.on_message(fake, None, msg)
    assert fake.acks == [(501, 1)]  # ack imediato
    assert ing.pending() == 0
    assert db.calls == []  # status nunca vai ao banco


class _LenExplode:
    """'veneno' cujo len() levanta — única forma de flush() PROPAGAR depois
    do hardening (store ok, ack ok, log do veneno explode). Serve para provar
    que a thread do timer absorve a exceção em vez de morrer."""

    def __len__(self):
        raise RuntimeError("boom no log do veneno")


def test_flush_loop_sobrevive_a_flush_que_levanta(caplog):
    """REGRESSÃO FIX-R2 (a): flush periódico levantando → log.exception e a
    thread do timer CONTINUA viva; para corretamente no stop."""
    db = DbFake()
    fake = MqttFake()
    ing = make_ingester(fake, db)
    ing._batch_secs = 0.05  # lote vence rápido
    add(ing, 601, 1)

    def store_com_len_explosivo(batch, names, fleet_ids):
        return (1, 0, _LenExplode())

    db.store_batch = store_com_len_explosivo
    ing.start()
    try:
        # espera o log da exceção absorvida (log.exception do _flush_loop)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "FALHA: flush periódico" not in caplog.text:
            time.sleep(0.01)
        assert "FALHA: flush periódico" in caplog.text
        assert ing._flusher.is_alive()  # VIVA depois do flush que levantou
    finally:
        ing.stop()
    assert not ing._flusher  # stop() juntou a thread — ela não morreu antes