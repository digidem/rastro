#!/usr/bin/env bash
# Rastro — aplica a migração 04 (leituras ambientais) num banco de dados EXISTENTE.
#
# Cria, de forma aditiva e idempotente, SÓ a tabela environment_telemetry (BME280 do nó
# sensor), o índice BRIN e os grants dela. Sem DROP, RENAME, troca de tipo nem ALTER em
# tabela existente. Tudo numa transação: lock estourado ou erro não deixa nada pela metade.
# O CREATE TABLE é o mesmo de init/02-native.sql (tests/test_native_db.py confere).
#
# Antes de alterar qualquer coisa, grava backup do schema (pg_dump -Fc -n <db>) em
# RASTRO_BACKUP_DIR (padrão deploy/backups, ignorado pelo git). Se o backup falhar, nada é alterado.
#
# Conexão (administrativa, aponte para o banco <db>):
#   - RASTRO_PG_ADMIN_URL: postgresql://usuario:senha@host:5432/<db>?sslmode=require
#   - Ou variáveis padrão do libpq (PGHOST, PGPORT, PGUSER, PGPASSWORD, etc.).
#
# Uso:
#   RASTRO_DB=rastro RASTRO_PG_ADMIN_URL=postgresql://postgres:senha@localhost:5432/rastro deploy/postgres/migrate-04-ambiente.sh
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

erro() { echo "ERRO: $*" >&2; exit 1; }

command -v psql >/dev/null || erro "psql não encontrado no PATH"
command -v pg_dump >/dev/null || erro "pg_dump não encontrado no PATH (necessário para o backup prévio)"

