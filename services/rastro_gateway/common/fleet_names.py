"""Nomes amigáveis da frota lidos de um arquivo JSON **opcional**.

O caminho vem do argumento ``path`` ou, na falta dele, do env
``RASTRO_FLEET_NAMES_FILE``. Nenhum dos dois é obrigatório: sem arquivo
configurado não há nomes e as funções voltam ``{}`` sem avisar nada — esse é
um estado normal (frota sem registry). Arquivo CONFIGURADO que está ausente ou
malformado é um problema e continua avisado no log.

Shape do arquivo (o mesmo do registry da frota):

    {"schema_version": 1,
     "devices": [{"id": "heltec-v4-fee0",
                  "identity": {"node_num": 202374880,      # int
                               "long_name": "Meshtastic fee0",
                               "short_name": "fee0", ...}}, ...]}

Sem PSK, sem coordenadas aqui — só nomes e ids de nó.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _fleet_path(path: str | None) -> Path | None:
    """Caminho configurado: argumento → env ``RASTRO_FLEET_NAMES_FILE``.

    ``None`` quando nada foi configurado — que é o padrão (arquivo opcional).
    """
    if path:
        return Path(path)
    env = os.environ.get("RASTRO_FLEET_NAMES_FILE")
    return Path(env) if env else None


def _load_fleet(path: str | None) -> dict[int, dict]:
    """Registry → {node_num: device_dict}.

    Sem arquivo configurado → ``{}`` SEM avisar (frota sem nomes é estado
    normal). Arquivo configurado ausente/malformado → ``{}`` com AVISO.
    """
    p = _fleet_path(path)
    if p is None:
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("AVISO: fleet.json ausente: %s", p)
        return {}
    except (OSError, ValueError) as exc:
        log.warning("AVISO: fleet.json inválido (%s): %s", p, exc)
        return {}
    devices = raw.get("devices") if isinstance(raw, dict) else None
    if not isinstance(devices, list):
        log.warning("AVISO: fleet.json inválido (sem lista 'devices'): %s", p)
        return {}
    out: dict[int, dict] = {}
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        identity = dev.get("identity")
        if not isinstance(identity, dict):
            continue
        num = identity.get("node_num")
        if isinstance(num, bool):
            continue
        if isinstance(num, str) and num.isdigit():
            num = int(num)
        if not isinstance(num, int):
            log.warning(
                "AVISO: fleet.json: dispositivo sem node_num válido: %s", dev.get("id")
            )
            continue
        out[num] = dev
    return out


def load_fleet_names(path: str | None = None) -> dict[int, tuple[str, str]]:
    """{node_num: (long_name, short_name)}; nomes ausentes viram ''. """
    names: dict[int, tuple[str, str]] = {}
    for num, dev in _load_fleet(path).items():
        identity = dev.get("identity") or {}
        long_name = identity.get("long_name")
        short_name = identity.get("short_name")
        names[num] = (
            long_name if isinstance(long_name, str) else "",
            short_name if isinstance(short_name, str) else "",
        )
    return names


def load_fleet_ids(path: str | None = None) -> dict[int, str]:
    """{node_num: id do dispositivo no arquivo de nomes} — preenche ``nodes.fleet_id``."""
    ids: dict[int, str] = {}
    for num, dev in _load_fleet(path).items():
        dev_id = dev.get("id")
        if isinstance(dev_id, str) and dev_id:
            ids[num] = dev_id
    return ids


def friendly(node_num: int, node_id: str | None, names: dict[int, tuple[str, str]]) -> str:
    """Nome de exibição: long_name da frota, senão ``!hexid`` curto."""
    entry = names.get(node_num)
    if entry and entry[0]:
        return entry[0]
    if node_id:
        return node_id
    return f"!{node_num:08x}"
