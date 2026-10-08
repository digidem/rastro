"""native/alerts: máquina de estados e limiares (WP-D enxuto).

Funções puras sobre dicts/epochs — sem banco, sem rede. Limiares via env
(monkeypatch). Mesmo estilo de tests/test_native_service.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_gateway.native import alerts

NOW = 1_800_000_000
GW = 0xA0000009
FIXO = 0xA00000AA


# --- gateway_mudo ------------------------------------------------------------

def test_gateway_mudo_dispara_apos_silencio_e_limpa_quando_volta(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    eventos = alerts.evaluate(NOW, [], {GW: NOW - 3601}, {}, {})
    raise_evt = [e for e in eventos if e.action == "raise"]
    assert [e.kind for e in raise_evt] == ["gateway_mudo"]
    # dentro do limiar: nada (">" estrito: exatamente 3600 s não dispara)
    eventos = alerts.evaluate(NOW, [], {GW: NOW - 3600}, {}, {})
    assert [e for e in eventos if e.action == "raise"] == []
    # limpeza: uplink chega → condição resolvida, linha ativa é limpa
    ativa = [{
        "key": "gateway_mudo:%d" % GW,
        "node_num": GW,
        "kind": "gateway_mudo",
        "since": NOW - 7200,
        "cleared_at": None,
    }]
    eventos = alerts.evaluate(NOW, ativa, {GW: NOW - 10}, {}, {})
    assert [e.action for e in eventos] == ["clear"]


# --- bateria_critica ----------------------------------------------------------

def test_bateria_critica_dispara_por_percentual_ou_tensao(monkeypatch):
    monkeypatch.setenv(alerts.ENV_BATTERY_LOW_PCT, "20")
    monkeypatch.setenv(alerts.ENV_BATTERY_MIN_VOLTS, "3.55")
    fixos = {FIXO: alerts.TIPO_FIXO}

    def pwr(bat=None, volts=None):
        return {FIXO: {"battery_level": bat, "voltage": volts}}

    # bateria 19% dispara; exatamente 20% não (">" estrito)
    evs = alerts.evaluate(NOW, [], {}, pwr(bat=19.0), fixos)
    assert [e.kind for e in evs] == ["bateria_critica"]
    assert alerts.evaluate(NOW, [], {}, pwr(bat=20.0), fixos) == []
    # tensão 3.54 dispara; exatamente 3.55 não
    evs = alerts.evaluate(NOW, [], {}, pwr(volts=3.54), fixos)
    assert [e.kind for e in evs] == ["bateria_critica"]
    assert alerts.evaluate(NOW, [], {}, pwr(volts=3.55), fixos) == []
    # "OU": cada limiar dispara sozinho; None nunca dispara sozinho
    evs = alerts.evaluate(NOW, [], {}, pwr(bat=15.0, volts=4.10), fixos)
    assert [e.kind for e in evs] == ["bateria_critica"]
    assert alerts.evaluate(NOW, [], {}, pwr(bat=None, volts=None), fixos) == []
    # nó móvel com leitura ruim: nunca avalia
    evs = alerts.evaluate(NOW, [], {}, pwr(bat=5.0), {FIXO: alerts.TIPO_MOVEL})
    assert evs == []
    # nó fixo sem leitura: não avalia
    assert alerts.evaluate(NOW, [], {}, {}, fixos) == []


def test_bateria_limiares_ajustaveis_por_env(monkeypatch):
    monkeypatch.setenv(alerts.ENV_BATTERY_LOW_PCT, "35")
    monkeypatch.setenv(alerts.ENV_BATTERY_MIN_VOLTS, "3.60")
    fixos = {FIXO: alerts.TIPO_FIXO}
    leitura = {FIXO: {"battery_level": 34.0, "voltage": 4.10}}
    evs = alerts.evaluate(NOW, [], {}, leitura, fixos)
    assert [e.kind for e in evs] == ["bateria_critica"]
    leitura = {FIXO: {"battery_level": 40.0, "voltage": 3.59}}
    evs = alerts.evaluate(NOW, [], {}, leitura, fixos)
    assert [e.kind for e in evs] == ["bateria_critica"]


# --- máquina de estados ---------------------------------------------------------

def _linha_ativa(kind, num, since=NOW - 7200):
    return {"key": "%s:%d" % (kind, num), "node_num": num, "kind": kind,
            "since": since, "cleared_at": None}


def test_maquina_de_estados_dispara_uma_vez_e_nao_redispara(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    mudo = {GW: NOW - 4000}
    # 1) sem linha ativa → dispara
    ev1 = alerts.evaluate(NOW, [], mudo, {}, {})
    assert [(e.action, e.kind, e.key) for e in ev1] == [
        ("raise", "gateway_mudo", "gateway_mudo:%d" % GW)]
    # 2) linha ativa + mesma condição → NÃO dispara de novo
    ativa = [_linha_ativa("gateway_mudo", GW)]
    assert alerts.evaluate(NOW, ativa, mudo, {}, {}) == []
    # 3) uplink chega → limpa
    ev3 = alerts.evaluate(NOW, ativa, {GW: NOW - 10}, {}, {})
    assert [e.action for e in ev3] == ["clear"]
    # 4) persiste a limpeza e volta a ficar mudo → dispara de novo
    limpa = [_linha_ativa("gateway_mudo", GW)]
    limpa[0]["cleared_at"] = NOW
    ev4 = alerts.evaluate(NOW + 7200, limpa, {GW: NOW + 3190}, {}, {})
    assert [e.action for e in ev4] == ["raise"]


# --- run_alert_cycle -------------------------------------------------------------

class FakeAlertDb:
    """Agregados programáveis + save_alert_events que grava as chamadas."""

    def __init__(self, linhas, uplinks, energia):
        self.linhas = linhas
        self.uplinks = uplinks
        self.energia = energia
        self.saved = []
        self.retired = []

    def alert_state_rows(self):
        return self.linhas

    def gateway_uplink_times(self):
        return self.uplinks

    def node_power_readings(self):
        return self.energia

    def retire_alert_kinds(self, kinds, now):
        self.retired.append(list(kinds))
        n = 0
        for linha in self.linhas:
            if linha["kind"] in kinds and linha["cleared_at"] is None:
                linha["cleared_at"] = now
                n += 1
        return n

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
    # 1º ciclo: gateway mudo → 1 raise gravado
    db = FakeAlertDb([], {GW: NOW - 4000}, {})
    resumo = alerts.run_alert_cycle(db, NOW)
    assert resumo == {"raised": 1, "cleared": 0, "aposentados": 0}
    raised, cleared, _agora = db.saved[-1]
    assert raised[0]["kind"] == "gateway_mudo"
    assert raised[0]["key"] == "gateway_mudo:%d" % GW
    assert cleared == []
    # 2º ciclo, mesmo estado: nada novo → save NÃO é chamado
    assert alerts.run_alert_cycle(db, NOW) == {"raised": 0, "cleared": 0, "aposentados": 0}
    assert len(db.saved) == 1
    # 3º ciclo: uplink chegou → clear gravado
    db.uplinks = {GW: NOW - 10}
    db.linhas = [_linha_ativa("gateway_mudo", GW)]
    assert alerts.run_alert_cycle(db, NOW) == {"raised": 0, "cleared": 1, "aposentados": 0}
    _raised, cleared, _agora = db.saved[-1]
    assert cleared == [{"key": "gateway_mudo:%d" % GW, "cleared_at": NOW}]


# --- node_kinds_padrao ------------------------------------------------------------

def test_node_kinds_padrao_aceita_hex_decimal_e_ignora_invalido(monkeypatch):
    monkeypatch.setenv(alerts.ENV_FIXED_NODES, "!a00000aa, 42, xpto, ,!A00000AB")
    kinds = alerts.node_kinds_padrao()
    assert kinds == {0xA00000AA: "fixed", 42: "fixed", 0xA00000AB: "fixed"}


def test_run_alert_cycle_bateria_critica_end_to_end(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    monkeypatch.setenv(alerts.ENV_FIXED_NODES, str(FIXO))
    energia = {FIXO: {"battery_level": 12.0, "voltage": 3.40}}
    db = FakeAlertDb([], {}, energia)
    resumo = alerts.run_alert_cycle(db, NOW)
    assert resumo == {"raised": 1, "cleared": 0, "aposentados": 0}
    raised, _cleared, _agora = db.saved[-1]
    assert raised[0]["kind"] == "bateria_critica"
    assert raised[0]["key"] == "bateria_critica:%d" % FIXO


def test_run_alert_cycle_aposenta_kinds_removidos(monkeypatch):
    monkeypatch.setenv(alerts.ENV_GATEWAY_SILENT_SECS, "3600")
    db = FakeAlertDb(
        [_linha_ativa("sem_fix", 0xA00000BB), _linha_ativa("gateway_mudo", GW)],
        {GW: NOW - 4000},
        {},
    )
    resumo = alerts.run_alert_cycle(db, NOW)
    assert resumo["aposentados"] == 1
    por_kind = {l["kind"]: l for l in db.linhas}
    assert por_kind["sem_fix"]["cleared_at"] == NOW
    assert por_kind["gateway_mudo"]["cleared_at"] is None  # ativo permanece
    # idempotente: 2º ciclo não aposenta nada de novo
    assert alerts.run_alert_cycle(db, NOW)["aposentados"] == 0
