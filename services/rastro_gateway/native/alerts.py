"""Alertas do ingest nativo (WP-D): funções puras + persistência em ``alert_state``.

Somente AVALIA e registra estado. Entrega (push/e-mail) está fora de escopo.
Nada aqui publica no broker e nada aqui lê payload de mensagem: as entradas
são agregados do banco (último uplink por gateway, energia por nó) e as saídas
são eventos de subida/limpeza de estado — seguros para log (kinds + contagens).

Máquina de estados (por chave ``kind:subject``):
- dispara uma vez (``since`` = agora) quando a condição passa a valer;
- não dispara de novo enquanto ativa (linha com ``cleared_at`` NULL);
- quando a condição deixa de valer, grava ``cleared_at`` (limpeza).

Configuração (lida do ambiente a cada ciclo, como em ``envelope._casa_ajuda``):
- ``RASTRO_GATEWAY_SILENT_SECS`` (3600)  — gateway_mudo
- ``RASTRO_BATTERY_LOW_PCT``      (20)   — bateria_critica (%)
- ``RASTRO_BATTERY_MIN_VOLTS``    (3.55) — bateria_critica (V)
- ``RASTRO_FIXED_NODES`` — nós da classe "fixa" (números decimais ou ``!hex``,
  separados por vírgula). Todo nó fora da lista é da classe móvel (barcos se
  movem; o schema não tem coluna de classe — fica no ambiente).

Semântica de cada kind (detection = condição ativa agora):
- ``gateway_mudo``: gateway conhecido sem uplink há mais de gateway_silent_secs.
- ``bateria_critica``: nó FIXO (repetidor/base solar) com ``battery_level < 20``
  ou ``voltage < 3.55 V`` na leitura mais recente de ``node_power``. Nó sem
  leitura não avalia; nós móveis nunca avaliam (bateria trocada em campo,
  não é condição operacional).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Limiares (segundos / % / volts) lidos do ambiente a cada ciclo.
ENV_GATEWAY_SILENT_SECS = "RASTRO_GATEWAY_SILENT_SECS"
ENV_BATTERY_LOW_PCT = "RASTRO_BATTERY_LOW_PCT"
ENV_BATTERY_MIN_VOLTS = "RASTRO_BATTERY_MIN_VOLTS"
ENV_FIXED_NODES = "RASTRO_FIXED_NODES"
ENV_ALERTS_ENABLED = "RASTRO_ALERTS_ENABLED"
ENV_INTERVALO_SECS = "RASTRO_ALERT_INTERVAL_SECS"

GATEWAY_SILENT_PADRAO_SECS = 3600.0
BATERIA_CRITICA_PCT_PADRAO = 20.0
BATERIA_CRITICA_VOLTS_PADRAO = 3.55
INTERVALO_PADRAO_SECS = 60.0

KIND_GATEWAY_MUDO = "gateway_mudo"
KIND_BATERIA_CRITICA = "bateria_critica"

# Kinds aposentados (plano 2026-10-08): o motor não os avalia mais; o ciclo
# limpa linhas ativas antigas via ``Db.retire_alert_kinds`` (idempotente).
KINDS_APOSENTADOS = ("sem_fix", "posicao_parada", "movel_ausente")
KIND_BATERIA_CRITICA = "bateria_critica"

TIPO_FIXO = "fixed"
TIPO_MOVEL = "mobile"


@dataclass(frozen=True)
class AlertEvent:
    """Evento de estado de alerta: ``raise`` (novo) ou ``clear`` (resolvido)."""

    action: str  # 'raise' | 'clear'
    kind: str
    key: str
    node_num: int | None


@dataclass(frozen=True)
class _Config:
    gateway_silent_secs: float
    battery_low_pct: float
    battery_min_volts: float


def _float_env(env: dict, nome: str, padrao: float) -> float:
    bruto = env.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        valor = float(bruto)
    except ValueError:
        log.warning("AVISO: %s inválido (%r) — usando padrão %s", nome, bruto, padrao)
        return padrao
    return valor if valor >= 0 else padrao


def _config(env: dict | None = None) -> _Config:
    env = os.environ if env is None else env
    return _Config(
        gateway_silent_secs=_float_env(
            env, ENV_GATEWAY_SILENT_SECS, GATEWAY_SILENT_PADRAO_SECS
        ),
        battery_low_pct=_float_env(env, ENV_BATTERY_LOW_PCT, BATERIA_CRITICA_PCT_PADRAO),
        battery_min_volts=_float_env(
            env, ENV_BATTERY_MIN_VOLTS, BATERIA_CRITICA_VOLTS_PADRAO
        ),
    )


def node_kinds_padrao(env: dict | None = None) -> dict[int, str]:
    """``RASTRO_FIXED_NODES`` → {node_num: 'fixed'}; todo o resto é móvel."""
    env = os.environ if env is None else env
    bruto = env.get(ENV_FIXED_NODES, "")
    fixos: dict[int, str] = {}
    for bruta in bruto.split(","):
        bruta = bruta.strip()
        if not bruta:
            continue
        texto = bruta[1:] if bruta.startswith("!") else bruta
        try:
            num = int(texto, 16) if bruta.startswith("!") else int(texto, 10)
        except ValueError:
            log.warning("AVISO: %s com entrada inválida ignorada: %s", ENV_FIXED_NODES, bruta)
            continue
        fixos[num] = TIPO_FIXO
    return fixos


def _chave(kind: str, sujeito: int) -> str:
    return f"{kind}:{sujeito}"


def _campo_valido(valor: object, minimo: float, maximo: float | None) -> float | None:
    """Número finito dentro dos limites; ausente/lixo → None.

    Leitura IMPOSSÍVEL (ex.: ``101%``/``-0.001 V`` de sensor quebrado) não é
    evidência de nada: não dispara e — pelo mesmo motivo — não limpa
    (preserve-until-plausible, consulta ao senior 2026-10-08).
    """
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return None
    v = float(valor)
    if not math.isfinite(v) or v < minimo or (maximo is not None and v > maximo):
        return None
    return v


def evaluate(
    now: float,
    state_rows: list,
    last_uplink_by_gateway: dict,
    node_power_by_node: dict,
    node_kinds: dict,
) -> list[AlertEvent]:
    """Avalia as 2 condições e devolve eventos de raise/clear (função pura).

    ``state_rows``: linhas de ``alert_state`` como dicts com
    ``key/node_num/kind/since/cleared_at`` (epochs float ou None).
    ``last_uplink_by_gateway``: {gateway_num: epoch_do_último_uplink}.
    ``node_power_by_node``: {node_num: dict} com ``battery_level``/``voltage``
    (None = sem leitura daquele campo; ausente do dict = sem telemetria;
    valores impossíveis — p.ex. ``101%``/``-0.001 V`` — são descartados como lixo).
    ``node_kinds``: {node_num: 'fixed' | 'mobile'}; ausente = móvel.
    """
    cfg = _config()
    ativos = {row["key"]: row for row in state_rows if row.get("cleared_at") is None}
    deteccoes: dict[str, tuple[str, int | None, bool]] = {}

    for gw, ultimo in sorted(last_uplink_by_gateway.items()):
        mudo = (now - ultimo) > cfg.gateway_silent_secs
        deteccoes[_chave(KIND_GATEWAY_MUDO, gw)] = (
            KIND_GATEWAY_MUDO,
            gw,
            "critica" if mudo else "saudavel",
        )

    for num, leitura in sorted((node_power_by_node or {}).items()):
        fixo = (node_kinds or {}).get(num, TIPO_MOVEL) == TIPO_FIXO
        # bateria_critica: só classe fixa (repetidor/base solar). "OU" entre os
        # dois limiares: cada um dispara sozinho. Campos impossíveis são
        # descartados ANTES do limiar; lixo puro (nenhum campo plausível) não
        # dispara E não limpa — preserva o estado ativo (último conhecido).
        estado = "ignora"
        if fixo and leitura:
            bateria = _campo_valido(leitura.get("battery_level"), 0.0, 100.0)
            volts = _campo_valido(leitura.get("voltage"), 0.0, None)
            if (bateria is not None and bateria < cfg.battery_low_pct) or (
                volts is not None and volts < cfg.battery_min_volts
            ):
                estado = "critica"
            elif bateria is not None or volts is not None:
                # pelo menos um campo plausível, nenhum abaixo do limiar
                estado = "saudavel"
        deteccoes[_chave(KIND_BATERIA_CRITICA, num)] = (
            KIND_BATERIA_CRITICA,
            num,
            estado,
        )

    eventos: list[AlertEvent] = []
    for key in sorted(deteccoes):
        kind, sujeito, estado = deteccoes[key]
        if estado == "ignora":
            continue  # sem evidência plausível: preserva raise/clear como está
        detectado = estado == "critica"
        ativo = ativos.get(key)
        if detectado and ativo is None:
            eventos.append(AlertEvent(action="raise", kind=kind, key=key, node_num=sujeito))
        elif not detectado and ativo is not None:
            eventos.append(AlertEvent(action="clear", kind=kind, key=key, node_num=sujeito))
    return eventos


def run_alert_cycle(db, now: float) -> dict:
    """Um ciclo completo: lê estado, avalia e grava raises/clears em alert_state.

    Hook periódico — NINGUÉM agenda por padrão; o ``__main__`` só inicia o
    temporizador com ``RASTRO_ALERTS_ENABLED=1``. Retorna contagens (kinds não
    entram no log aqui — quem loga é o chamador, se quiser, com kinds only).
    """
    # Aposentados primeiro: linha antiga ativa recebe cleared_at uma vez.
    aposentados = db.retire_alert_kinds(list(KINDS_APOSENTADOS), now)
    eventos = evaluate(
        now,
        db.alert_state_rows(),
        db.gateway_uplink_times(),
        db.node_power_readings(),
        node_kinds_padrao(),
    )
    raised = [
        {"key": e.key, "node_num": e.node_num, "kind": e.kind, "since": now}
        for e in eventos
        if e.action == "raise"
    ]
    cleared = [{"key": e.key, "cleared_at": now} for e in eventos if e.action == "clear"]
    if raised or cleared:
        db.save_alert_events(raised, cleared, now)
    return {"raised": len(raised), "cleared": len(cleared), "aposentados": aposentados}
