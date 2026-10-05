#!/usr/bin/env python3
"""Geração de passwd e aclfile do Mosquitto a partir de arquivo de contas JSON (WP-C).

Lê a definição de contas e permissões (RASTRO_ACCOUNTS_FILE), valida a integridade
dos dados e produz os arquivos de senhas em texto puro (para hash posterior via
mosquitto_passwd -U) e o arquivo de ACL com regras estritas por nó e por barco.
Sem dependências externas (apenas biblioteca padrão).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

# Padrões de validação
RE_VALID_USER = re.compile(r"^[A-Za-z0-9_!.-]+$")
RE_VALID_GATEWAY = re.compile(r"^[A-Za-z0-9_!.-]+$")
RE_VALID_ROOT = re.compile(r"^[A-Za-z0-9_./-]+$")


def validate_topic_root(root: str) -> str:
    """Valida e normaliza o prefixo raiz de tópicos nativos."""
    if not isinstance(root, str):
        raise ValueError("O campo 'root' deve ser uma string")
    clean_root = root.strip("/")
    if not clean_root:
        raise ValueError("O campo 'root' não pode ser vazio")
    if any(c in clean_root for c in ("+", "#", " ", "\n", "\r", "\t")):
        raise ValueError(f"O campo 'root' contém caracteres inválidos: '{root}'")
    if not RE_VALID_ROOT.match(clean_root):
        raise ValueError(f"O campo 'root' contém caracteres inválidos: '{root}'")
    return clean_root


def validate_password(user: str, password: str, min_len: int = 24) -> str:
    """Valida senha do usuário sem expor conteúdo em mensagens."""
    if not isinstance(password, str):
        raise ValueError(f"A senha do usuário '{user}' deve ser uma string")
    if len(password) < min_len:
        raise ValueError(
            f"A senha do usuário '{user}' é muito curta (mínimo {min_len} caracteres)"
        )
    if ":" in password:
        raise ValueError(
            f"A senha do usuário '{user}' não pode conter ':' (delimitador do arquivo passwd)"
        )
    if "\n" in password or "\r" in password:
        raise ValueError(
            f"A senha do usuário '{user}' não pode conter quebras de linha"
        )
    return password


def validate_username(user: str, role_desc: str = "usuário") -> str:
    """Valida identificador de usuário."""
    if not isinstance(user, str) or not user.strip():
        raise ValueError(f"Identificador de {role_desc} não pode ser vazio")
    user = user.strip()
    if ":" in user:
        raise ValueError(f"Identificador de {role_desc} não pode conter ':'")
    if any(c in user for c in (" ", "\n", "\r", "\t", "+", "#", "/")):
        raise ValueError(
            f"Identificador de {role_desc} '{user}' contém caracteres inválidos"
        )
    if not RE_VALID_USER.match(user):
        raise ValueError(
            f"Identificador de {role_desc} '{user}' contém caracteres inválidos"
        )
    return user


def validate_gateway_id(gateway_id: str) -> str:
    """Valida identificador de gateway virtual."""
    if not isinstance(gateway_id, str) or not gateway_id.strip():
        raise ValueError("Identificador de gateway virtual não pode ser vazio")
    gw = gateway_id.strip()
    if any(c in gw for c in (" ", "\n", "\r", "\t", "+", "#", "/", ":")):
        raise ValueError(
            f"Identificador de gateway virtual '{gw}' contém caracteres inválidos"
        )
    if not RE_VALID_GATEWAY.match(gw):
        raise ValueError(
            f"Identificador de gateway virtual '{gw}' contém caracteres inválidos"
        )
    return gw


def load_accounts(file_path: str | Path) -> dict:
    """Carrega e valida o arquivo JSON de contas."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de contas não encontrado: '{path}'")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido no arquivo de contas '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("O conteúdo raiz do arquivo de contas deve ser um objeto JSON")

    root = validate_topic_root(data.get("root", "univaja/mesh"))

    # ingest
    if "ingest" not in data or not isinstance(data["ingest"], dict):
        raise ValueError("Configuração obrigatória da conta 'ingest' ausente no JSON")
    ingest_pw = validate_password("ingest", data["ingest"].get("password", ""))

    # outbox
    if "outbox" not in data or not isinstance(data["outbox"], dict):
        raise ValueError("Configuração obrigatória da conta 'outbox' ausente no JSON")
    outbox_pw = validate_password("outbox", data["outbox"].get("password", ""))

    # monitor (opcional, rig de teste): somente leitura em <root>/#
    monitor_pw = None
    if data.get("monitor") is not None:
        if not isinstance(data["monitor"], dict):
            raise ValueError("O campo 'monitor' deve ser um objeto")
        monitor_pw = validate_password("monitor", data["monitor"].get("password", ""))

    # legacy_prefix (opcional)
    legacy_prefix = None
    if "legacy_prefix" in data and data["legacy_prefix"] is not None:
        if not isinstance(data["legacy_prefix"], str):
            raise ValueError("O campo 'legacy_prefix' deve ser uma string")
        clean_legacy = data["legacy_prefix"].strip().strip("/")
        if clean_legacy:
            legacy_prefix = validate_topic_root(clean_legacy)

    # gateway (legado opcional)
    gateway_pw = None
    gateway_prefix = "rastro"
    if "gateway" in data and data["gateway"] is not None:
        if not isinstance(data["gateway"], dict):
            raise ValueError("O campo 'gateway' deve ser um objeto")
        gateway_pw = validate_password("gateway", data["gateway"].get("password", ""))
        gateway_prefix = data["gateway"].get("prefix", "rastro").strip("/")
        if not gateway_prefix:
            gateway_prefix = "rastro"
        gateway_prefix = validate_topic_root(gateway_prefix)

    # virtual_gateways
    if "virtual_gateways" not in data or not isinstance(data["virtual_gateways"], list):
        raise ValueError("Lista obrigatória 'virtual_gateways' ausente no JSON")

    virtual_gateways: list[dict[str, str]] = []
    boat_to_vgws: dict[str, list[str]] = {}
    ids_gateway_vistos: set[str] = set()
    for i, vgw in enumerate(data["virtual_gateways"]):
        if not isinstance(vgw, dict):
            raise ValueError(f"Item {i} de 'virtual_gateways' deve ser um objeto")
        boat = vgw.get("boat")
        if not isinstance(boat, str) or not boat.strip():
            raise ValueError(f"Campo 'boat' inválido ou ausente no gateway virtual {i}")
        boat = boat.strip()
        gw_id = validate_gateway_id(vgw.get("gateway_id", ""))
        if gw_id in ids_gateway_vistos:
            raise ValueError(
                f"Identificador de gateway virtual '{gw_id}' duplicado — "
                "cada gateway virtual deve pertencer a um único barco"
            )
        ids_gateway_vistos.add(gw_id)
        virtual_gateways.append({"boat": boat, "gateway_id": gw_id})
        boat_to_vgws.setdefault(boat, []).append(gw_id)

    # nodes
    if "nodes" not in data or not isinstance(data["nodes"], list):
        raise ValueError("Lista obrigatória 'nodes' ausente no JSON")

    nodes: list[dict[str, str]] = []
    # Usuários de serviço reservados (ingest, outbox, gateway, monitor): NUNCA
    # podem ser usados como usuário de nó, mesmo sem a conta correspondente no
    # JSON — o nome colidiria com o passwd/ACL do serviço.
    seen_users = {"ingest", "outbox", "gateway", "monitor"}

    for i, node in enumerate(data["nodes"]):
        if not isinstance(node, dict):
            raise ValueError(f"Item {i} de 'nodes' deve ser um objeto")
        user = validate_username(node.get("user", ""), f"nó {i}")
        if user in seen_users:
            raise ValueError(f"Usuário duplicado ou reservado nas contas: '{user}'")
        seen_users.add(user)

        pw = validate_password(user, node.get("password", ""))
        boat = node.get("boat")
        if not isinstance(boat, str) or not boat.strip():
            raise ValueError(f"Campo 'boat' inválido ou ausente para o nó '{user}'")
        boat = boat.strip()
        if boat not in boat_to_vgws:
            raise ValueError(
                f"Barco '{boat}' do nó '{user}' não possui gateway virtual configurado"
            )

        nodes.append({"user": user, "password": pw, "boat": boat})

    validated: dict = {
        "root": root,
        "ingest": {"password": ingest_pw},
        "outbox": {"password": outbox_pw},
        "virtual_gateways": virtual_gateways,
        "nodes": nodes,
    }
    if legacy_prefix is not None:
        validated["legacy_prefix"] = legacy_prefix
    if gateway_pw is not None:
        validated["gateway"] = {"password": gateway_pw, "prefix": gateway_prefix}
    if monitor_pw is not None:
        validated["monitor"] = {"password": monitor_pw}

    return validated


