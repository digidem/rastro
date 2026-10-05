"""Alertas do ingest nativo (WP-D): funções puras + persistência em ``alert_state``.

Somente AVALIA e registra estado. Entrega (push/e-mail) está fora de escopo.
Nada aqui publica no broker e nada aqui lê payload de mensagem: as entradas
são agregados do banco (último uplink por gateway, fixes por nó) e as saídas
são eventos de subida/limpeza de estado — seguros para log (kinds + contagens).

Máquina de estados (por chave ``kind:subject``):
- dispara uma vez (``since`` = agora) quando a condição passa a valer;
- não dispara de novo enquanto ativa (linha com ``cleared_at`` NULL);
- quando a condição deixa de valer, grava ``cleared_at`` (limpeza).

Configuração (lida do ambiente a cada ciclo, como em ``envelope._casa_ajuda``):
- ``RASTRO_GATEWAY_SILENT_SECS`` (3600)  — gateway_mudo
- ``RASTRO_PARKED_METERS``        (30)   — posicao_parada (haversine)
- ``RASTRO_PARKED_SECS``          (7200) — posicao_parada
- ``RASTRO_NOFIX_SECS``           (3600) — sem_fix
- ``RASTRO_MOBILE_ABSENT_SECS``   (7200) — movel_ausente
- ``RASTRO_QUIET_START``/``RASTRO_QUIET_END`` (HH:MM, fuso ``RASTRO_TZ``):
  suprimem APENAS o disparo (raise) de ``gateway_mudo``/``movel_ausente``;
  janela cruzando meia-noite funciona (início > fim).
- ``RASTRO_FIXED_NODES`` — nós da classe "fixa" (números decimais ou ``!hex``,
  separados por vírgula). Todo nó fora da lista é da classe móvel (barcos se
  movem; o schema não tem coluna de classe — fica no ambiente).

Semântica de cada kind (detection = condição ativa agora):
- ``gateway_mudo``: gateway conhecido sem uplink há mais de gateway_silent_secs.
- ``posicao_parada``: nó FIXO com último fix fresco (≤ parked_secs), dois fixes
  consecutivos separados por ≥ parked_secs e deslocamento haversine entre eles
  < parked_meters. Com um único fix não há deslocamento mensurável: não avalia.
- ``sem_fix``: nó vivo (last_seen dentro de nofix_secs) que está há mais de
  nofix_secs sem fix válido (gap = last_seen − fix_time; sem fix nenhum,
  gap = agora − first_seen). Nó parado de enviar NÃO avalia (não está enviando).
- ``movel_ausente``: nó MÓVEL sem atividade (nodes.last_seen) há mais de
  mobile_absent_secs.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Limiares (segundos / metros) e janela de silêncio noturno.
ENV_GATEWAY_SILENT_SECS = "RASTRO_GATEWAY_SILENT_SECS"
ENV_PARKED_METERS = "RASTRO_PARKED_METERS"
ENV_PARKED_SECS = "RASTRO_PARKED_SECS"
ENV_NOFIX_SECS = "RASTRO_NOFIX_SECS"
ENV_MOBILE_ABSENT_SECS = "RASTRO_MOBILE_ABSENT_SECS"
ENV_QUIET_START = "RASTRO_QUIET_START"
ENV_QUIET_END = "RASTRO_QUIET_END"
ENV_TZ = "RASTRO_TZ"
ENV_FIXED_NODES = "RASTRO_FIXED_NODES"
ENV_ALERTS_ENABLED = "RASTRO_ALERTS_ENABLED"
ENV_INTERVALO_SECS = "RASTRO_ALERT_INTERVAL_SECS"

GATEWAY_SILENT_PADRAO_SECS = 3600.0
PARKED_PADRAO_METERS = 30.0
PARKED_PADRAO_SECS = 7200.0
NOFIX_PADRAO_SECS = 3600.0
MOBILE_ABSENT_PADRAO_SECS = 7200.0
TZ_PADRAO = "UTC"
INTERVALO_PADRAO_SECS = 60.0

KIND_GATEWAY_MUDO = "gateway_mudo"
KIND_POSICAO_PARADA = "posicao_parada"
KIND_SEM_FIX = "sem_fix"
KIND_MOVEL_AUSENTE = "movel_ausente"

# Kinds suprimidos pela janela de silêncio (apenas o disparo, não a limpeza).
_KINDS_COM_SILENCIO = (
    KIND_GATEWAY_MUDO,
    KIND_MOVEL_AUSENTE,
)

TIPO_FIXO = "fixed"
TIPO_MOVEL = "mobile"

_RAIO_TERRA_M = 6_371_000.0
_ESCALA_1E7 = 10_000_000.0


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
    parked_meters: float
    parked_secs: float
    nofix_secs: float
    mobile_absent_secs: float


def haversine_m(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    """Distância haversine em metros entre dois pontos em graus decimais."""
    phi1, phi2 = math.radians(lat1_deg), math.radians(lat2_deg)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2_deg - lon1_deg)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _RAIO_TERRA_M * math.asin(math.sqrt(a))


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
        gateway_silent_secs=_float_env(env, ENV_GATEWAY_SILENT_SECS, GATEWAY_SILENT_PADRAO_SECS),
        parked_meters=_float_env(env, ENV_PARKED_METERS, PARKED_PADRAO_METERS),
        parked_secs=_float_env(env, ENV_PARKED_SECS, PARKED_PADRAO_SECS),
        nofix_secs=_float_env(env, ENV_NOFIX_SECS, NOFIX_PADRAO_SECS),
        mobile_absent_secs=_float_env(env, ENV_MOBILE_ABSENT_SECS, MOBILE_ABSENT_PADRAO_SECS),
    )


def _minutos_hhmm(valor: str) -> int | None:
    """``HH:MM`` → minutos do dia; None se inválido (não levanta)."""
    partes = valor.strip().split(":")
    if len(partes) != 2:
        return None
    try:
        hora, minuto = int(partes[0]), int(partes[1])
    except ValueError:
        return None
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    return hora * 60 + minuto


def _janela_silencio(now: float, env: dict | None = None) -> bool:
    """True se ``now`` cai na janela de silêncio noturno (RASTRO_TZ).

    Início == fim desliga a janela (senão seria silêncio 24 h). Início > fim =
    janela cruzando a meia-noite. Fuso/config inválidos desligam a janela.
    """
    env = os.environ if env is None else env
    inicio_bruto = (env.get(ENV_QUIET_START) or "").strip()
    fim_bruto = (env.get(ENV_QUIET_END) or "").strip()
    if not inicio_bruto or not fim_bruto:
        return False
    inicio = _minutos_hhmm(inicio_bruto)
    fim = _minutos_hhmm(fim_bruto)
    if inicio is None or fim is None or inicio == fim:
        return False
    tz_nome = (env.get(ENV_TZ) or TZ_PADRAO).strip()
    try:
        from zoneinfo import ZoneInfo

        local = datetime.fromtimestamp(now, tz=ZoneInfo(tz_nome))
    except Exception:  # noqa: BLE001 — fuso inválido: janela desligada, nunca levantar
        log.warning("AVISO: %s inválido (%r) — janela de silêncio desligada", ENV_TZ, tz_nome)
        return False
    minuto = local.hour * 60 + local.minute
    if inicio < fim:
        return inicio <= minuto < fim
    return minuto >= inicio or minuto < fim  # janela cruza a meia-noite


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


def evaluate(
    now: float,
    state_rows: list,
    last_uplink_by_gateway: dict,
    last_fix_by_node: dict,
    node_kinds: dict,
) -> list[AlertEvent]:
    """Avalia as 4 condições e devolve eventos de raise/clear (função pura).

    ``state_rows``: linhas de ``alert_state`` como dicts com
    ``key/node_num/kind/since/cleared_at`` (epochs float ou None).
    ``last_uplink_by_gateway``: {gateway_num: epoch_do_último_uplink}.
    ``last_fix_by_node``: {node_num: dict} com ``first_seen/last_seen/fix_time/
    lat_i/lon_i/prev_time/prev_lat_i/prev_lon_i`` (None = sem dado daquele tipo).
    ``node_kinds``: {node_num: 'fixed' | 'mobile'}; ausente = móvel.
    """
    cfg = _config()
    ativos = {row["key"]: row for row in state_rows if row.get("cleared_at") is None}
    silencio = _janela_silencio(now)
    deteccoes: dict[str, tuple[str, int | None, bool]] = {}

    for gw, ultimo in sorted(last_uplink_by_gateway.items()):
        mudo = (now - ultimo) > cfg.gateway_silent_secs
        deteccoes[_chave(KIND_GATEWAY_MUDO, gw)] = (KIND_GATEWAY_MUDO, gw, mudo)

    for num, info in sorted((last_fix_by_node or {}).items()):
        fixo = (node_kinds or {}).get(num, TIPO_MOVEL) == TIPO_FIXO
        last_seen = info.get("last_seen") if info else None
        fix_time = info.get("fix_time") if info else None
        first_seen = info.get("first_seen") if info else None

        # posicao_parada: só classe fixa, com dois fixes e último fresco.
        parada = False
        if (
            fixo
            and info
            and fix_time is not None
            and info.get("prev_time") is not None
            and (now - fix_time) <= cfg.parked_secs
            and (fix_time - info["prev_time"]) >= cfg.parked_secs
        ):
            deslocamento = info.get("displacement_m")
            if deslocamento is None and info.get("lat_i") is not None:
                deslocamento = haversine_m(
                    info["prev_lat_i"] / _ESCALA_1E7,
                    info["prev_lon_i"] / _ESCALA_1E7,
                    info["lat_i"] / _ESCALA_1E7,
                    info["lon_i"] / _ESCALA_1E7,
                )
            parada = deslocamento is not None and deslocamento < cfg.parked_meters
        deteccoes[_chave(KIND_POSICAO_PARADA, num)] = (KIND_POSICAO_PARADA, num, parada)

        # sem_fix: vivo (enviando) e sem fix válido há demais.
        sem = False
        if vivo := bool(last_seen is not None and (now - last_seen) <= cfg.nofix_secs):
            if fix_time is None:
                sem = first_seen is not None and (now - first_seen) > cfg.nofix_secs
            else:
                sem = (last_seen - fix_time) > cfg.nofix_secs
        deteccoes[_chave(KIND_SEM_FIX, num)] = (KIND_SEM_FIX, num, sem)

        # movel_ausente: só classe móvel, sem atividade há demais.
        ausente = (
            not fixo
            and last_seen is not None
            and (now - last_seen) > cfg.mobile_absent_secs
        )
        deteccoes[_chave(KIND_MOVEL_AUSENTE, num)] = (KIND_MOVEL_AUSENTE, num, ausente)

    eventos: list[AlertEvent] = []
    for key in sorted(deteccoes):
        kind, sujeito, detectado = deteccoes[key]
        ativo = ativos.get(key)
        if detectado and ativo is None:
            if kind in _KINDS_COM_SILENCIO and silencio:
                continue  # quiet hours: só suprime o disparo, estado continua limpo
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
    eventos = evaluate(
        now,
        db.alert_state_rows(),
        db.gateway_uplink_times(),
        db.node_activity(),
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
    return {"raised": len(raised), "cleared": len(cleared)}
