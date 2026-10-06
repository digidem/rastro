#!/usr/bin/env python3
"""Derivação determinística de nós, senhas e gateways virtuais para o broker (H2).

Implementa algoritmos determinísticos compartilhados com o gateway (apenas stdlib):
- Gateway virtual: a partir da chave do barco (ex.: 'b1'), gera o virtual_node_num
  e virtual gateway id ('!%08x').
- Senha do nó: derivação via HMAC-SHA256(secret, b"node:" + user) em base64 urlsafe.
- Construção de contas a partir do ambiente (RASTRO_NATIVE_NODES, RASTRO_NATIVE_SECRET).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

# Usuário de nó Meshtastic: '!' + 8 hex minúsculos (ex.: '!a0000001').
RE_VALID_USER = re.compile(r"^![0-9a-f]{8}$")
RE_VALID_BOAT = re.compile(r"^[A-Za-z0-9_.-]+$")


def derive_virtual_node(boat_key: str) -> tuple[int, str]:
    """Deriva deterministamente virtual_node_num e virtual_gateway_id a partir da chave do barco.

    Algoritmo:
      n = (int.from_bytes(sha256(b"rastro-vgw:" + b.encode()).digest()[:4], "big") | 0xE0000000) & 0xFFFFFFFE
      virtual_node_num = n
      virtual_gateway_id = "!%08x" % n
    """
    if not isinstance(boat_key, str) or not boat_key.strip():
        raise ValueError("A chave do barco não pode ser vazia")
    b = boat_key.strip()
    digest4 = hashlib.sha256(b"rastro-vgw:" + b.encode("utf-8")).digest()[:4]
    n = (int.from_bytes(digest4, "big") | 0xE0000000) & 0xFFFFFFFE
    vgw_id = "!%08x" % n
    return n, vgw_id


def derive_node_password(secret: str, user_id: str) -> str:
    """Deriva a senha do nó a partir do segredo mestre e do ID do usuário.

    Algoritmo:
      base64.urlsafe_b64encode(hmac.new(secret.encode(), b"node:" + u.encode(), sha256).digest()).decode().rstrip("=")[:30]
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("O segredo não pode ser vazio")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Identificador de nó não pode ser vazio")
    u = user_id.strip()
    raw = hmac.new(secret.encode("utf-8"), b"node:" + u.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")[:30]


# Aliases compartilhados com services/rastro_gateway/native/derive.py
vgw_for_boat = derive_virtual_node
node_password = derive_node_password


def parse_native_nodes(nodes_str: str | None) -> list[tuple[str, str]]:
    """Analisa a string de nós nativos no formato '!a0000001=b1,!a0000002=b2'.

    Retorna lista de pares (user_id, boat_key).
    """
    if nodes_str is None or not isinstance(nodes_str, str):
        return []
    clean = nodes_str.strip()
    if not clean:
        return []

    pairs: list[tuple[str, str]] = []
    seen_users: set[str] = set()
    for token in clean.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split("=")
        if len(parts) != 2:
            raise ValueError(
                f"Entrada de nó inválida: '{token}' (esperado formato '!id=barco')"
            )
        user, boat = parts[0].strip(), parts[1].strip()
        if not user:
            raise ValueError(f"Identificador de usuário vazio na entrada: '{token}'")
        if not boat:
            raise ValueError(f"Identificador de barco vazio na entrada: '{token}'")
        if not RE_VALID_USER.match(user):
            raise ValueError(f"Identificador de nó inválido: '{user}'")
        if not RE_VALID_BOAT.match(boat):
            raise ValueError(f"Identificador de barco inválido: '{boat}'")
        if user in seen_users:
            raise ValueError(f"Nó duplicado na configuração nativa: '{user}'")
        seen_users.add(user)
        pairs.append((user, boat))

    return pairs


parse_nodes = parse_native_nodes


def extract_distinct_boats(pairs: list[tuple[str, str]]) -> list[str]:
    """Extrai lista de barcos distintos preservando a ordem de aparição."""
    boats: list[str] = []
    seen: set[str] = set()
    for _, boat in pairs:
        if boat not in seen:
            seen.add(boat)
            boats.append(boat)
    return boats


def build_accounts_from_env(env: dict[str, str] | None = None) -> dict:
    """Gera a estrutura de dicionário de contas a partir das variáveis de ambiente."""
    source = env if env is not None else os.environ

    nodes_val = source.get("RASTRO_NATIVE_NODES")
    if not nodes_val or not nodes_val.strip():
        raise ValueError("RASTRO_NATIVE_NODES não definida")

    secret = source.get("RASTRO_NATIVE_SECRET")
    if not secret:
        raise ValueError(
            "RASTRO_NATIVE_SECRET não definida — obrigatória quando RASTRO_NATIVE_NODES estiver configurada"
        )
    if len(secret) < 24:
        raise ValueError("RASTRO_NATIVE_SECRET muito curta: mínimo 24 caracteres")

    pw_ingest = source.get("RASTRO_MQTT_PASSWORD_INGEST")
    if not pw_ingest:
        raise ValueError("RASTRO_MQTT_PASSWORD_INGEST não definida — obrigatória")
    if len(pw_ingest) < 24:
        raise ValueError("RASTRO_MQTT_PASSWORD_INGEST muito curta: mínimo 24 caracteres")

    pw_outbox = source.get("RASTRO_MQTT_PASSWORD_OUTBOX")
    if not pw_outbox:
        raise ValueError("RASTRO_MQTT_PASSWORD_OUTBOX não definida — obrigatória no modo nativo")
    if len(pw_outbox) < 24:
        raise ValueError("RASTRO_MQTT_PASSWORD_OUTBOX muito curta: mínimo 24 caracteres")

    root = (source.get("RASTRO_NATIVE_ROOT") or "univaja/mesh").strip().strip("/")
    if not root:
        root = "univaja/mesh"

    prefix = (source.get("RASTRO_MQTT_TOPIC_PREFIX") or "rastro").strip().strip("/")
    if not prefix:
        prefix = "rastro"

    pw_gateway = source.get("RASTRO_MQTT_PASSWORD_GATEWAY")
    if pw_gateway is not None and len(pw_gateway.strip()) > 0:
        if len(pw_gateway) < 24:
            raise ValueError("RASTRO_MQTT_PASSWORD_GATEWAY muito curta: mínimo 24 caracteres")
    else:
        pw_gateway = None

    node_pairs = parse_native_nodes(nodes_val)
    if not node_pairs:
        raise ValueError("Nenhum nó válido encontrado na configuração de RASTRO_NATIVE_NODES")
    boats = extract_distinct_boats(node_pairs)

    virtual_gateways: list[dict[str, str]] = []
    for boat in boats:
        _, vgw_id = derive_virtual_node(boat)
        virtual_gateways.append({"boat": boat, "gateway_id": vgw_id})

    nodes: list[dict[str, str]] = []
    for user, boat in node_pairs:
        pw = derive_node_password(secret, user)
        nodes.append({"user": user, "password": pw, "boat": boat})

    accounts: dict = {
        "root": root,
        "ingest": {"password": pw_ingest},
        "outbox": {"password": pw_outbox},
        "legacy_prefix": prefix,
        "virtual_gateways": virtual_gateways,
        "nodes": nodes,
    }

    if pw_gateway:
        accounts["gateway"] = {
            "password": pw_gateway,
            "prefix": prefix,
        }

    return accounts


def main(argv: list[str] | None = None) -> int:
    """CLI para gerar contas a partir de variáveis de ambiente e gravar em arquivo seguro."""
    parser = argparse.ArgumentParser(
        description="Gera JSON de contas a partir das variáveis de ambiente RASTRO_NATIVE_*."
    )
    parser.add_argument(
        "out_file",
        nargs="?",
        help="Caminho do arquivo de saída JSON (criado com modo 0600)",
    )
    args = parser.parse_args(argv)

    if not args.out_file:
        sys.stderr.write("ERRO: caminho do arquivo de saída não informado\n")
        return 1

    try:
        accounts = build_accounts_from_env()
    except Exception as exc:
        sys.stderr.write(f"ERRO: {exc}\n")
        return 1

    out_path = Path(args.out_file)
    old_umask = os.umask(0o077)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=2)
        out_path.chmod(0o600)
    finally:
        os.umask(old_umask)

    return 0


if __name__ == "__main__":
    sys.exit(main())