DB="${RASTRO_DB:-rastro}"
[[ "$DB" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || erro "RASTRO_DB inválido (use ^[a-z][a-z0-9_]{0,30}\$)"
OWNER="${DB}_owner"; INGEST="${DB}_ingest"; VIEWER="${DB}_viewer"; MAINT="${DB}_maint"; BACKUP="${DB}_backup"

# A URL de admin é desmontada em variáveis libpq (PGHOST/PGPASSWORD...): a senha nunca vai em argv.
# O banco alvo é sempre RASTRO_DB, qualquer que seja o caminho da URL.
# O banco da URL é ignorado de propósito (ADMIN_DB/pdb só existem por causa do bloco copiado do migrate-02).
ADMIN_DB="$DB"
if [ -n "${RASTRO_PG_ADMIN_URL:-}" ]; then
  if command -v python3 >/dev/null; then
    analisado="$(python3 - <<'PY'
import os, shlex, sys
from urllib.parse import urlsplit, unquote, parse_qsl

def erro(motivo):
    print("ERRO: RASTRO_PG_ADMIN_URL inválida (%s)." % motivo, file=sys.stderr)
    sys.exit(1)

bruto = os.environ["RASTRO_PG_ADMIN_URL"].strip()
try:
    u = urlsplit(bruto)
    porta = u.port
    host = u.hostname
except ValueError:
    erro("não foi possível interpretar host/porta")
if u.scheme not in ("postgres", "postgresql"):
    erro("o esquema deve ser postgresql:// ou postgres://")
if u.fragment:
    erro("há '#' sem percent-encoding")
if not host:
    erro("host ausente")
if not u.username:
    erro("usuário ausente")
if not u.password:
    erro("senha ausente")
porta = 5432 if porta is None else porta
if not 1 <= porta <= 65535:
    erro("porta fora do intervalo")
usuario, senha = unquote(u.username), unquote(u.password)
banco = unquote(u.path.lstrip("/")) or os.environ.get("RASTRO_ADMIN_DB") or "postgres"
sslmode = ""
try:
    pares = parse_qsl(u.query, keep_blank_values=True, strict_parsing=bool(u.query))
except ValueError:
    erro("parâmetros ilegíveis")
for k, v in pares:
    if k == "sslmode":
        if v not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
            erro("sslmode inválido")
        sslmode = v
for nome, valor in (("ph", host), ("pp", str(porta)), ("pu", usuario), ("pw", senha), ("pdb", banco), ("psm", sslmode)):
    print("%s=%s" % (nome, shlex.quote(valor)))
PY
)" || erro "falha ao interpretar RASTRO_PG_ADMIN_URL"
    eval "$analisado"; analisado=""
  else
    # Fallback em bash puro se python3 não estiver no PATH
    bruto="${RASTRO_PG_ADMIN_URL}"
    [[ "$bruto" =~ ^postgres(ql)?:// ]] || erro "esquema deve ser postgresql:// ou postgres://"
    sem_proto="${bruto#*://}"
    cred="${sem_proto%%@*}"
    resto="${sem_proto#*@}"
    pu="${cred%%:*}"
    pw="${cred#*:}"
    hp="${resto%%/*}"
    ph="${hp%%:*}"
    if [[ "$hp" == *:* ]]; then pp="${hp#*:}"; else pp="5432"; fi
    caminho_query="${resto#*/}"
    pdb="${caminho_query%%\?*}"
    [ -n "$pdb" ] || pdb="${RASTRO_ADMIN_DB:-postgres}"
    psm=""
    if [[ "$caminho_query" == *\?* ]]; then
      q="${caminho_query#*\?}"
      if [[ "$q" =~ sslmode=([^&]+) ]]; then psm="${BASH_REMATCH[1]}"; fi
    fi
  fi
  export PGHOST="$ph" PGPORT="$pp" PGUSER="$pu" PGPASSWORD="$pw"
  [ -z "$psm" ] || export PGSSLMODE="$psm"
  ADMIN_DB="$pdb"
  unset RASTRO_PG_ADMIN_URL
fi
ALVO="$DB"

psql_em() { psql -X -q -At -v ON_ERROR_STOP=1 -d "$ALVO" "$@"; }
VARS=(-v db="$DB" -v owner="$OWNER" -v ingest="$INGEST" -v viewer="$VIEWER" -v maint="$MAINT" -v backup="$BACKUP")

EXISTE="$(psql_em "${VARS[@]}" <<'SQL'
SELECT to_regclass(format('%I.nodes', :'db')) IS NOT NULL;
SQL
)" || erro "não foi possível conectar ao banco '$DB'"
[ "$EXISTE" = "t" ] || erro "tabela $DB.nodes não existe (rode o bootstrap antes)"

if [ -z "${RASTRO_BACKUP_DIR:-}" ] && [ "$AQUI" = "/rastro" ]; then
  erro "dentro da imagem rastro-pgtools defina RASTRO_BACKUP_DIR com um volume montado (o padrão se perderia com o contêiner)"
fi
BACKUP_DIR="${RASTRO_BACKUP_DIR:-$AQUI/../backups}"
mkdir -p "$BACKUP_DIR"
ARQUIVO="$BACKUP_DIR/${DB}-pre-04-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump -Fc -n "$DB" -f "$ARQUIVO" -d "$ALVO" || erro "backup prévio falhou; nada foi alterado"
[ -s "$ARQUIVO" ] || erro "backup prévio vazio: $ARQUIVO; nada foi alterado"
echo "OK: backup prévio gravado em $ARQUIVO"

psql_em "${VARS[@]}" <<'SQL'
BEGIN;
SET LOCAL lock_timeout = '5s';
SELECT format('SET LOCAL ROLE %I', :'owner')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner') \gexec
SELECT format('SET LOCAL search_path = %I', :'db') \gexec

CREATE TABLE IF NOT EXISTS environment_telemetry (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_num          BIGINT NOT NULL REFERENCES nodes(node_num),
    telem_time        TIMESTAMPTZ NOT NULL,
    time_source       TEXT NOT NULL DEFAULT 'device' CHECK (time_source IN ('device','gateway')),
    temperature_c     REAL,
    relative_humidity REAL,
    pressure_hpa      REAL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT environment_dedupe UNIQUE (node_num, telem_time)
);
CREATE INDEX IF NOT EXISTS environment_telem_time_brin ON environment_telemetry USING brin (telem_time);

SELECT format('GRANT INSERT ON %I.environment_telemetry TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec
SELECT format('GRANT SELECT (node_num, telem_time) ON %I.environment_telemetry TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec
SELECT format('GRANT SELECT ON %I.environment_telemetry TO %I', :'db', :'viewer')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'viewer') \gexec
SELECT format('GRANT SELECT, DELETE ON %I.environment_telemetry TO %I', :'db', :'maint')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'maint') \gexec
SELECT format('GRANT SELECT ON %I.environment_telemetry TO %I', :'db', :'backup')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'backup') \gexec
-- pg_dump lê o estado de cada sequência: o papel backup precisa de SELECT na do IDENTITY
SELECT format('GRANT SELECT ON SEQUENCE %I.environment_telemetry_id_seq TO %I', :'db', :'backup')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'backup') \gexec

-- Dono da tabela nova = dono de device_telemetry (sem o papel owner a tabela ficaria com o admin):
-- diferença aborta a transação inteira, antes do COMMIT.
SELECT (SELECT relowner FROM pg_class WHERE oid = to_regclass(format('%I.environment_telemetry', :'db')))
     IS NOT DISTINCT FROM (SELECT relowner FROM pg_class WHERE oid = to_regclass(format('%I.device_telemetry', :'db'))) AS dono_ok \gset
\if :dono_ok
\else
\echo ERRO: dono de environment_telemetry difere do de device_telemetry (papel owner ausente?)
SELECT 1/0;
\endif
COMMIT;
SQL

# Verificação: tabela existe e tem o mesmo dono de device_telemetry
OK="$(psql_em "${VARS[@]}" <<'SQL'
SELECT (SELECT relowner FROM pg_class WHERE oid = to_regclass(format('%I.environment_telemetry', :'db')))
     = (SELECT relowner FROM pg_class WHERE oid = to_regclass(format('%I.device_telemetry', :'db')));
SQL
)"
[ "$OK" = "t" ] || erro "verificação falhou: tabela ausente ou dono diferente de device_telemetry"

echo "OK: migração 04-ambiente aplicada no banco '$DB' (schema '$DB')"
