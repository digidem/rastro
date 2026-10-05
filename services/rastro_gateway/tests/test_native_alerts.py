"""native/alerts: máquina de estados e janela de silêncio (WP-D).

Funções puras sobre dicts/epochs — sem banco, sem rede. Fuso/janela/lata
limiares via env (monkeypatch). Mesmo estilo de tests/test_native_service.py.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_gateway.native import alerts

NOW = 1_800_000_000


def _no(num, *, last_seen, first_seen=None, fix_time=None, lat_i=None,
        lon_i=None, prev_time=None, prev_lat_i=None, prev_lon_i=None):
    """Entrada de last_fix_by_node (formato do Db.node_activity)."""
    return num, {
        "first_seen": first_seen if first_seen is not None else NOW - 3600,
        "last_seen": last_seen,
        "fix_time": fix_time,
        "lat_i": lat_i,
        "lon_i": lon_i,
        "prev_time": prev_time,
        "prev_lat_i": prev_lat_i,
        "prev_lon_i": prev_lon_i,
    }


# --- gateway_mudo ------------------------------------------------------------

def test_gateway_mudo_dispara_apos_silencio_e_limpa_quando_volta(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    eventos = alerts.evaluate(
        NOW,
        [],
        {0xA0000009: NOW - 3601},
        {},
        {},
    )
    raise_evt = [e for e in eventos if e.action == "raise"]
    assert [e.kind for e in raise_evt] == ["gateway_mudo"]
    # dentro do limiar: nada
    eventos = alerts.evaluate(NOW, [], {0xA0000009: NOW - 3600}, {}, {})
    assert eventos == []  # > estrito: exatamente 3600 s não dispara
    # limpeza: uplink chega → condição resolvida, linha ativa é limpa
    ativa = [{"key": "gateway_mudo:0xa0000009", "node_num": 0xA0000009,
              "kind": "gateway_mudo", "since": NOW - 7200, "cleared_at": None}]
    ativa[0]["key"] = "gateway_mudo:" + str(0xA0000009)
    eventos = alerts.evaluate(NOW, ativa, {0xA0000009: NOW - 10}, {}, {})
    assert [e.action for e in eventos] == ["clear"]

def test_movel_ausente_so_movel(monkeypatch):
    monkeypatch.setenv(alerts.ENV_MOBILE_ABSENT_SECS, "7200")
    num = 0xA00000AA
    eventos = alerts.evaluate(NOW, [], {}, dict([_no(num, last_seen=NOW - 7201)]), {})
    assert [e.kind for e in eventos] == ["movel_ausente"]
    # classe fixa (via dict de node_kinds — evaluate não consulta env): nada
    eventos = alerts.evaluate(NOW, [], {}, dict([_no(num, last_seen=NOW - 7201)]),
                              {num: alerts.TIPO_FIXO})
    assert eventos == []



# --- sem_fix / posicao_parada -------------------------------------------------

def test_sem_fix_dispara_com_fix_atrasado_ou_inexistente(monkeypatch):
    monkeypatch.setenv(alerts.ENV_NOFIX_SECS, "3600")
    num = 0xA00000BB
    # vivo (última atividade 60 s atrás), fix atrasado (gap 2 h) → dispara
    nos = dict([_no(num, last_seen=NOW - 60, fix_time=NOW - 7260,
                    lat_i=0, lon_i=0)])
    eventos = alerts.evaluate(NOW, [], {}, nos, {})
    assert [e.kind for e in eventos] == ["sem_fix"]
    # fix fresco: nada
    nos = dict([_no(num, last_seen=NOW - 60, fix_time=NOW - 120,
                    lat_i=0, lon_i=0)])
    assert alerts.evaluate(NOW, [], {}, nos, {}) == []
    # sem fix NENHUM e vivo desde antes: dispara (gap = agora - first_seen)
    nos = dict([_no(num, last_seen=NOW - 60, first_seen=NOW - 7200)])
    assert [e.kind for e in alerts.evaluate(NOW, [], {}, nos, {})] == ["sem_fix"]
    # nó parou de enviar (last_seen 2 h): NÃO é sem_fix (não está enviando)
    nos = dict([_no(num, last_seen=NOW - 7200)])
    assert alerts.evaluate(NOW, [], {}, nos, {}) == []


def test_posicao_parada_so_classe_fixa_com_deslocamento_pequeno(monkeypatch):
    monkeypatch.setenv(alerts.ENV_PARKED_METERS, "30")
    monkeypatch.setenv(alerts.ENV_PARKED_SECS, "7200")
    num = 0xA00000CC
    fixos = {num: alerts.TIPO_FIXO}
    # dois fixes 3 h de distância, ~0 m de deslocamento, último fix fresco
    nos = dict([_no(num, last_seen=NOW - 60, fix_time=NOW - 60,
                    lat_i=0, lon_i=0, prev_time=NOW - 60 - 3 * 3600,
                    prev_lat_i=1, prev_lon_i=1)])
    eventos = alerts.evaluate(NOW, [], {}, nos, fixos)
    assert [e.kind for e in eventos] == ["posicao_parada"]
    # deslocamento grande (0.001 grau ≈ 111 m por eixo): não é parada
    nos = dict([_no(num, last_seen=NOW - 60, fix_time=NOW - 60,
                    lat_i=10000, lon_i=10000, prev_time=NOW - 60 - 3 * 3600,
                    prev_lat_i=0, prev_lon_i=0)])
    assert alerts.evaluate(NOW, [], {}, nos, fixos) == []
    # classe móvel com dados parados: posicao_parada não se aplica
    assert alerts.evaluate(NOW, [], {}, nos, {num: alerts.TIPO_MOVEL}) == []
    # só um fix: sem deslocamento mensurável — não avalia
    nos = dict([_no(num, last_seen=NOW - 60, fix_time=NOW - 60, lat_i=0, lon_i=0)])
    assert alerts.evaluate(NOW, [], {}, nos, fixos) == []


# --- máquina de estados --------------------------------------------------------

def _linha_ativa(kind, num, since=NOW - 7200):
    return {"key": f"{kind}:{num}", "node_num": num, "kind": kind,
            "since": since, "cleared_at": None}


def test_maquina_de_estados_dispara_uma_vez_e_nao_redispara(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    gw = 0xA0000009
    mudo = {gw: NOW - 4000}
    # 1) sem linha ativa → dispara
    ev1 = alerts.evaluate(NOW, [], mudo, {}, {})
    assert [(e.action, e.kind, e.key) for e in ev1] == [
        ("raise", "gateway_mudo", f"gateway_mudo:{gw}")]
    # 2) linha ativa + mesma condição → NÃO dispara de novo
    ativa = [_linha_ativa("gateway_mudo", gw)]
    assert alerts.evaluate(NOW, ativa, mudo, {}, {}) == []
    # 3) uplink chega → limpa
    ev3 = alerts.evaluate(NOW, ativa, {gw: NOW - 10}, {}, {})
    assert [e.action for e in ev3] == ["clear"]
    # 4) persiste a limpeza e volta a ficar mudo → dispara de novo
    limpa = [_linha_ativa("gateway_mudo", gw)]
    limpa[0]["cleared_at"] = NOW
    ev4 = alerts.evaluate(NOW + 7200, limpa, {gw: NOW + 10 - 4010}, {}, {})
    assert [e.action for e in ev4] == ["raise"]


# --- janela de silêncio --------------------------------------------------------

def _mudo_eventos(now, gw=0xA0000009):
    return alerts.evaluate(now, [], {gw: now - 4000}, {}, {})


def test_janela_silencio_suprime_disparo_e_cruza_meia_noite(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    monkeypatch.setenv(alerts.ENV_QUIET_START, "22:00")
    monkeypatch.setenv(alerts.ENV_QUIET_END, "06:00")
    monkeypatch.setenv(alerts.ENV_TZ, "UTC")
    base = 1_800_000_000  # 2027-01-15 08:00:00 UTC
    # 08:00 e 12:00: fora da janela → dispara
    assert [e.action for e in _mudo_eventos(base)] == ["raise"]
    assert [e.action for e in _mudo_eventos(base + 4 * 3600)] == ["raise"]
    # 22:00 (início, inclusivo) até 05:xx: dentro (janela cruza a meia-noite)
    for delta_h in (14, 15, 16, 18, 21):
        assert _mudo_eventos(base + delta_h * 3600) == [], delta_h
    # 05:59:59 dentro; 06:00 (fim, exclusivo) fora
    assert _mudo_eventos(base + 21 * 3600 + 3599) == []
    assert [e.action for e in _mudo_eventos(base + 22 * 3600)] == ["raise"]


def test_janela_silencio_usa_fuso_e_so_alguns_kinds(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    monkeypatch.setenv(alerts.ENV_NOFIX_SECS, "3600")
    monkeypatch.setenv(alerts.ENV_QUIET_START, "22:00")
    monkeypatch.setenv(alerts.ENV_QUIET_END, "06:00")
    # 08:00 UTC = 05:00 em America/Sao_Paulo (janela noturna lá)
    monkeypatch.setenv(alerts.ENV_TZ, "America/Sao_Paulo")
    base = 1_800_000_000
    assert _mudo_eventos(base) == []  # gateway_mudo suprimido
    # sem_fix NUNCA é suprimido pela janela
    nos = dict([_no(0xA00000BB, last_seen=base - 60, fix_time=base - 7260,
                    lat_i=0, lon_i=0)])
    evs = alerts.evaluate(base, [], {}, nos, {})
    assert [e.kind for e in evs] == ["sem_fix"]
    # limpeza (clear) passa mesmo dentro da janela: linha ativa + uplink fresco
    ativa = [_linha_ativa("gateway_mudo", 0xA0000009)]
    evs = alerts.evaluate(base, ativa, {0xA0000009: base - 10}, {}, {})
    assert [e.action for e in evs] == ["clear"]


# --- run_alert_cycle -----------------------------------------------------------

class FakeAlertDb:
    """Agregados programáveis + save_alert_events que grava as chamadas."""

    def __init__(self, linhas, uplinks, atividade):
        self.linhas = linhas
        self.uplinks = uplinks
        self.atividade = atividade
        self.saved = []

    def alert_state_rows(self):
        return self.linhas

    def gateway_uplink_times(self):
        return self.uplinks

    def node_activity(self):
        return self.atividade

    def save_alert_events(self, raised, cleared, now):
        self.saved.append((list(raised), list(cleared), now))
        for r in raised:  # espelha a persistência real em alert_state
            self.linhas.append({"key": r["key"], "node_num": r["node_num"],
                                "kind": r["kind"], "since": r["since"],
                                "cleared_at": None})
        for c in cleared:
            for linha in self.linhas:
                if linha["key"] == c["key"] and linha["cleared_at"] is None:
                    linha["cleared_at"] = c["cleared_at"]


def test_run_alert_cycle_grava_raises_e_clears(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    monkeypatch.delenv(alerts.ENV_QUIET_START, raising=False)
    monkeypatch.delenv(alerts.ENV_QUIET_END, raising=False)
    gw = 0xA0000009
    # 1º ciclo: gateway mudo → 1 raise gravado
    db = FakeAlertDb([], {gw: NOW - 4000}, {})
    resumo = alerts.run_alert_cycle(db, NOW)
    assert resumo == {"raised": 1, "cleared": 0}
    raised, cleared, agora = db.saved[-1]
    assert raised[0]["kind"] == "gateway_mudo"
    assert raised[0]["key"] == f"gateway_mudo:{gw}"
    assert cleared == []
    # 2º ciclo, mesmo estado: nada novo → save NÃO é chamado
    assert alerts.run_alert_cycle(db, NOW) == {"raised": 0, "cleared": 0}
    assert len(db.saved) == 1
    # 3º ciclo: uplink chegou → clear gravado
    db.uplinks = {gw: NOW - 10}
    db.linhas = [_linha_ativa("gateway_mudo", gw)]
    assert alerts.run_alert_cycle(db, NOW) == {"raised": 0, "cleared": 1}
    _raised, cleared, _agora = db.saved[-1]
    assert cleared == [{"key": f"gateway_mudo:{gw}", "cleared_at": NOW}]


# --- node_kinds_padrao / haversine ----------------------------------------------

def test_node_kinds_padrao_aceita_hex_decimal_e_ignora_invalido(monkeypatch):
    monkeypatch.setenv(alerts.ENV_FIXED_NODES, "!a00000aa, 42, xpto, ,!A00000AB")
    kinds = alerts.node_kinds_padrao()
    assert kinds == {0xA00000AA: "fixed", 42: "fixed", 0xA00000AB: "fixed"}


def test_haversine_m_sanity():
    # ~111.2 km por grau de latitude no equador; 0.001 grau ≈ 111 m
    d = alerts.haversine_m(0.0, 0.0, 0.001, 0.0)
    assert 110 < d < 113
    assert alerts.haversine_m(10.0, 20.0, 10.0, 20.0) == 0.0
