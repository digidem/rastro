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
        client_id="t", topic_prefix="rastro", keepalive_secs=60,
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


def test_transport_padrao_tcp_e_websockets_por_env():
    from rastro_gateway.bridge.mqtt_out import MqttConfig, build_client

    padrao = MqttConfig.from_env({})
    assert (padrao.transport, padrao.ws_path) == ("tcp", "/mqtt")

    ws = MqttConfig.from_env(
        {"RASTRO_MQTT_TRANSPORT": "WebSockets", "RASTRO_MQTT_WS_PATH": "/x", "RASTRO_MQTT_PORT": "443"}
    )
    assert (ws.transport, ws.ws_path, ws.port) == ("websockets", "/x", 443)
    # o cliente paho é construído com o transporte pedido (TLS de verificação ligada)
    cliente = build_client(ws)
    assert cliente._transport == "websockets"


def test_transport_invalido_falha_na_configuracao():
    import pytest

    from rastro_gateway.bridge.mqtt_out import MqttConfig

    with pytest.raises(RuntimeError, match="RASTRO_MQTT_TRANSPORT"):
        MqttConfig.from_env({"RASTRO_MQTT_TRANSPORT": "quic"})


# --- PUBACK com reason code (MQTT v5): recusa do broker — TODO 6a ------------

import logging
import threading
import time

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

import rastro_gateway.bridge.mqtt_out as mo

RC_NEGADO = "Not authorized"  # reason 135 (>= 128): o que a ACL negada devolve


def _rc_puback(negado=True):
    if negado:
        return ReasonCode(PacketTypes.PUBACK, RC_NEGADO)
    # 16 "No matching subscribers" < 128: falha SÓ acima de 128, não só "Success"
    return ReasonCode(PacketTypes.PUBACK, "No matching subscribers")


def test_puback_negado_nao_avanca_watermark_e_reenfileira(tmp_path, spool, caplog):
    """v5 reason >= 128 (broker recusou): watermark PARADA, não conta como
    entregue, log FALHA em PT-BR e o record volta pra fila p/ retry."""
    out = make_out(tmp_path, spool)
    out._client = FakeClient()
    rec = make_record()
    span = spool.append(rec)
    payload = rec.to_mqtt_payload()
    out._publish(span, payload)
    assert spool.position() == (span[0], 0)  # nada acked ainda
    mid = next(iter(out._mids))
    with caplog.at_level(logging.ERROR, logger="rastro_gateway.bridge.mqtt_out"):
        out._on_publish(out._client, None, mid, _rc_puback(), None)
    # NÃO avançou, NÃO conta como entregue
    assert spool.position() == (span[0], 0)
    assert not out._mids
    # buraco preservado na FIFO do spool: acks de registros posteriores nunca
    # atravessam o record recusado (é ele que protege a watermark)
    assert spool.inflight(span)
    # record mantido p/ retry — no TOPO da fila, uma única cópia
    assert list(out._pending) == [(span, payload)]
    # log claro p/ operador (prefixo/usuário/ACL)
    assert any(
        "FALHA: publish recusado pelo broker" in r.getMessage()
        and RC_NEGADO in r.getMessage()
        for r in caplog.records
    ), f"log FALHA esperado; registros: {[r.getMessage() for r in caplog.records]}"
    # backoff de retry armado (recusa não pode virar loop quente)
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS * 2
    assert out._retry_after > time.monotonic()


def test_puback_sucesso_fora_de_success_ainda_avanca(tmp_path, spool):
    """v5 reason < 128 (ex.: 16 No matching subscribers) = aceito — o limiar é
    128, não o nome "Success": watermark avança, nada reenfileirado."""
    out = make_out(tmp_path, spool)
    out._client = FakeClient()
    rec = make_record()
    span = spool.append(rec)
    out._publish(span, rec.to_mqtt_payload())
    mid = next(iter(out._mids))
    out._on_publish(out._client, None, mid, _rc_puback(negado=False), None)
    assert spool.position() == (span[0], span[2])  # avançou
    assert not out._pending
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS  # backoff zerado
    assert out._retry_after == 0.0


