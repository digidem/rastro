#!/usr/bin/env python3
"""Verificação e2e da ponte Rastro (Fase 4, T3/T9).

Conecta ao PostgreSQL (env RASTRO_PG_*) e reporta por nó: nome amigável, quantas
posições nos últimos N minutos, idade do fix mais recente — e imprime PASS ou
FALHA: <motivo> (PT-BR).

REGRAS DE SIGILO E VERIFICAÇÃO:
- NUNCA imprime coordenadas nem payloads (sensibilidade — AGENTS.md).
- Idades são medidas em `received_at` (relógio do GATEWAY), NÃO no relógio do
  dispositivo: nós sem GNSS têm relógio não confiável (R5/F6).
- Relógio implausível do dispositivo é sinalizado como AVISO (não causa FALHA,
  pois o sistema é desenhado para usar received_at como verdade operacional).
- Certificado do broker: valida existência do server.crt e alerta se expirar em < 60 dias.

Env:
  RASTRO_PG_HOST/PORT/DB/USER/PASSWORD (com fallback para POSTGRES_USER/PASSWORD)
  RASTRO_E2E_WINDOW_SECS (default 900 — 15 min)
  RASTRO_E2E_CERT (caminho de server.crt; default deploy/mosquitto/certs/server.crt)
  RASTRO_E2E_NODES (opcional: nós esperados separados por vírgula, ex: "!0decade0,!0badf00d")
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Sanitizador de nomes vindos do banco — mesmas heurísticas do fleet_sync (coordenada/
# chave/URL de canal em valores de texto). Cópia local: fleet_sync importa o pacote
# `meshtastic` no nível do módulo e sai sem ele; este script precisa rodar só com psycopg.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fleet_sync import COORD_VALUE_RE, KEY_VALUE_RE  # noqa: E402
except (ImportError, SystemExit):  # fallback: réplica das regexes (mantenha em sincronia)
    COORD_VALUE_RE = re.compile(r"-?\d{1,2}\.\d{2,}\s*[,;/ ]\s*-?\d{1,3}\.\d{2,}")
    KEY_VALUE_RE = re.compile(r"meshtastic\.org/e/#|[A-Za-z0-9+/]{22}==|[A-Za-z0-9+/]{32,}={0,2}")


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "PgConfig":
        pwd = os.environ.get("RASTRO_PG_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
        if not pwd:
            raise RuntimeError("RASTRO_PG_PASSWORD ou POSTGRES_PASSWORD não definida")
        return cls(
            host=os.environ.get("RASTRO_PG_HOST", "127.0.0.1"),
            port=int(os.environ.get("RASTRO_PG_PORT", "5432")),
            dbname=os.environ.get("RASTRO_PG_DB") or os.environ.get("RASTRO_DB", "rastro"),
            user=os.environ.get("RASTRO_PG_USER") or os.environ.get("POSTGRES_USER", "rastro"),
            password=pwd,
        )


def fmt_idade(secs: float) -> str:
    if secs < 0:
        return "futuro(?)"
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs // 60)} min"
    return f"{secs / 3600:.1f} h"


def cert_dias() -> tuple[int | None, str | None]:
    import subprocess

    cert = os.environ.get(
        "RASTRO_E2E_CERT", os.path.join(REPO_ROOT, "deploy/mosquitto/certs/server.crt")
    )
    if not os.path.exists(cert):
        return None, f"arquivo de certificado não encontrado: {cert}"
    try:
        out = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        fim = datetime.strptime(out.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        return int((fim - datetime.now(timezone.utc)).total_seconds() // 86400), None
    except Exception as exc:
        return None, f"falha ao ler certificado com openssl: {exc}"


def main() -> int:
    window = int(os.environ.get("RASTRO_E2E_WINDOW_SECS", "900"))
    expected_nodes_raw = os.environ.get("RASTRO_E2E_NODES", "").strip()
    expected_nodes = [x.strip() for x in expected_nodes_raw.split(",") if x.strip()] if expected_nodes_raw else []

    falhas: list[str] = []
    avisos: list[str] = []

    try:
        cfg = PgConfig.from_env()
        conn = psycopg.connect(
            host=cfg.host, port=cfg.port, dbname=cfg.dbname,
            user=cfg.user, password=cfg.password,
        )
    except Exception as exc:
        print(f"FALHA: sem conexão com o PostgreSQL: {exc}")
        return 1

    print(f"== Rastro e2e — janela de {window // 60} min ==")
    agora = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.node_num, n.node_id, COALESCE(n.friendly_name, n.node_id) AS nome,
                   (SELECT count(*) FROM positions p
                     WHERE p.node_num = n.node_num
                       AND p.received_at > now() - make_interval(secs => %s)) AS recentes,
                   (SELECT max(p.received_at) FROM positions p
                     WHERE p.node_num = n.node_num) AS ultimo,
                   (SELECT p.time_source FROM positions p
                     WHERE p.node_num = n.node_num
                     ORDER BY p.received_at DESC LIMIT 1) AS fonte,
                   (SELECT count(*) FROM positions p
                     WHERE p.node_num = n.node_num
                       AND p.received_at > now() - make_interval(secs => %s)
                       AND p.time_source = 'device'
                       AND p.pos_time < now() - interval '365 days') AS rel_implausivel
            FROM nodes n ORDER BY n.node_num
            """,
            (window, window),
        )
        linhas = cur.fetchall()
    conn.close()

    if not linhas:
        print("FALHA: nenhum nó no banco — a ponte/ingester estão entregando?")
        return 1

    nodes_seen = set()
    for node_num, node_id, nome, recentes, ultimo, fonte, rel_implausivel in linhas:
        # Nomes vêm do dispositivo (friendly_name) e podem conter coordenada/PSK/URL
        # de canal. node_id (hex) sempre imprimível; nome só quando passa no sanitizador.
        if COORD_VALUE_RE.search(nome) or KEY_VALUE_RE.search(nome):
            nome_exibicao = "<nome não-imprimível>"
        else:
            nome_exibicao = nome
        idade_fmt = fmt_idade((agora - ultimo).total_seconds()) if ultimo else "nunca"
        aviso_rel = " [AVISO: relógio do dispositivo implausível na janela]" if rel_implausivel else ""
        print(
            f"  {nome_exibicao:28s} ({node_id}) posições(janela)={recentes:>3}  último fix há {idade_fmt}"
            f" (fonte {fonte or '?'}){aviso_rel}"
        )
        if rel_implausivel:
            avisos.append(f"{node_id}: relógio do dispositivo implausível na janela (usando received_at do gateway)")
        if recentes and ultimo:
            nodes_seen.add(node_id)
            nodes_seen.add(str(node_num))

    # Validação de nós
    if expected_nodes:
        missing = [en for en in expected_nodes if en not in nodes_seen]
        if missing:
            falhas.append(f"nós esperados sem posição na janela: {', '.join(missing)}")
    else:
        if not nodes_seen:
            falhas.append("nenhum nó com posição dentro da janela")

    # Certificado do broker
    dias, cert_err = cert_dias()
    if cert_err is not None:
        falhas.append(f"certificado do broker: {cert_err}")
    elif dias is not None:
        print(f"  certificado do broker: expira em {dias} dias")
        if dias < 60:
            falhas.append(f"certificado expira em {dias} dias (< 60) — renovar")

    for a in avisos:
        print(f"AVISO: {a}")

    if falhas:
        for f in falhas:
            print(f"FALHA: {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