def generate_passwd(accounts: dict) -> str:
    """Gera o conteúdo em texto plano do arquivo passwd."""
    lines: list[str] = [
        f"ingest:{accounts['ingest']['password']}",
        f"outbox:{accounts['outbox']['password']}",
    ]
    if "gateway" in accounts:
        lines.append(f"gateway:{accounts['gateway']['password']}")
    if "monitor" in accounts:
        lines.append(f"monitor:{accounts['monitor']['password']}")

    for node in accounts.get("nodes", []):
        lines.append(f"{node['user']}:{node['password']}")

    return "\n".join(lines) + "\n"


def generate_acl(accounts: dict) -> str:
    """Gera o conteúdo do arquivo de ACL do Mosquitto.
    
    Regras estritas:
    - ingest: somente leitura em <root>/2/e/#
    - outbox: somente escrita em <root>/2/e/EVU/<vgw> para cada vgw
    - nó !id: escrita em <root>/2/e/+/!id e leitura em <root>/2/e/EVU/<vgw do seu barco>
    - nenhum %u; negar todo o restante por omissão.
    """
    root = accounts["root"]
    lines: list[str] = [
        "# ACL do mosquitto gerada por accounts.py",
        "# Regras nominais por usuario. Acesso padrao: negar tudo o que nao estiver listado.",
        "",
        "user ingest",
        f"topic read {root}/2/e/#",
    ]

    legacy_pfx = None
    if "gateway" in accounts:
        legacy_pfx = accounts["gateway"].get("prefix", "rastro")
    elif "legacy_prefix" in accounts:
        legacy_pfx = accounts.get("legacy_prefix", "rastro")

    if legacy_pfx:
        lines.extend([
            f"topic read {legacy_pfx}/positions/#",
            f"topic read {legacy_pfx}/telemetry/#",
            f"topic read {legacy_pfx}/status/#",
        ])

    lines.append("")
    lines.append("user outbox")
    # Gateways virtuais únicos
    vgw_ids = sorted({vgw["gateway_id"] for vgw in accounts.get("virtual_gateways", [])})
    for vgw_id in vgw_ids:
        lines.append(f"topic write {root}/2/e/EVU/{vgw_id}")

    if "gateway" in accounts:
        prefix = accounts["gateway"].get("prefix", "rastro")
        lines.extend([
            "",
            "user gateway",
            f"topic write {prefix}/positions/#",
            f"topic write {prefix}/telemetry/#",
            f"topic write {prefix}/status/#",
        ])

    # Mapeamento boat -> lista de gateway_ids
    boat_to_vgws: dict[str, list[str]] = {}
    for vgw in accounts.get("virtual_gateways", []):
        boat_to_vgws.setdefault(vgw["boat"], []).append(vgw["gateway_id"])

    # Permissões por nó
    for node in accounts["nodes"]:
        user = node["user"]
        boat = node["boat"]
        lines.append("")
        lines.append(f"user {user}")
        lines.append(f"topic write {root}/2/e/+/{user}")
        for vgw_id in sorted(set(boat_to_vgws.get(boat, []))):
            lines.append(f"topic read {root}/2/e/EVU/{vgw_id}")

    if "monitor" in accounts:
        lines.extend(
            [
                "",
                "user monitor",
                f"topic read {root}/#",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def generate_broker_files(
    accounts_file: str | Path, passwd_file: str | Path, acl_file: str | Path
) -> None:
    """Lê as contas JSON e escreve com segurança os arquivos passwd e aclfile."""
    accounts = load_accounts(accounts_file)
    passwd_content = generate_passwd(accounts)
    acl_content = generate_acl(accounts)

    passwd_path = Path(passwd_file)
    acl_path = Path(acl_file)

    # Criação segura com umask restritivo (077)
    old_umask = os.umask(0o077)
    try:
        passwd_path.parent.mkdir(parents=True, exist_ok=True)
        acl_path.parent.mkdir(parents=True, exist_ok=True)

        with open(passwd_path, "w", encoding="utf-8") as f:
            f.write(passwd_content)
        passwd_path.chmod(0o600)

        with open(acl_path, "w", encoding="utf-8") as f:
            f.write(acl_content)
        acl_path.chmod(0o600)
    finally:
        os.umask(old_umask)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para geração dos arquivos do broker."""
    parser = argparse.ArgumentParser(
        description="Gera arquivos passwd e aclfile para o broker Mosquitto (WP-C)."
    )
    parser.add_argument(
        "accounts_file",
        nargs="?",
        default=os.environ.get("RASTRO_ACCOUNTS_FILE"),
        help="Caminho do arquivo JSON de contas (ou via RASTRO_ACCOUNTS_FILE)",
    )
    parser.add_argument(
        "passwd_file",
        nargs="?",
        default=os.environ.get("RASTRO_PASSWD_FILE"),
        help="Caminho de saída para o arquivo passwd em texto plano",
    )
    parser.add_argument(
        "acl_file",
        nargs="?",
        default=os.environ.get("RASTRO_ACL_FILE"),
        help="Caminho de saída para o arquivo aclfile",
    )

    args = parser.parse_args(argv)

    if not args.accounts_file:
        sys.stderr.write("ERRO: caminho do arquivo de contas não informado\n")
        return 1
    if not args.passwd_file:
        sys.stderr.write("ERRO: caminho do arquivo passwd não informado\n")
        return 1
    if not args.acl_file:
        sys.stderr.write("ERRO: caminho do arquivo aclfile não informado\n")
        return 1

    try:
        generate_broker_files(args.accounts_file, args.passwd_file, args.acl_file)
        return 0
    except Exception as exc:
        sys.stderr.write(f"ERRO: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