def test_apos_negacao_worker_republica_e_aceite_avanca(tmp_path, spool):
    """Record negado não some nem duplica: volta 1× pra fila, o worker só
    re-tenta após o backoff (sem loop quente) e o PUBACK de sucesso avança
    a watermark — o span segue inflight, então o replay não o duplica."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    out._retry_backoff = 0.05  # backoff curto p/ teste (produto: 5 s)
    rec = make_record()
    span = spool.append(rec)
    out._publish(span, rec.to_mqtt_payload())
    mid = next(iter(out._mids))
    out._on_publish(fake, None, mid, _rc_puback(), None)
    assert len(fake.published) == 1
    # span recusado fica inflight → o replay NÃO o republica (sem duplicata)
    out._replay.set()
    t = threading.Thread(target=out._work, daemon=True)
    t.start()
    deadline = time.time() + 5
    while len(fake.published) < 2 and time.time() < deadline:
        time.sleep(0.02)
    out._stop.set()
    with out._cond:
        out._cond.notify_all()
    t.join(timeout=10)
    assert len(fake.published) == 2  # exatamente UMA re-tentativa (backoff)
    # broker passou a aceitar → PUBACK ok avança a watermark
    mid2 = next(iter(out._mids))
    out._on_publish(fake, None, mid2, None, None)
    assert spool.position() == (span[0], span[2])
    assert not out._pending
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS
    assert out._retry_after == 0.0


def test_early_ack_negado_nao_avanca_e_reenfileira(tmp_path, spool, caplog):
    """PUBACK negado chega ANTES do registro do mid (corrida de rede, janela
    B1): _after_publish precisa tratar como recusa — sem avanço, com retry."""
    out = make_out(tmp_path, spool)
    rec = make_record()
    span = spool.append(rec)
    payload = rec.to_mqtt_payload()

    class DenyClient(FakeClient):
        def publish(self, topic, pl, qos=1):
            info = super().publish(topic, pl, qos)
            # PUBACK (negado) emplacado antes de _after_publish registrar o mid
            out._on_publish(self, None, info.mid, _rc_puback(), None)
            return info

    out._client = DenyClient()
    with caplog.at_level(logging.ERROR, logger="rastro_gateway.bridge.mqtt_out"):
        out._publish(span, payload)
    assert spool.position() == (span[0], 0)  # não avançou
    assert not out._mids
    assert not out._early_acks  # consumido
    assert list(out._pending) == [(span, payload)]  # reenfileirado p/ retry
    assert spool.inflight(span)
    assert any(
        "FALHA: publish recusado pelo broker" in r.getMessage()
        for r in caplog.records
    )


def test_recusa_de_status_so_loga_sem_tocar_fila(tmp_path, spool, caplog):
    """Recusa do status online/offline: loga FALHA (é o sintoma do ACL errada)
    mas não mexe em watermark/fila/backoff — status renasce a cada connect."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    info = fake.publish("rastro/status/gateway", "{}", qos=1)
    with out._lock:
        out._ignore_mids.add(info.mid)
    antes = spool.position()
    with caplog.at_level(logging.ERROR, logger="rastro_gateway.bridge.mqtt_out"):
        out._on_publish(fake, None, info.mid, _rc_puback(), None)
    assert spool.position() == antes
    assert not out._ignore_mids  # consumido
    assert not out._pending
    assert not out._mids
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS  # backoff intacto
    assert any(
        "FALHA: publish recusado pelo broker" in r.getMessage()
        for r in caplog.records
    )


def test_build_client_mqtt_v5_e_reconexao_rapida():
    """6a: cliente fala MQTT v5 (PUBACK com reason code detectável).
    6e: reconexão 1–10 s p/ link de satélite que pisca (padrão do paho é
    1–120 s — lento demais p/ recuperação rápida)."""
    from rastro_gateway.bridge.mqtt_out import MqttConfig, build_client

    cliente = build_client(MqttConfig.from_env({}))
    assert cliente._protocol == mqtt.MQTTv5
    assert (cliente._reconnect_min_delay, cliente._reconnect_max_delay) == (1, 10)


