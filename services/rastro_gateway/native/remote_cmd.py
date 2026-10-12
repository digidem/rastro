"""Modelo e política do comando remoto por nó (reiniciar/desligar). Tarefa tsk #36 / M2.

Só valida e decide; NÃO envia nada ao rádio (o caminho de comando pela malha
ainda não foi provado). Desligado por padrão: ``RASTRO_REMOTE_CMD_ENABLED=1`` liga.
Regras aprovadas (H6, 2026-10-09): nós solares/fixos só reiniciam; desligar só
com tripulação por perto; factory reset remoto nunca.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum

ENV_ENABLED = "RASTRO_REMOTE_CMD_ENABLED"
_NODE_ID_RE = re.compile(r"![0-9a-f]{8}")


class Action(str, Enum):
    REBOOT = "reboot"
    SHUTDOWN = "shutdown"


class NodeKind(str, Enum):
    BOAT = "boat"
    SOLAR = "solar"
    FIXED = "fixed"
    UNKNOWN = "unknown"


class CommandError(ValueError):
    """Comando inválido ou negado; ``code`` é estável para a API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RemoteCommand:
    node_id: str
    action: Action
    kind: NodeKind
    crew_nearby: bool = False


def is_enabled(env: dict[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return e.get(ENV_ENABLED, "").strip() == "1"


def parse_command(
    node_id: object, action: object, kind: object, crew_nearby: object = False,
) -> RemoteCommand:
    """Valida entradas cruas. Qualquer ação fora de reboot/shutdown é recusada
    (inclui factory reset e variantes)."""
    if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
        raise CommandError("bad_node", "id de nó inválido (esperado !xxxxxxxx)")
    if not isinstance(action, str):
        raise CommandError("bad_action", "ação inválida")
    try:
        act = Action(action.strip().lower())
    except ValueError:
        raise CommandError("bad_action", "ação não permitida (só reboot/shutdown)") from None
    try:
        nk = NodeKind(kind) if isinstance(kind, str) else NodeKind.UNKNOWN
    except ValueError:
        nk = NodeKind.UNKNOWN
    if not isinstance(crew_nearby, bool):
        raise CommandError("bad_crew", "crew_nearby deve ser booleano")
    return RemoteCommand(node_id, act, nk, crew_nearby)


def authorize(cmd: RemoteCommand, env: dict[str, str] | None = None) -> None:
    """Levanta CommandError se o comando não pode seguir."""
    if not is_enabled(env):
        raise CommandError("disabled", "comando remoto desligado")
    if cmd.action is Action.REBOOT:
        return
    # shutdown
    if cmd.kind is not NodeKind.BOAT:
        raise CommandError("shutdown_denied_kind", "desligar só em nó de barco (solar/fixo/desconhecido só reinicia)")
    if not cmd.crew_nearby:
        raise CommandError("shutdown_denied_crew", "desligar exige tripulação por perto")
