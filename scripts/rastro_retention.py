#!/usr/bin/env python3
"""Retenção de dados Rastro — DRY-RUN por padrão (Fase 5; decisões R6/R3).

Modo destrutivo exige DUAS condições simultâneas:
  1. ``RASTRO_RETENTION_DAYS`` ratificado pela equipe (> 0) no ambiente; e
  2. a flag ``--executar`` na linha de comando.

Sem isso o script apenas DIMENSIONA (somente SELECT) e sai 0 — nunca apaga.
``RASTRO_RETENTION_DRY_RUN`` (padrão ``true``) é uma chave extra: ``false`` sozinho
NÃO ativa o modo destrutivo — só ``--executar`` ativa, e a política precisa estar
ratificada. VACUUM não é executado (fica para o autovacuum).

Env (mesmos nomes do ingester): RASTRO_PG_HOST/PORT/DB/USER/PASSWORD.

Saída 100% em PT-BR; NUNCA imprime coordenadas — apenas contagens e datas.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg

JANELA_ILUSTRATIVA_DIAS = 365


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> "PgConfig":
        """Mesmos nomes e padrões do ingester (services/rastro_gateway/ingest/db.py)."""
        env = os.environ if env is None else env
        password = env.get("RASTRO_PG_PASSWORD", "")
        if not password:
            raise RuntimeError("RASTRO_PG_PASSWORD não definida")
        return cls(
            host=env.get("RASTRO_PG_HOST", "localhost"),
            port=int(env.get("RASTRO_PG_PORT", "5432")),
            dbname=env.get("RASTRO_PG_DB", "rastro"),
            user=env.get("RASTRO_PG_USER", "rastro"),
            password=password,
        )


def parse_keep_days(env: dict | None = None) -> int | None:
    """Retorna a janela ratificada (int > 0) ou None se não ratificada.

    Não definida, vazia, 0 ou negativa ⇒ política NÃO ratificada (R6).
    """
    env = os.environ if env is None else env
    bruto = (env.get("RASTRO_RETENTION_DAYS") or "").strip()
    if not bruto:
        return None
    try:
        dias = int(bruto)
    except ValueError:
        return None
    return dias if dias > 0 else None


def dry_run_env(env: dict | None = None) -> bool:
    """``RASTRO_RETENTION_DRY_RUN`` — padrão ``true``. Só falso com 0/false/no/off."""
    env = os.environ if env is None else env
    return (env.get("RASTRO_RETENTION_DRY_RUN", "true") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def conectar(cfg: PgConfig) -> psycopg.Connection:
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password,
    )


def fmt_ts(ts) -> str:
    """Timestamp em horário local, formato PT-BR. Nunca imprime coordenadas."""
    if ts is None:
        return "—"
    return ts.astimezone().strftime("%d/%m/%Y %H:%M:%S")


def dimensar_tabela(conn: psycopg.Connection, tabela: str, coluna_tempo: str, dias: int) -> tuple[int, int, object, object]:
    """(total, fora_da_janela, mais_antiga, mais_nova) — somente leitura."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), min({coluna_tempo}), max({coluna_tempo}) FROM {tabela}")
        total, antiga, nova = cur.fetchone()
        cur.execute(
            f"SELECT count(*) FROM {tabela} WHERE {coluna_tempo} < now() - make_interval(days => %s)",
            (dias,),
        )
        (fora,) = cur.fetchone()
    return total, fora, antiga, nova


def nos_orfaos(conn: psycopg.Connection, dias: int) -> list[tuple[str, object]]:
    """Nós que a retenção APAGARIA: sem positions, sem telemetry e sem reportar há > janela."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.node_id, n.last_seen
            FROM nodes n
            WHERE NOT EXISTS (SELECT 1 FROM positions p       WHERE p.node_num = n.node_num)
              AND NOT EXISTS (SELECT 1 FROM device_telemetry t WHERE t.node_num = n.node_num)
              AND n.last_seen < now() - make_interval(days => %s)
            ORDER BY n.last_seen
            """,
            (dias,),
        )
        return [(node_id, ultima) for node_id, ultima in cur.fetchall()]