def test_recusa_preservada_em_desconexao_e_reconexao(tmp_path, spool):
    """Registro recusado não pode ser perdido se o cliente desconectar enquanto
    estiver na fila ou em backoff; após reconectar, o worker republica e
    quando aceito, a watermark avança."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    out._retry_backoff = 0.05  # backoff curto para teste

    rec = make_record()
    span = spool.append(rec)
    payload = rec.to_mqtt_payload()

    # 1. Primeira publicação é recusada pelo broker
    out._publish(span, payload)
    out._on_publish(fake, None, 1, _rc_puback(), None)
    assert spool.position() == (span[0], 0)
    assert spool.inflight(span)
    assert list(out._pending) == [(span, payload)]

    # 2. Desconexão da rede: worker roda, encontra fake.connected == False,
    # e preserva o record recusado em _pending sem descartar
    fake.connected = False
    worker_thread = threading.Thread(target=out._work, daemon=True)
    worker_thread.start()
    time.sleep(0.3)  # deixa o worker tentar processar durante a desconexão

    # Record recusado continua preservado na fila
    with out._cond:
        assert list(out._pending) == [(span, payload)]

    # 3. Reconexão: worker republica a partir de _pending
    fake.connected = True
    with out._cond:
        out._cond.notify_all()

    deadline = time.time() + 5
    while len(fake.published) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(fake.published) == 2

    # Quando o PUBACK chega com sucesso:
    out._on_publish(fake, None, 2, 0, None)
    assert spool.position() == (span[0], span[2])  # watermark avançou!
    assert not out._mids
    assert not spool.inflight(span)

    out._stop.set()
    with out._cond:
        out._cond.notify_all()
    worker_thread.join(timeout=2)


def test_recusa_com_erro_de_enqueue_preserva_inflight(tmp_path, spool):
    """Erro de enqueue (ex. queue size cheio) em tentativa de retry de um
    record recusado NÃO chama unregister() e não permite que registros
    posteriores avancem a watermark sobre ele."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake

    rec_a, rec_b = make_record(0), make_record(1)
    span_a, span_b = spool.append(rec_a), spool.append(rec_b)

    out._publish(span_a, rec_a.to_mqtt_payload())
    out._publish(span_b, rec_b.to_mqtt_payload())
    out._on_publish(fake, None, 1, _rc_puback(), None)

    # Simula cliente com fila cheia no retry de A
    # Zera o backoff de recusa para que a tentativa de enqueue seja imediata
    with out._lock:
        out._retry_after = 0.0

    # Intercepta _stop.wait() para sincronizar APÓS o tratamento da falha de enqueue
    entrou_no_wait = threading.Event()

    class InterceptStop(threading.Event):
        def wait(self, timeout=None):
            entrou_no_wait.set()
            return super().wait(timeout)

    out._stop = InterceptStop()
    out._client = FakeClient(rc=mqtt.MQTT_ERR_QUEUE_SIZE)
    t = threading.Thread(target=out._publish, args=(span_a, rec_a.to_mqtt_payload()))
    t.start()
    try:
        assert entrou_no_wait.wait(timeout=2.0)

        # A deve continuar inflight mesmo com erro de enqueue
        assert spool.inflight(span_a)

        # B é aceito (mid 2)
        out._on_publish(out._client, None, 2, None, None)

        # A watermark NÃO pode ter ultrapassado A!
        assert spool.position() == (span_a[0], 0)
    finally:
        out._stop.set()
        t.join(timeout=2)
        assert not t.is_alive()


