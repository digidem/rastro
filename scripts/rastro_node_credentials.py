#!/usr/bin/env python3
"""Utilitário CLI para derivar credenciais de nós Meshtastic para o broker MQTT (H2).

Permite visualizar as senhas derivadas a partir do segredo mestre para
configuração manual de nós ou verificação.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Permite importar broker/derive.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROKER_DIR = _REPO_ROOT / "broker"
if str(_BROKER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROKER_DIR))

try:
    from derive import derive_node_password, parse_native_nodes
except ImportError as exc:
    sys.stderr.write(f"ERRO: não foi possível importar módulo de derivação: {exc}\n")
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deriva credenciais de nós Meshtastic para autenticação no broker MQTT."
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("RASTRO_NATIVE_SECRET"),
        help="Segredo mestre de derivação (ou via RASTRO_NATIVE_SECRET, mínimo 24 caracteres)",
    )
    parser.add_argument(
        "--node",
        help="Identificador de nó individual (ex.: '!a0000001')",
    )
    parser.add_argument(
        "--nodes",
        help="Lista de nós no formato RASTRO_NATIVE_NODES (ex.: '!a0000001=b1,!a0000002=b2')",
    )

    args = parser.parse_args(argv)

    if not args.node and not args.nodes:
        sys.stderr.write("ERRO: informe ao menos um nó com --node '!id' ou lista com --nodes\n")
        return 1

    secret = args.secret
    if not secret:
        sys.stderr.write(
            "ERRO: segredo mestre não informado (use --secret ou defina RASTRO_NATIVE_SECRET)\n"
        )
        return 1

    if len(secret) < 24:
        sys.stderr.write("ERRO: o segredo deve ter no mínimo 24 caracteres\n")
        return 1

    # Aviso no stderr de que a saída contém dados sensíveis
    sys.stderr.write("AVISO: a saída contém credenciais secretas (senhas derivadas)\n")

    try:
        if args.node:
            user = args.node.strip()
            if not user:
                sys.stderr.write("ERRO: identificador de nó vazio\n")
                return 1
            pw = derive_node_password(secret, user)
            print(f"{user} {pw}")

        if args.nodes:
            pairs = parse_native_nodes(args.nodes)
            for user, _ in pairs:
                pw = derive_node_password(secret, user)
                print(f"{user} {pw}")
    except Exception as exc:
        sys.stderr.write(f"ERRO: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
