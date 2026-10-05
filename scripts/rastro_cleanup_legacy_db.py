#!/usr/bin/env python3
"""Inspetor READ-ONLY do banco legado 'mqtt' no PostgreSQL.

A remoção do banco 'mqtt' foi executada em 2026-10-01 (Tarefa 1 do TODO.md) e o código
destrutivo (DROP/ALTER, token, confirmação) foi removido do script em seguida.
O script apenas confere o estado da instância e NUNCA escreve:

1. Lê credenciais de DB= / DATABASE_URL / RASTRO_PG_ADMIN_URL do .env (percent-encoding, sslmode).
2. Conecta no banco de manutenção (postgres) e confere que 'rastro' existe e responde.
3. Confere que 'superset_metastore' não contém tabelas do Rastro.
4. Informa se 'mqtt' existe; se existir, imprime o inventário estrito e valida contra o casco Prisma
   vazio conhecido (somente relatório; nenhuma ação destrutiva).

Uso:
  python3 scripts/rastro_cleanup_legacy_db.py            # relatório
  python3 scripts/rastro_cleanup_legacy_db.py --selftest # testes internos sem servidor
  python3 scripts/rastro_cleanup_legacy_db.py --env-file /caminho/para/.env
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

try:
    import psycopg
except ImportError:
    print("ERRO: O driver 'psycopg' não está instalado no ambiente Python.", file=sys.stderr)
    print("Instale com: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)


def parse_env_file(filepath: Path) -> dict[str, str]:
    """Lê pares CHAVE=VALOR de um arquivo .env ignorando comentários e espaços."""
    env_vars: dict[str, str] = {}
    if not filepath.exists() or not filepath.is_file():
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                env_vars[k] = v
    return env_vars


def get_conn_params(args: argparse.Namespace) -> dict[str, str | int]:
    """Consolida parâmetros de conexão a partir de CLI, arquivo .env e variáveis de ambiente."""
    env_vars: dict[str, str] = {}

    if args.env_file:
        candidate = Path(args.env_file)
        if not candidate.is_file():
            print(f"ERRO: O arquivo especificado em --env-file ({args.env_file}) não existe.", file=sys.stderr)
            sys.exit(1)
        env_vars = parse_env_file(candidate)
        print(f"[INFO] Credenciais carregadas de: {candidate}")
    else:
        for fallback in [Path(".env"), Path("deploy/.env"), Path("../.env")]:
            if fallback.is_file():
                env_vars = parse_env_file(fallback)
                print(f"[INFO] Credenciais carregadas de: {fallback}")
                break

    db_url = (
        args.url
        or env_vars.get("DATABASE_URL")
        or env_vars.get("RASTRO_PG_ADMIN_URL")
        or env_vars.get("DB")
        or os.getenv("DATABASE_URL")
        or os.getenv("RASTRO_PG_ADMIN_URL")
        or os.getenv("DB")
    )

    if db_url:
        parsed = urlparse(db_url)
        sslmode = ""
        if parsed.query:
            for k, v in parse_qsl(parsed.query, keep_blank_values=True):
                if k.lower() == "sslmode":
                    sslmode = v
        params: dict[str, str | int] = {
            "host": args.host or parsed.hostname or "localhost",
            "port": args.port or parsed.port or 5432,
            "user": unquote(parsed.username) if parsed.username else (args.user or "postgres"),
            "password": unquote(parsed.password) if parsed.password else (args.password or ""),
            "dbname": "postgres",  # Conectar sempre em postgres para operações de manutenção
        }
        if sslmode:
            params["sslmode"] = sslmode
        return params

    host = (
        args.host
        or env_vars.get("PGHOST")
        or env_vars.get("RASTRO_PG_HOST")
        or os.getenv("PGHOST")
        or os.getenv("RASTRO_PG_HOST")
        or "localhost"
    )
    port = int(
        args.port
        or env_vars.get("PGPORT")
        or env_vars.get("RASTRO_PG_PORT")
        or os.getenv("PGPORT")
        or os.getenv("RASTRO_PG_PORT")
        or 5432
    )
    user = (
        args.user
        or env_vars.get("PGUSER")
        or env_vars.get("POSTGRES_USER")
        or os.getenv("PGUSER")
        or os.getenv("POSTGRES_USER")
        or "postgres"
    )
    password = (
        args.password
        or env_vars.get("PGPASSWORD")
        or env_vars.get("POSTGRES_PASSWORD")
        or os.getenv("PGPASSWORD")
        or os.getenv("POSTGRES_PASSWORD")
        or ""
    )
    sslmode = (
        env_vars.get("PGSSLMODE")
        or env_vars.get("RASTRO_PG_SSLMODE")
        or os.getenv("PGSSLMODE")
        or os.getenv("RASTRO_PG_SSLMODE")
        or ""
    )

    params = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": "postgres",
    }
    if sslmode:
        params["sslmode"] = sslmode
    return params



EXPECTED_MQTT_EXTENSIONS = ["plpgsql"]
EXPECTED_RELATION_OWNERS = ["mqtt"]
# Conjunto (schema, nome, relkind) do casco Prisma vazio que existia em 'mqtt'.
EXPECTED_RELATIONS = [
    ("public", "_prisma_migrations", "r"),
    ("public", "_prisma_migrations_pkey", "i"),
]

_NOT_SYS = "n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'"
_RO_OPTIONS = "-c default_transaction_read_only=on"


def collect_inventory(mcur) -> dict:
    """Inventário completo de 'mqtt' por catálogos (somente SELECT)."""
    mcur.execute(f"SELECT nspname FROM pg_namespace n WHERE {_NOT_SYS} ORDER BY 1;")
    schemas = [r[0] for r in mcur.fetchall()]
    # relkinds: r p v m S f (relações), c (tipos compostos standalone), i/I (índices)
    mcur.execute(
        "SELECT n.nspname, c.relname, c.relkind::text FROM pg_class c "
        f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_NOT_SYS} "
        "AND c.relkind IN ('r','p','v','m','S','f','c','i','I') ORDER BY 1,2;"
    )
    relations = [(r[0], r[1], r[2]) for r in mcur.fetchall()]
    mcur.execute(
        "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c "
        f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_NOT_SYS} "
        "AND c.relkind IN ('r','p','v','m','S','f','c','i','I');"
    )
    owners = sorted(r[0] for r in mcur.fetchall())
    mcur.execute(
        "SELECT n.nspname, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {_NOT_SYS} AND NOT EXISTS "
        "(SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e');"
    )
    functions = [(r[0], r[1]) for r in mcur.fetchall()]
    mcur.execute(
        "SELECT n.nspname, t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        f"WHERE {_NOT_SYS} AND t.typrelid = 0 AND NOT EXISTS "
        "(SELECT 1 FROM pg_type a WHERE a.typarray = t.oid) AND NOT EXISTS "
        "(SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e');"
    )
    types = [(r[0], r[1]) for r in mcur.fetchall()]
    mcur.execute("SELECT extname FROM pg_extension ORDER BY 1;")
    extensions = [r[0] for r in mcur.fetchall()]
    mcur.execute("SELECT count(*) FROM pg_largeobject_metadata;")
    large_objects = mcur.fetchone()[0]
    prisma_is_table = ("public", "_prisma_migrations", "r") in relations
    prisma_rows = None
    if prisma_is_table:
        mcur.execute("SELECT count(*) FROM public._prisma_migrations;")
        prisma_rows = mcur.fetchone()[0]
    return {
        "schemas": schemas,
        "relations": relations,
        "owners": owners,
        "functions": functions,
        "types": types,
        "extensions": extensions,
        "large_objects": large_objects,
        "prisma_is_table": prisma_is_table,
        "prisma_rows": prisma_rows,
    }

def validate_inventory(inv) -> list[str]:
    """Lista de divergências do inventário contra o casco Prisma vazio; vazia = banco vazio conhecido."""
    e: list[str] = []
    if inv["schemas"] != ["public"]:
        e.append(f"schemas além de 'public': {inv['schemas']}")
    if sorted(inv["relations"]) != sorted(EXPECTED_RELATIONS):
        e.append(f"relações != esperado. vivo={inv['relations']} esperado={EXPECTED_RELATIONS}")
    if inv["owners"] != EXPECTED_RELATION_OWNERS:
        e.append(f"donos das relações != {EXPECTED_RELATION_OWNERS}: {inv['owners']}")
    if not inv["prisma_is_table"]:
        e.append("'public._prisma_migrations' ausente OU não é tabela (relkind != 'r')")
    elif inv["prisma_rows"] != 0:
        e.append(f"_prisma_migrations tem {inv['prisma_rows']} linha(s); esperado 0")
    if inv["functions"]:
        e.append(f"funções user: {inv['functions'][:5]}")
    if inv["types"]:
        e.append(f"tipos user: {inv['types'][:5]}")
    if inv["large_objects"] > 0:
        e.append(f"{inv['large_objects']} large object(s)")
    if sorted(inv["extensions"]) != sorted(EXPECTED_MQTT_EXTENSIONS):
        e.append(f"extensões != {EXPECTED_MQTT_EXTENSIONS}: {inv['extensions']}")
    return e


_GOOD_INV = {
    "schemas": ["public"], "relations": list(EXPECTED_RELATIONS), "owners": ["mqtt"], "functions": [],
    "types": [], "extensions": ["plpgsql"], "large_objects": 0, "prisma_is_table": True, "prisma_rows": 0,
}


def run_selftest() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails += 1

    def v(**over):
        return validate_inventory({**_GOOD_INV, **over})

    check("válido", v() == [])
    check("schema extra", bool(v(schemas=["public", "x"])))
    check("view homônima de _prisma_migrations", bool(v(
        relations=[("public", "_prisma_migrations", "v"), EXPECTED_RELATIONS[1]], prisma_is_table=False)))
    check("tipo composto standalone", bool(v(relations=[*EXPECTED_RELATIONS, ("public", "t", "c")])))
    check("relação extra", bool(v(relations=[*EXPECTED_RELATIONS, ("public", "v1", "v")])))
    check("_prisma_migrations ausente", bool(v(prisma_is_table=False)))
    check("_prisma_migrations com 1 linha", bool(v(prisma_rows=1)))
    check("dono das relações", bool(v(owners=["cmiadmin"])))
    check("função user", bool(v(functions=[("public", "f")])))
    check("tipo user", bool(v(types=[("public", "t")])))
    check("large object", bool(v(large_objects=1)))
    check("extensão extra", bool(v(extensions=["plpgsql", "postgis"])))
    src = Path(__file__).read_text(encoding="utf-8")
    # termos montados por concatenação para não casarem com esta própria lista
    forbidden = ["DROP" " DATABASE", "ALTER" " DATABASE", "pg_" "terminate_backend", "--" "execute"]
    check("script não contém SQL destrutivo nem modo de execução", not any(t in src for t in forbidden))
    print(f"\nSELFTEST: {'OK' if fails == 0 else f'{fails} FALHA(S)'}")
    return 0 if fails == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Limpeza segura do banco legado 'mqtt' no PostgreSQL."
    )
    parser.add_argument("--env-file", type=str, help="Caminho para arquivo .env com credenciais.")
    parser.add_argument("--url", type=str, help="Connection string PostgreSQL.")
    parser.add_argument("--host", type=str, help="Host do PostgreSQL.")
    parser.add_argument("--port", type=int, help="Porta do PostgreSQL (padrão: 5432).")
    parser.add_argument("--user", type=str, help="Usuário administrador do PostgreSQL.")
    parser.add_argument("--password", type=str, help="PROIBIDO (credencial em argv); use DB= do .env.")
    parser.add_argument("--selftest", action="store_true", help="Testes internos sem servidor.")

    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.password or args.url:
        print("[ERRO FATAL] --password/--url proibidos (credencial em argv). Use DB= do .env.", file=sys.stderr)
        return 1

    conn_params = get_conn_params(args)
    host = conn_params["host"]
    user = conn_params["user"]
    port = conn_params["port"]

    print("=" * 70)
    print("RASTRO — Inspeção de Banco Legado (mqtt) [READ-ONLY]")
    print("=" * 70)
    print(f"Alvo PostgreSQL: {user}@{host}:{port}/postgres")
    print("-" * 70)

    try:
        conn = psycopg.connect(**conn_params, autocommit=True)
    except Exception as e:
        print(f"[ERRO FATAL] Não foi possível conectar ao PostgreSQL: {e}", file=sys.stderr)
        return 1

    try:
        with conn.cursor() as cur:
            # 1. Conferir bancos existentes
            cur.execute(
                "SELECT datname FROM pg_database WHERE datname IN ('rastro', 'mqtt', 'superset_metastore', 'postgres');"
            )
            databases = {row[0] for row in cur.fetchall()}

            print(f"[CHECK] Bancos detectados: {sorted(databases)}")
            if "postgres" in databases:
                print("[OK] Banco administrativo 'postgres' confirmado e ativo.")
            else:
                print("[AVISO] Banco 'postgres' não encontrado na lista!")

            # 2. Conferir obrigatoriedade e integridade do banco ativo 'rastro'
            if "rastro" not in databases:
                print(
                    "\n[ERRO FATAL] O banco ativo 'rastro' NÃO foi encontrado nesta instância!",
                    file=sys.stderr,
                )
                print(
                    "Abortando imediatamente para proteger o ambiente contra conexão a servidor incorreto.",
                    file=sys.stderr,
                )
                return 1

            print("[OK] Banco ativo 'rastro' encontrado. Verificando integridade das tabelas operacionais...")
            try:
                with psycopg.connect(
                    **{**conn_params, "dbname": "rastro"}, autocommit=True
                ) as rconn:
                    with rconn.cursor() as rcur:
                        rcur.execute("SELECT count(*) FROM rastro.nodes;")
                        node_count = rcur.fetchone()[0]
                        rcur.execute("SELECT count(*) FROM rastro.positions;")
                        pos_count = rcur.fetchone()[0]
                        print(f"     -> Nós registrados em 'rastro.nodes': {node_count}")
                        print(f"     -> Posições registradas em 'rastro.positions': {pos_count}")
            except Exception as e:
                print(
                    f"\n[ERRO FATAL] Falha ao verificar as tabelas operacionais do banco 'rastro': {e}",
                    file=sys.stderr,
                )
                print("Abortando por segurança: 'rastro' precisa estar saudável antes de qualquer alteração.", file=sys.stderr)
                return 1

            # 2b. Inspecionar se superset_metastore existe e se está limpo de objetos do Rastro
            if "superset_metastore" in databases:
                print("[CHECK] 'superset_metastore' detectado. Verificando se há tabelas residuais do Rastro...")
                try:
                    with psycopg.connect(
                        **{**conn_params, "dbname": "superset_metastore"}, autocommit=True
                    ) as sconn:
                        with sconn.cursor() as scur:
                            scur.execute(
                                """
                                SELECT table_name FROM information_schema.tables
                                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                                  AND (table_name IN ('nodes', 'positions', 'device_telemetry', 'vw_ultima_posicao')
                                       OR table_schema = 'rastro');
                                """
                            )
                            stables = [row[0] for row in scur.fetchall()]
                            if stables:
                                print(f"     -> [AVISO] Tabelas do Rastro encontradas em 'superset_metastore': {stables}")
                                print("     -> [NOTA] NÃO apague automaticamente; verifique dependências antes de qualquer ação.")
                            else:
                                print("     -> [OK] Nenhuma tabela do Rastro presente em 'superset_metastore'.")
                except Exception as e:
                    print(f"     -> [INFO] Não foi possível inspecionar 'superset_metastore': {e}")

            # 3. Conferir existência de 'mqtt'
            if "mqtt" not in databases:
                print("\n[SUCESSO] O banco de dados legado 'mqtt' NÃO existe nesta instância.")
                print("Nenhuma ação de limpeza necessária.")
                return 0

            # 4. Inspecionar o interior de 'mqtt' para certificar que pertence especificamente ao meshtastic-map
            print("\n[CHECK] Inspecionando tabelas e migrations do banco legado 'mqtt'...")
            has_meshtastic_evidence = False
            inventory = None
            try:
                with psycopg.connect(
                    **{**conn_params, "dbname": "mqtt", "options": _RO_OPTIONS}, autocommit=True
                ) as mconn:
                    with mconn.cursor() as mcur:
                        mcur.execute(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
                        )
                        tables = {row[0] for row in mcur.fetchall()}
                        print(f"     -> Tabelas encontradas em 'mqtt': {sorted(tables)}")

                        # Checa tabelas exclusivas do schema de dados do meshtastic-map
                        mm_tables = tables.intersection({"map_reports", "service_envelopes", "traceroutes", "waypoints"})
                        if mm_tables:
                            has_meshtastic_evidence = True
                            print(f"     -> Confirmado: tabelas específicas do meshtastic-map presentes: {sorted(mm_tables)}")

                        if "_prisma_migrations" in tables:
                            mcur.execute(
                                """
                                SELECT migration_name FROM public._prisma_migrations
                                WHERE migration_name LIKE '%create_nodes_table%'
                                   OR migration_name LIKE '%mqtt_connection_state%'
                                   OR migration_name LIKE '%create_positions_table%';
                                """
                            )
                            mm_migrations = [row[0] for row in mcur.fetchall()]
                            if mm_migrations:
                                has_meshtastic_evidence = True
                                print(f"     -> Confirmado: migrations específicas do meshtastic-map detectadas: {mm_migrations}")

                        inventory = collect_inventory(mcur)
                        print(f"     -> Schemas não-sistêmicos: {inventory['schemas']}")
                        print(f"     -> Relações (schema, nome, relkind): {inventory['relations']} | donos: {inventory['owners']}")
                        print(
                            f"     -> Funções user: {len(inventory['functions'])} | Tipos user: {len(inventory['types'])} "
                            f"| Large objects: {inventory['large_objects']}"
                        )
                        print(f"     -> Extensões: {inventory['extensions']}")
                        if inventory["prisma_rows"] is not None:
                            print(f"     -> public._prisma_migrations contém {inventory['prisma_rows']} linha(s).")
            except Exception as e:
                print(
                    f"\n[ERRO FATAL] Não foi possível inspecionar as tabelas do banco 'mqtt': {e}",
                    file=sys.stderr,
                )
                print("Abortando por segurança: procedência de 'mqtt' não pôde ser confirmada.", file=sys.stderr)
                return 1

            problems = validate_inventory(inventory)
            print(f"\n[INFO] Evidência interna do meshtastic-map: {'SIM' if has_meshtastic_evidence else 'NÃO'}.")
            if problems:
                print("[AVISO] 'mqtt' EXISTE de novo e não é o casco Prisma vazio conhecido:")
                for x in problems:
                    print(f"   -> {x}")
            else:
                print("[AVISO] 'mqtt' EXISTE de novo (casco Prisma vazio). Este script é somente leitura; nada foi alterado.")
            return 0

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