def test_replay_respeita_backoff_de_recusa(tmp_path, spool):
    """Replay não pode disparar rajada contra o broker se houver backoff ativo,
    inclusive quando o prazo for estendido durante a espera (provando que um
    segundo wait ocorre após a expiração do primeiro)."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    # Isola o teste do _wait_retry_backoff do _do_replay (evita que o _publish
    # interno interfira na contagem de waits do InterceptStop)
    out._publish = lambda s, p: fake.published.append((s, p))

    waits = []
    primeiro_wait_entrou = threading.Event()
    extensao_concluida = threading.Event()
    segundo_wait_entrou = threading.Event()

    class InterceptStop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                primeiro_wait_entrou.set()
                # Segura o primeiro wait até que o teste aplique a extensão
                extensao_concluida.wait(timeout=2.0)
                # Simula o término natural do primeiro prazo
                time.sleep(timeout or 0.02)
                return False
            segundo_wait_entrou.set()
            return super().wait(timeout)

    out._stop = InterceptStop()

    with out._lock:
        out._retry_after = time.monotonic() + 0.05

    for i in range(3):
        spool.append(make_record(i))

    t = threading.Thread(target=out._do_replay)
    t.start()
    try:
        # Garante que o replay entrou no primeiro wait
        assert primeiro_wait_entrou.wait(timeout=2.0)

        # Estende o prazo durante o primeiro wait
        with out._lock:
            out._retry_after = time.monotonic() + 2.0
        extensao_concluida.set()

        # Prova que o primeiro wait expirou, houve re-checagem e o segundo wait iniciou
        assert segundo_wait_entrou.wait(timeout=2.0)

        # Como o backoff foi estendido, publicações seguem bloqueadas
        assert len(fake.published) == 0
    finally:
        out._stop.set()
        t.join(timeout=2)
        assert not t.is_alive()


def test_backoff_extensao_durante_espera_publish(tmp_path, spool):
    """Se o prazo de backoff for estendido enquanto _publish espera, a publicação
    NÃO ocorre no término do prazo original — ela re-checa e entra em um segundo wait."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake

    rec = make_record()
    span = spool.append(rec)

    waits = []
    primeiro_wait_entrou = threading.Event()
    extensao_concluida = threading.Event()
    segundo_wait_entrou = threading.Event()

    class InterceptStop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                primeiro_wait_entrou.set()
                # Segura o primeiro wait até que o teste aplique a extensão
                extensao_concluida.wait(timeout=2.0)
                # Simula o término natural do primeiro prazo
                time.sleep(timeout or 0.02)
                return False
            segundo_wait_entrou.set()
            return super().wait(timeout)

    out._stop = InterceptStop()

    with out._lock:
        out._retry_after = time.monotonic() + 0.05

    t = threading.Thread(target=out._publish, args=(span, rec.to_mqtt_payload()))
    t.start()
    try:
        # Garante que _publish entrou no primeiro wait
        assert primeiro_wait_entrou.wait(timeout=2.0)

        # Estende o backoff para mais 2.0s
        with out._lock:
            out._retry_after = time.monotonic() + 2.0
        extensao_concluida.set()

        # Prova que após expirar o primeiro wait, re-checou e entrou no segundo wait
        assert segundo_wait_entrou.wait(timeout=2.0)

        # fake.published NÃO pode ter sido chamado porque o prazo foi estendido
        assert len(fake.published) == 0
    finally:
        out._stop.set()
        t.join(timeout=2)
        assert not t.is_alive()


def test_backoff_escalation_multiplas_recusas(tmp_path, spool):
    """Múltiplas recusas consecutivas dobram o backoff até o teto; sucesso zera."""
    out = make_out(tmp_path, spool)
    fake = FakeClient()
    out._client = fake
    rec = make_record()
    span = spool.append(rec)
    payload = rec.to_mqtt_payload()

    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS
    # recusa 1
    out._recusa(span, payload, _rc_puback())
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS * 2

    # recusa 2
    out._recusa(span, payload, _rc_puback())
    assert out._retry_backoff == min(mo.DENIED_BACKOFF_INIT_SECS * 4, mo.DENIED_BACKOFF_MAX_SECS)

    # simula muitas recusas até o teto
    for _ in range(10):
        out._recusa(span, payload, _rc_puback())
    assert out._retry_backoff == mo.DENIED_BACKOFF_MAX_SECS

    # sucesso zera o backoff
    out._advance(span)
    assert out._retry_backoff == mo.DENIED_BACKOFF_INIT_SECS


# --- regressão com broker REAL (mosquitto efêmero) — TODO 6a -----------------
# Broker descartável SÓ em 127.0.0.1:porta livre. PROIBIDO usar qualquer
# broker de produção (univaja-mosquitto-1, rastro-broker..., porta 8883).


def _docker_disponivel():
    import shutil

    return shutil.which("docker") is not None


def _porta_livre():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


