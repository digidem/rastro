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

ALVO="${RASTRO_PG_ADMIN_URL:-$DB}"
unset RASTRO_PG_ADMIN_URL

psql_em() { psql -X -q -At -v ON_ERROR_STOP=1 -d "$ALVO" "$@"; }
VARS=(-v db="$DB" -v owner="$OWNER" -v ingest="$INGEST" -v viewer="$VIEWER" -v maint="$MAINT" -v backup="$BACKUP")

EXISTE="$(psql_em "${VARS[@]}" <<'SQL'
SELECT to_regclass(format('%I.nodes', :'db')) IS NOT NULL;
SQL
)" || erro "não foi possível conectar ao banco '$DB'"
[ "$EXISTE" = "t" ] || erro "tabela $DB.nodes não existe (rode o bootstrap antes)"

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
