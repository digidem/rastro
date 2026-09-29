#!/usr/bin/env python3
"""Gera um docker compose que SIMULA a instalação do template CapRover do Rastro.

Uso:
  rastro_caprover_sim.py <rastro.yml> <valores.json> <saida.yml> [--app NOME] [--image-tag TAG]

- Substitui as variáveis ``$$cap_*`` do template pelos valores de teste de
  ``valores.json`` (variáveis ausentes usam o ``defaultValue`` do template;
  ``$$cap_gen_random_hex(n)`` vira hex aleatório). Falha se sobrar variável.
- Recusa template em que um id de variável é prefixo de outro (uma substituição
  textual corromperia o maior — o validador da loja não pega isso).
- Cada serviço ganha o alias de rede ``srv-captain--<serviço>`` numa rede
  ``internal: true`` (sem saída para a internet). NENHUMA porta é publicada no host:
  as portas declaradas no template ficam no label ``rastro.sim.declared-ports`` para
  conferência; as sondas rodam como contêineres na mesma rede.
- ``caproverExtra`` é removido; volumes nomeados viram volumes de topo (compartilhados
  entre serviços, como o ``<app>-pki`` que leva a CA pública do broker ao ingest e ao web).
- Variáveis sem ``defaultValue`` (ex.: ``$$cap_pg_admin_url``) DEVEM vir em valores.json.
Só lê e escreve arquivos (roda no sandbox sem rede).
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys

import yaml

BUILTINS = ["$$cap_appname", "$$cap_root_domain"]
ALLOWED_KEYS = {"image", "environment", "ports", "volumes", "depends_on", "hostname",
                "command", "cap_add", "restart", "caproverExtra", "expose"}


def die(msg: str) -> None:
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("template"); ap.add_argument("values"); ap.add_argument("out")
    ap.add_argument("--app", default="sim")
    ap.add_argument("--image-tag", default=None, help="sobrescreve $$cap_tag")
    a = ap.parse_args()

    raw = open(a.template, encoding="utf-8").read()
    tpl = yaml.safe_load(raw)
    if str(tpl.get("captainVersion")) != "4":
        die("captainVersion deve ser 4")
    variables = tpl["caproverOneClickApp"].get("variables", [])
    ids = [v["id"] for v in variables] + BUILTINS
    if len(set(ids)) != len(ids):
        die("ids de variável duplicados")
    for x in ids:
        for y in ids:
            if x != y and y.startswith(x):
                die(f"id de variável {x} é prefixo de {y} (substituição textual ambígua)")

    given = json.load(open(a.values, encoding="utf-8"))
    values = {"$$cap_appname": a.app, "$$cap_root_domain": "sim.local"}
    for v in variables:
        vid = v["id"]
        val = given.get(vid, v.get("defaultValue"))
        if vid == "$$cap_tag" and a.image_tag:
            val = a.image_tag
        if val is None:
            die(f"sem valor para {vid} (nem no valores.json nem defaultValue)")
        val = str(val)
        m = re.fullmatch(r"\$\$cap_gen_random_hex\((\d+)\)", val)
        if m:
            val = secrets.token_hex((int(m.group(1)) + 1) // 2)[: int(m.group(1))]
        # NÃO expandir built-ins aqui: o CapRover real (verificado numa VM) não expande
        # $$cap_appname dentro do valor de uma variável — só no corpo do YAML (ver abaixo)
        rx = v.get("validRegex")
        if rx:
            pat = rx.strip("/")
            if not re.fullmatch(pat, val):
                die(f"valor de teste de {vid} não passa em validRegex {rx}")
        values[vid] = val

    services_raw = yaml.safe_dump(tpl["services"], sort_keys=False, allow_unicode=True)
    # Ordem do CapRover real (verificada numa VM): $$cap_appname primeiro, depois as
    # variáveis do formulário, por último $$cap_root_domain. Logo um valor de variável
    # contendo $$cap_appname sobra sem expandir (e a checagem abaixo recusa).
    services_raw = services_raw.replace("$$cap_appname", values["$$cap_appname"])
    for vid in sorted((k for k in values if k not in BUILTINS), key=len, reverse=True):
        services_raw = services_raw.replace(vid, values[vid])
    services_raw = services_raw.replace("$$cap_root_domain", values["$$cap_root_domain"])
    if "$$cap_" in services_raw:
        die("sobrou variável $$cap_ não substituída: "
            + ", ".join(sorted(set(re.findall(r"\$\$cap_\w+", services_raw)))))
    services = yaml.safe_load(services_raw)

    out_services, volumes = {}, {}
    for name, svc in services.items():
        extra = set(svc) - ALLOWED_KEYS
        if extra:
            die(f"serviço {name}: chaves que o CapRover ignora/não suporta: {sorted(extra)}")
        svc = dict(svc)
        svc.pop("caproverExtra", None)
        declared = svc.pop("ports", [])
        svc.pop("expose", None)
        labels = {"rastro.sim.declared-ports": ",".join(map(str, declared))}
        for vol in svc.get("volumes", []) or []:
            src = str(vol).split(":", 1)[0]
            if not src.startswith(("/", ".")):
                volumes[src] = {}
        env = svc.get("environment") or {}
        svc["environment"] = {k: ("" if v is None else str(v)) for k, v in env.items()}
        svc["labels"] = labels
        svc["networks"] = {"sim": {"aliases": [f"srv-captain--{name}"]}}
        out_services[name] = svc

    compose = {
        "name": f"rastro-sim-{a.app}",
        "services": out_services,
        "networks": {"sim": {"internal": True}},
    }
    if volumes:
        compose["volumes"] = volumes
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("# GERADO por scripts/rastro_caprover_sim.py — não editar à mão\n")
        yaml.safe_dump(compose, f, sort_keys=False, allow_unicode=True)
    print(f"OK: {a.out} ({len(out_services)} serviços, rede interna, sem portas no host)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