@pytest.mark.skipif(
    not _docker_disponivel(), reason="docker indisponível — broker real pulado"
)
def test_broker_real_negando_publish_fica_na_fila(
    tmp_path, spool, caplog, monkeypatch
):
    """Mosquitto efêmero com ACL que NEGA escrita: em MQTT v5 o PUBACK vem com
    reason >= 128 e o caminho da ponte (connect/publish/_on_publish reais) não
    pode avançar a watermark — é exatamente o bug do TODO 6a (records perdidos
    em silêncio sob ACL errada). O record fica no spool p/ retry."""
    import subprocess
    import uuid as _uuid

    nome = f"rastro-test-acl-{_uuid.uuid4().hex[:10]}"
    cfgdir = tmp_path / "mosquitto"
    cfgdir.mkdir()
    (cfgdir / "mosquitto.conf").write_text(
        "listener 1883\n"
        "allow_anonymous true\n"
        "persistence false\n"
        "acl_file /mosquitto/config/acl\n"
    )
    # leitura liberada, ESCRITA sem regra = negada → publish QoS 1 apanha
    # (mosquitto responde PUBACK com reason 135 em v5; em 3.1.1 seria o
    # PUBACK "comum" que escondia a perda — o bug original)
    (cfgdir / "acl").write_text("topic read #\n")
    cfgdir.chmod(0o755)  # o processo do container precisa ler a config montada
    for arq in cfgdir.iterdir():
        arq.chmod(0o644)

    porta = _porta_livre()
    criado = False
    out = None
    try:
        try:
            r = subprocess.run(
                [
                    "docker", "run", "--rm", "-d",
                    "--name", nome,
                    "-p", f"127.0.0.1:{porta}:1883",
                    "-v", f"{cfgdir}:/mosquitto/config",
                    "eclipse-mosquitto:2",
                ],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            pytest.skip(f"broker efêmero indisponível ({exc}) — teste pulado")
        if r.returncode != 0:
            pytest.skip(
                f"broker efêmero não subiu (pull/rede?) — teste pulado: "
                f"{r.stderr.strip()[:300]}"
            )
        criado = True

        # espera o mosquitto aceitar TCP em 127.0.0.1 (nunca porta externa)
        import socket as _socket

        deadline = time.time() + 15
        no_ar = False
        while time.time() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", porta), timeout=1):
                    no_ar = True
                    break
            except OSError:
                time.sleep(0.1)
        if not no_ar:
            logs = subprocess.run(
                ["docker", "logs", nome], capture_output=True, text=True
            )
            pytest.fail(f"mosquitto efêmero não abriu a porta: {logs.stderr[:500]}")

        # Só o build_client é trocado (o broker de teste é texto plano, sem
        # TLS — produção continua TLS-obrigatório); MqttOut inteiro
        # (connect/publish/_on_publish/worker) é o de produção.
        def _sem_tls(cfg):
            cliente = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=cfg.client_id,
                protocol=mqtt.MQTTv5,
                transport=cfg.transport,
            )
            cliente.reconnect_delay_set(min_delay=1, max_delay=10)
            return cliente

        monkeypatch.setattr(mo, "build_client", _sem_tls)
        cfg = mo.MqttConfig(
            host="127.0.0.1", port=porta, username=None, password=None,
            ca_cert=None, client_id="rastro-test-acl", topic_prefix="rastro",
            keepalive_secs=10,
        )
        out = mo.MqttOut(cfg, spool)
        caplog.set_level(logging.ERROR, logger="rastro_gateway.bridge.mqtt_out")
        out.connect()
        deadline = time.time() + 15
        while time.time() < deadline and not (
            out._client is not None and out._client.is_connected()
        ):
            time.sleep(0.05)
        assert (
            out._client is not None and out._client.is_connected()
        ), "não conectou ao mosquitto efêmero"
        # deixa o replay do connect terminar no spool vazio antes do teste
        # (senão ele e o _publish do teste publicariam o mesmo span)
        deadline = time.time() + 5
        while out._replay.is_set() and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        # publica pelo caminho real — worker ocioso (fila vazia, replay vazio)
        rec = make_record()
        span = spool.append(rec)
        out._publish(span, rec.to_mqtt_payload())

        # a recusa chega como PUBACK v5 negado → record volta pra fila c/ backoff
        deadline = time.time() + 15
        reenfileirado = False
        while time.time() < deadline:
            with out._cond:
                reenfileirado = any(s == span for s, _pl in out._pending)
            with out._lock:
                sem_mid = not out._mids
            if reenfileirado and sem_mid:
                break
            time.sleep(0.05)
        assert reenfileirado, "PUBACK negado não reenfileirou o record"
        assert spool.position() == (span[0], 0)  # watermark NUNCA avançou
        assert spool.inflight(span)  # buraco preservado na FIFO do spool
        assert not out._mids  # não conta como entregue
        assert any(
            "FALHA: publish recusado pelo broker" in r.getMessage()
            for r in caplog.records
        ), "log FALHA em PT-BR esperado"
        # backoff armado só pelo caminho de DADO (_recusa); status não arma
        assert out._retry_backoff > mo.DENIED_BACKOFF_INIT_SECS
    finally:
        if out is not None:
            try:
                out.stop()
            except Exception:
                pass
        if criado:
            # ALWAYS remove o broker descartável, mesmo no caminho de falha
            subprocess.run(
                ["docker", "rm", "-f", nome], capture_output=True, timeout=60
            )
