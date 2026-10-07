#!/usr/bin/env python3
"""Operação dos apps Rastro no CapRover via API (login com CAP_URL/CAP_PASS do .env).

Dry-run por padrão: sem --execute só lê e mostra o que faria. NUNCA imprime senhas/valores de
variáveis de ambiente (só nomes). Não apaga apps nem volumes.

Uso:
  scripts/rastro_caprover.py status
  scripts/rastro_caprover.py deploy-image rastro-ingest communityfirst/rastro-ingest:0.8.0 [--execute]
  scripts/rastro_caprover.py set-env rastro-broker KEY=VALOR [KEY=VALOR ...] [--execute]
  scripts/rastro_caprover.py gen-secret            # imprime 32 hex aleatórios (uso local)
  scripts/rastro_caprover.py create-app rastro-chat --image IMG --volume nome:/caminho \
        [--volume ...] [--env K=V ...] [--execute]
  scripts/rastro_caprover.py env-from APP_ORIGEM APP_DESTINO CHAVE [CHAVE ...] [--execute]
        # copia o valor de variáveis entre apps sem imprimir o valor
  scripts/rastro_caprover.py univaja-secret [--provision-env ARQ] [--execute]
        # compara RASTRO_NATIVE_SECRET do rastro-broker com UNIVAJA_MQTT_SECRET do provision.env
        # (padrão ~/.config/univaja/provision.env); com --execute grava/atualiza a linha (modo 600)

Env: --env-file (padrão .env na raiz do repo) com CAP_URL e CAP_PASS.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def ler_env(caminho: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.lstrip().startswith("#"):
            k, v = linha.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


class Cap:
    def __init__(self, base: str, senha: str) -> None:
        self.base = base.rstrip("/")
        self.token = self._chamar("/api/v2/login", {"password": senha}, auth=False)["data"]["token"]

    def _chamar(self, caminho: str, corpo: dict | None = None, auth: bool = True) -> dict:
        cab = {"Content-Type": "application/json", "x-namespace": "captain"}
        if auth:
            cab["x-captain-auth"] = self.token
        req = urllib.request.Request(
            self.base + caminho,
            data=json.dumps(corpo).encode() if corpo is not None else None,
            headers=cab,
        )
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as exc:  # corpo pode ter detalhe útil, nunca credencial
            raise SystemExit(f"ERRO HTTP {exc.code} em {caminho}: {exc.read()[:300]!r}") from None
        if resp.get("status") not in (100, 101, 102, 200):
            raise SystemExit(f"ERRO CapRover em {caminho}: {resp.get('description')}")
        return resp

    def apps(self) -> dict[str, dict]:
        defs = self._chamar("/api/v2/user/apps/appDefinitions")["data"]["appDefinitions"]
        return {a["appName"]: a for a in defs}


def imagem_atual(app: dict) -> str | None:
    for v in app.get("versions") or []:
        if v.get("version") == app.get("deployedVersion"):
            return v.get("deployedImageName")
    return None


def cmd_status(cap: Cap, args) -> None:
    for nome, app in sorted(cap.apps().items()):
        if not nome.startswith("rastro"):
            continue
        print(f"{nome}: {imagem_atual(app)} | env: {sorted(e['key'] for e in app.get('envVars', []))}")
        print(f"   volumes: {[(v.get('volumeName'), v.get('containerPath')) for v in app.get('volumes', [])]}")


def cmd_deploy_image(cap: Cap, args) -> None:
    app = cap.apps().get(args.app) or sys.exit(f"app inexistente: {args.app}")
    print(f"{args.app}: {imagem_atual(app)} -> {args.image}")
    if not args.execute:
        return print("(dry-run; use --execute)")
    definicao = json.dumps({"schemaVersion": 2, "imageName": args.image})
    cap._chamar(f"/api/v2/user/apps/appData/{args.app}?detached=1",
                {"captainDefinitionContent": definicao, "gitHash": ""})
    print("deploy enfileirado (detached); confira com 'status'")


def _salvar_def(cap: Cap, app: dict, env_vars: list[dict]) -> None:
    campos = ("appName", "instanceCount", "captainDefinitionRelativeFilePath", "notExposeAsWebApp",
              "forceSsl", "websocketSupport", "volumes", "ports", "nodeId", "appPushWebhook",
              "customNginxConfig", "preDeployFunction", "serviceUpdateOverride",
              "containerHttpPort", "description", "httpAuth", "tags", "redirectDomain")
    corpo = {k: app[k] for k in campos if k in app}
    corpo["envVars"] = env_vars
    cap._chamar("/api/v2/user/apps/appDefinitions/update", corpo)


def _mesclar(app: dict, novos: dict[str, str]) -> list[dict]:
    atual = {e["key"]: e["value"] for e in app.get("envVars", [])}
    atual.update(novos)
    return [{"key": k, "value": v} for k, v in atual.items()]


def cmd_set_env(cap: Cap, args) -> None:
    app = cap.apps().get(args.app) or sys.exit(f"app inexistente: {args.app}")
    novos = dict(p.split("=", 1) for p in args.pares)
    print(f"{args.app}: definir/alterar variáveis {sorted(novos)} (valores não impressos)")
    if args.execute:
        _salvar_def(cap, app, _mesclar(app, novos))
        print("ok")
    else:
        print("(dry-run; use --execute)")


def cmd_env_from(cap: Cap, args) -> None:
    apps = cap.apps()
    origem = apps.get(args.origem) or sys.exit(f"app inexistente: {args.origem}")
    destino = apps.get(args.destino) or sys.exit(f"app inexistente: {args.destino}")
    valores = {e["key"]: e["value"] for e in origem.get("envVars", [])}
    faltam = [k for k in args.chaves if k not in valores]
    if faltam:
        sys.exit(f"faltam em {args.origem}: {faltam}")
    print(f"copiar {args.chaves} de {args.origem} para {args.destino} (valores não impressos)")
    if args.execute:
        _salvar_def(cap, destino, _mesclar(destino, {k: valores[k] for k in args.chaves}))
        print("ok")
    else:
        print("(dry-run; use --execute)")


def cmd_create_app(cap: Cap, args) -> None:
    if args.app in cap.apps():
        sys.exit(f"app já existe: {args.app} (use deploy-image/set-env)")
    volumes = [{"volumeName": v.split(":", 1)[0], "containerPath": v.split(":", 1)[1]} for v in args.volume]
    env = dict(p.split("=", 1) for p in args.env)
    print(f"criar {args.app}: imagem {args.image}; volumes {volumes}; env {sorted(env)}")
    if not args.execute:
        return print("(dry-run; use --execute)")
    cap._chamar("/api/v2/user/apps/appDefinitions/register",
                {"appName": args.app, "hasPersistentData": bool(volumes)})
    app = cap.apps()[args.app]
    app["volumes"] = volumes
    app["notExposeAsWebApp"] = True
    app["instanceCount"] = 1
    _salvar_def(cap, app, [{"key": k, "value": v} for k, v in env.items()])
    cap._chamar(f"/api/v2/user/apps/appData/{args.app}?detached=1",
                {"captainDefinitionContent": json.dumps({"schemaVersion": 2, "imageName": args.image}),
                 "gitHash": ""})
    print("criado e deploy enfileirado")


def cmd_univaja_secret(cap: Cap, args) -> None:
    broker = cap.apps().get("rastro-broker") or sys.exit("app inexistente: rastro-broker")
    valor = next((e["value"] for e in broker.get("envVars", []) if e["key"] == "RASTRO_NATIVE_SECRET"), None)
    if not valor:
        sys.exit("rastro-broker sem RASTRO_NATIVE_SECRET")
    arq = Path(args.provision_env).expanduser()
    atual = ler_env(arq).get("UNIVAJA_MQTT_SECRET") if arq.exists() else None
    if atual == valor:
        print(f"OK: UNIVAJA_MQTT_SECRET em {arq} é igual a RASTRO_NATIVE_SECRET (tamanho {len(valor)})")
        return
    estado = "ausente" if atual is None else "DIFERENTE do broker"
    print(f"UNIVAJA_MQTT_SECRET em {arq}: {estado} (valores não impressos)")
    if not args.execute:
        print("(dry-run; use --execute para gravar)")
        return
    linhas = arq.read_text(encoding="utf-8").splitlines() if arq.exists() else []
    novas, achou = [], False
    for ln in linhas:
        if ln.split("=", 1)[0].strip() == "UNIVAJA_MQTT_SECRET":
            novas.append(f"UNIVAJA_MQTT_SECRET={valor}"); achou = True
        else:
            novas.append(ln)
    if not achou:
        novas.append(f"UNIVAJA_MQTT_SECRET={valor}")
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.touch(mode=0o600)
    arq.chmod(0o600)
    arq.write_text("\n".join(novas) + "\n", encoding="utf-8")
    print("gravado (modo 600)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(RAIZ / ".env"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("gen-secret")
    p = sub.add_parser("deploy-image"); p.add_argument("app"); p.add_argument("image")
    p = sub.add_parser("set-env"); p.add_argument("app"); p.add_argument("pares", nargs="+")
    p = sub.add_parser("env-from"); p.add_argument("origem"); p.add_argument("destino"); p.add_argument("chaves", nargs="+")
    p = sub.add_parser("create-app"); p.add_argument("app"); p.add_argument("--image", required=True)
    p.add_argument("--volume", action="append", default=[]); p.add_argument("--env", action="append", default=[])
    p = sub.add_parser("univaja-secret"); p.add_argument("--provision-env", default="~/.config/univaja/provision.env")
    for p in sub.choices.values():
        p.add_argument("--execute", action="store_true", help="aplica de verdade (padrão: dry-run)")
    args = ap.parse_args()
    if args.cmd == "gen-secret":
        print(secrets.token_hex(16))
        return 0
    env = ler_env(Path(args.env_file))
    cap = Cap(env["CAP_URL"], env["CAP_PASS"])
    {"status": cmd_status, "deploy-image": cmd_deploy_image, "set-env": cmd_set_env,
     "env-from": cmd_env_from, "create-app": cmd_create_app, "univaja-secret": cmd_univaja_secret}[args.cmd](cap, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