def rodar_dry_run(conn: psycopg.Connection, dias: int, ilustrativa: bool) -> None:
    origem = (
        "ILUSTRATIVA — RASTRO_RETENTION_DAYS não ratificada (R6); apenas para dimensionar"
        if ilustrativa
        else "ratificada via RASTRO_RETENTION_DAYS"
    )
    print("== Rastro retenção — DRY-RUN (nenhum dado será apagado) ==")
    print(f"Janela: {dias} dias ({origem})")
    print()
    print(f"{'tabela':<20}{'total':>12}{'fora da janela':>16}  {'mais antiga':<20}{'mais nova':<20}")
    for tabela, coluna in (("positions", "pos_time"), ("device_telemetry", "telem_time")):
        total, fora, antiga, nova = dimensar_tabela(conn, tabela, coluna, dias)
        print(
            f"{tabela:<20}{total:>12}{fora:>16}  {fmt_ts(antiga):<20}{fmt_ts(nova):<20}"
        )
    print()
    orfaos = nos_orfaos(conn, dias)
    print(f"Nós órfãos que seriam apagados (sem positions nem telemetry, last_seen > {dias} dias): {len(orfaos)}")
    for node_id, ultima in orfaos:
        print(f"  {node_id:<12} último acesso: {fmt_ts(ultima)}")
    print()
    print("Dry-run concluído — nada foi apagado.")


def rodar_execucao(conn: psycopg.Connection, dias: int) -> None:
    print("== Rastro retenção — EXECUTANDO (destrutivo) ==")
    print(f"Janela: {dias} dias (ratificada via RASTRO_RETENTION_DAYS)")
    print()
    # UMA transação: psycopg faz commit ao sair do bloco sem erro e rollback em exceção.
    # Contagens saem só DEPOIS do commit: se o commit falhar, nada de "N apagada(s)".
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM positions WHERE pos_time < now() - make_interval(days => %s)",
                (dias,),
            )
            apagadas_positions = cur.rowcount
            cur.execute(
                "DELETE FROM device_telemetry WHERE telem_time < now() - make_interval(days => %s)",
                (dias,),
            )
            apagadas_telemetry = cur.rowcount
            cur.execute(
                """
                DELETE FROM nodes n
                WHERE NOT EXISTS (SELECT 1 FROM positions p       WHERE p.node_num = n.node_num)
                  AND NOT EXISTS (SELECT 1 FROM device_telemetry t WHERE t.node_num = n.node_num)
                  AND n.last_seen < now() - make_interval(days => %s)
                """,
                (dias,),
            )
            apagados_nodes = cur.rowcount
    print(f"positions: {apagadas_positions} linha(s) apagada(s)")
    print(f"device_telemetry: {apagadas_telemetry} linha(s) apagada(s)")
    print(f"nodes (órfãos sem reportar há mais de {dias} dias): {apagados_nodes} nó(s) apagado(s)")
    print()
    print("Execução concluída — VACUUM não executado (autovacuum cuida disso).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retenção de dados Rastro (dry-run por padrão; --executar exige política ratificada)"
    )
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                       help="apenas dimensiona (padrão; nunca apaga)")
    grupo.add_argument("--executar", dest="executar", action="store_true",
                       help="executa a retenção destrutiva (exige RASTRO_RETENTION_DAYS ratificado)")
    args = parser.parse_args(argv)

    modo_executar = args.executar
    if modo_executar:
        dias = parse_keep_days()
        if dias is None:
            print(
                "ERRO: política de retenção não ratificada — apenas --dry-run. "
                "Defina RASTRO_RETENTION_DAYS (> 0) ratificado pela equipe para usar --executar.",
                file=sys.stderr,
            )
            return 2
        # --executar tem precedência sobre RASTRO_RETENTION_DRY_RUN=false (a flag é explícita).
        cfg = PgConfig.from_env()  # valida senha ANTES de qualquer conexão
        try:
            conn = conectar(cfg)
        except psycopg.Error as exc:
            print(f"ERRO: falha ao conectar ao PostgreSQL: {exc}", file=sys.stderr)
            return 1
        try:
            rodar_execucao(conn, dias)
        except psycopg.Error as exc:
            print(f"ERRO: falha durante a retenção (transação revertida): {exc}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0

    # Dry-run (padrão). RASTRO_RETENTION_DRY_RUN=false sozinho NÃO destrói nada:
    if not dry_run_env():
        print(
            "AVISO: RASTRO_RETENTION_DRY_RUN=false não ativa o modo destrutivo; "
            "o modo destrutivo exige --executar + política ratificada.",
            file=sys.stderr,
        )
    dias = parse_keep_days()
    ilustrativa = dias is None
    if ilustrativa:
        dias = JANELA_ILUSTRATIVA_DIAS
    cfg = PgConfig.from_env()
    try:
        conn = conectar(cfg)
    except psycopg.Error as exc:
        print(f"ERRO: falha ao conectar ao PostgreSQL: {exc}", file=sys.stderr)
        return 1
    conn.read_only = True  # garantia aplicada pelo próprio Postgres (gate F5 NIT)
    try:
        rodar_dry_run(conn, dias, ilustrativa)
    except psycopg.Error as exc:
        print(f"ERRO: falha durante o dry-run: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:  # ex.: RASTRO_PG_PASSWORD não definida
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
