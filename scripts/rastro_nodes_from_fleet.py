#!/usr/bin/env python3
"""Gera RASTRO_NATIVE_NODES (`!id=barco,...`) a partir do inventário da frota (univaja-lora).

Fonte: devices/fleet.json (campo `devices[]`). Entra quem tem deployment.kind == "boat" E long_name
no formato `univaja-<rio>-barco-<n>` (o que exclui nós de teste/bancada e rádios ainda sem nome da frota).
A chave do barco é `<rio>-<n>` (minúsculas, [a-z0-9-]).

Uso:
  scripts/rastro_nodes_from_fleet.py [--fleet ../univaja-lora/devices/fleet.json]
  scripts/rastro_nodes_from_fleet.py --apply        # grava nos apps rastro-broker e rastro-chat (CapRover)

Sem --apply só imprime (ids de nó são identificadores, não segredos, mas não os coloque no repositório).
Rode de novo sempre que um barco novo for provisionado: a conta MQTT do nó precisa existir ANTES de ele
conectar, então o inventário é a fonte; reinicie broker/chat (o --apply já aplica).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PADRAO = RAIZ.parent / "univaja-lora" / "devices" / "fleet.json"
NOME = re.compile(r"^univaja-([a-z0-9-]+)-barco-(\d+)$")
ID = re.compile(r"^![0-9a-f]{8}$")


def nos_da_frota(caminho: Path) -> list[tuple[str, str]]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    saida: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for dev in dados.get("devices", []):
        ident = dev.get("identity") or {}
        m = NOME.match(ident.get("long_name") or "")
        uid = ident.get("user_id") or ""
        if (dev.get("deployment") or {}).get("kind") != "boat" or not m or not ID.match(uid):
            continue
        barco = f"{m.group(1)}-{m.group(2)}"
        if uid in vistos:
            continue
        vistos.add(uid)
        saida.append((uid, barco))
    return sorted(saida, key=lambda p: p[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fleet", default=str(PADRAO))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    nos = nos_da_frota(Path(args.fleet))
    if not nos:
        print("nenhum barco elegível no inventário", file=sys.stderr)
        return 1
    valor = ",".join(f"{uid}={barco}" for uid, barco in nos)
    for uid, barco in nos:
        print(f"{barco:22} {uid}", file=sys.stderr)
    print(valor)
    if args.apply:
        for app in ("rastro-broker", "rastro-chat"):
            subprocess.run(
                [sys.executable, str(RAIZ / "scripts" / "rastro_caprover.py"), "set-env", app,
                 f"RASTRO_NATIVE_NODES={valor}", "--execute"],
                check=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
