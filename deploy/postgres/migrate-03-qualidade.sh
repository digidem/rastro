#!/usr/bin/env bash
# Rastro — aplica a migração 03 (qualidade do fix) num banco de dados EXISTENTE.
#
# Adiciona, de forma aditiva e idempotente, colunas ANULÁVEIS em positions:
#   pdop REAL, hdop REAL, ground_speed_ms REAL, ground_track_deg REAL, precision_bits SMALLINT.
# Sem DROP, RENAME, troca de tipo nem índice. Mesmo bloco do 01-schema.sql.
#
# Antes de alterar qualquer coisa, grava backup do schema (pg_dump -Fc -n <db>) em
# RASTRO_BACKUP_DIR (padrão deploy/backups, ignorado pelo git). Se o backup falhar, nada é alterado.
#
# Conexão (administrativa, aponte para o banco <db>):
#   - RASTRO_PG_ADMIN_URL: postgresql://usuario:senha@host:5432/<db>?sslmode=require
#   - Ou variáveis padrão do libpq (PGHOST, PGPORT, PGUSER, PGPASSWORD, etc.).
#
# Uso:
#   RASTRO_DB=rastro RASTRO_PG_ADMIN_URL=postgresql://postgres:senha@localhost:5432/rastro deploy/postgres/migrate-03-qualidade.sh
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

erro() { echo "ERRO: $*" >&2; exit 1; }

command -v psql >/dev/null || erro "psql não encontrado no PATH"
command -v pg_dump >/dev/null || erro "pg_dump não encontrado no PATH (necessário para o backup prévio)"

DB="${RASTRO_DB:-rastro}"
[[ "$DB" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || erro "RASTRO_DB inválido (use ^[a-z][a-z0-9_]{0,30}\$)"
OWNER="${DB}_owner"

ALVO="${RASTRO_PG_ADMIN_URL:-$DB}"
unset RASTRO_PG_ADMIN_URL

psql_em() { psql -X -q -At -v ON_ERROR_STOP=1 -d "$ALVO" "$@"; }

# Confere o banco e a tabela antes de qualquer escrita
EXISTE="$(psql_em -v db="$DB" <<'SQL'
SELECT to_regclass(format('%I.positions', :'db')) IS NOT NULL;
SQL
)" || erro "não foi possível conectar ao banco '$DB'"
[ "$EXISTE" = "t" ] || erro "tabela $DB.positions não existe (rode o bootstrap e o 01-schema.sql antes)"

# Backup prévio do schema: falha aqui aborta antes de qualquer ALTER
BACKUP_DIR="${RASTRO_BACKUP_DIR:-$AQUI/../backups}"
mkdir -p "$BACKUP_DIR"
ARQUIVO="$BACKUP_DIR/${DB}-pre-03-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump -Fc -n "$DB" -f "$ARQUIVO" -d "$ALVO" || erro "backup prévio falhou; nada foi alterado"
[ -s "$ARQUIVO" ] || erro "backup prévio vazio: $ARQUIVO; nada foi alterado"
echo "OK: backup prévio gravado em $ARQUIVO"

# Aplica a migração (ALTER como dono do schema, se o papel existir)
psql_em -v db="$DB" -v owner="$OWNER" <<'SQL'
-- ALTER pega ACCESS EXCLUSIVE: não fica na fila atrás de query longa travando
-- ingest e API. Estourou? Rode de novo (idempotente).
SET lock_timeout = '5s';
SELECT format('SET ROLE %I', :'owner')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner') \gexec

SELECT format('ALTER TABLE %I.positions ADD COLUMN IF NOT EXISTS pdop REAL', :'db') \gexec
SELECT format('ALTER TABLE %I.positions ADD COLUMN IF NOT EXISTS hdop REAL', :'db') \gexec
SELECT format('ALTER TABLE %I.positions ADD COLUMN IF NOT EXISTS ground_speed_ms REAL', :'db') \gexec
SELECT format('ALTER TABLE %I.positions ADD COLUMN IF NOT EXISTS ground_track_deg REAL', :'db') \gexec
SELECT format('ALTER TABLE %I.positions ADD COLUMN IF NOT EXISTS precision_bits SMALLINT', :'db') \gexec

RESET ROLE;
SQL

# Verificação: as cinco colunas existem
CONTAGEM="$(psql_em -v db="$DB" <<'SQL'
SELECT count(*) FROM information_schema.columns
WHERE table_schema = :'db' AND table_name = 'positions'
  AND column_name IN ('pdop', 'hdop', 'ground_speed_ms', 'ground_track_deg', 'precision_bits');
SQL
)"
[ "$CONTAGEM" = 5 ] || erro "verificação falhou: $CONTAGEM de 5 colunas de qualidade presentes"

echo "OK: migração 03-qualidade aplicada no banco '$DB' (schema '$DB')"
