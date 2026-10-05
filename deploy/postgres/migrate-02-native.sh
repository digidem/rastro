#!/usr/bin/env bash
# Rastro — aplica a migração 02-native.sql num banco de dados EXISTENTE.
#
# Aplica, de forma aditiva e idempotente:
#   - colunas novas em positions (packet_id, gateway_num, time_flag, observed_at);
#   - tabelas: raw_envelopes, packet_seen, gateway_status, node_info, chat_messages,
#     chat_outbox, virtual_gateways, boat_devices, node_power, alert_state;
#   - GRANTs mínimos para os papéis existentes (<db>_ingest, <db>_viewer, <db>_maint, <db>_backup).
#
# Conexão administrativa:
#   - RASTRO_PG_ADMIN_URL: postgresql://usuario:senha@host:5432/banco?sslmode=require
#   - Ou variáveis padrão do libpq (PGHOST, PGPORT, PGUSER, PGPASSWORD, etc.).
#
# Uso:
#   RASTRO_DB=rastro RASTRO_PG_ADMIN_URL=postgresql://postgres:senha@localhost:5432/postgres deploy/postgres/migrate-02-native.sh
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_SQL="$AQUI/init/02-native.sql"

erro() { echo "ERRO: $*" >&2; exit 1; }
aviso() { echo "AVISO: $*" >&2; }

command -v psql >/dev/null || erro "psql não encontrado no PATH"
[ -f "$NATIVE_SQL" ] || erro "arquivo de migração não encontrado: $NATIVE_SQL"

DB="${RASTRO_DB:-rastro}"
[[ "$DB" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || erro "RASTRO_DB inválido (use ^[a-z][a-z0-9_]{0,30}\$)"
ADMIN_DB="${RASTRO_ADMIN_DB:-postgres}"
OWNER="${DB}_owner"; INGEST="${DB}_ingest"; VIEWER="${DB}_viewer"; MAINT="${DB}_maint"; BACKUP="${DB}_backup"

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

psql_em() { local banco="$1"; shift; psql -X -q -At -v ON_ERROR_STOP=1 -d "$banco" "$@"; }
VARS=(-v db="$DB" -v owner="$OWNER" -v ingest="$INGEST" -v viewer="$VIEWER" -v maint="$MAINT" -v backup="$BACKUP")

# Confere existência do banco e do schema alvo
EXISTE="$(psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT count(*) FROM pg_database WHERE datname = :'db';
SQL
)"
[ "$EXISTE" = 1 ] || erro "banco '$DB' não existe (rode o bootstrap inicial antes da migração)"

# Aplica 02-native.sql e atualiza GRANTs
psql_em "$DB" "${VARS[@]}" -v native_sql="$NATIVE_SQL" <<'SQL'
-- PG16+: executa como owner do schema se ele existir
SELECT format('SET ROLE %I', :'owner')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner') \gexec

SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'db') \gexec
SELECT format('SET search_path = %I, public', :'db') \gexec

\i :native_sql

-- Concede privilégios aos papéis existentes se estiverem presentes no cluster.
-- Conjunto canônico: idêntico ao DO block da 02-native.sql e ao
-- bootstrap-existing.sh (seção 8) — manter os três idênticos.
-- ingest: grava/deduplica/consulta (o ciclo de alertas lê nodes/positions)
SELECT format('GRANT INSERT, SELECT, UPDATE ON %I.raw_envelopes, %I.packet_seen, %I.gateway_status, %I.node_info, %I.node_power, %I.chat_messages, %I.alert_state TO %I', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT SELECT ON %I.virtual_gateways, %I.boat_devices TO %I', :'db', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT SELECT, UPDATE ON %I.chat_outbox TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

-- Sequências: IDENTITY exige USAGE (nextval) nos INSERTs com id gerado
SELECT format('GRANT USAGE, SELECT ON SEQUENCE %I.raw_envelopes_id_seq, %I.chat_messages_id_seq TO %I', :'db', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

-- Suporte legado (mesmo papel grava legado + nativo): colunas que o ingest
-- lê de fato (ON CONFLICT do legado / node_activity do ciclo de alertas)
SELECT format('GRANT INSERT ON %I.positions, %I.device_telemetry, %I.nodes TO %I', :'db', :'db', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT UPDATE (node_id, friendly_name, fleet_id, last_seen, updated_at) ON %I.nodes TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT SELECT (node_num, pos_time) ON %I.positions TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT EXECUTE ON FUNCTION %I.node_activity_summary() TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

-- Corretivo (idempotente): versões anteriores desta migração concederam ao ingest SELECT em
-- coordenadas e em first/last_seen. O ingest NÃO lê coordenadas (invariante do projeto):
-- revoga e deixa só a função SECURITY DEFINER node_activity_summary().
SELECT format('REVOKE SELECT (lat_i, lon_i, lat, lon) ON %I.positions FROM %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('REVOKE SELECT (first_seen, last_seen) ON %I.nodes FROM %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT SELECT (node_num, telem_time) ON %I.device_telemetry TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

SELECT format('GRANT SELECT (node_num, node_id, friendly_name, fleet_id) ON %I.nodes TO %I', :'db', :'ingest')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingest') \gexec

-- viewer: leitura das tabelas nativas; INSERT limitado no outbox (+ sequência do IDENTITY)
SELECT format('GRANT SELECT ON %I.chat_messages, %I.chat_outbox, %I.alert_state, %I.virtual_gateways, %I.node_info, %I.node_power, %I.boat_devices TO %I', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'viewer')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'viewer') \gexec

SELECT format('GRANT INSERT (boat_id, text, created_by, expires_at) ON %I.chat_outbox TO %I', :'db', :'viewer')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'viewer') \gexec

SELECT format('GRANT USAGE, SELECT ON SEQUENCE %I.chat_outbox_id_seq TO %I', :'db', :'viewer')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'viewer') \gexec

-- viewer legado: leitura das tabelas e da view (idêntico ao bootstrap)
SELECT format('GRANT SELECT ON %I.nodes, %I.positions, %I.device_telemetry, %I.vw_ultima_posicao TO %I', :'db', :'db', :'db', :'db', :'viewer')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'viewer') \gexec

-- maint: retenção e limpeza (legado + nativo, idêntico ao bootstrap)
SELECT format('GRANT SELECT, DELETE ON %I.nodes, %I.positions, %I.device_telemetry TO %I', :'db', :'db', :'db', :'maint')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'maint') \gexec

SELECT format('GRANT SELECT, DELETE ON %I.raw_envelopes, %I.packet_seen, %I.gateway_status, %I.node_info, %I.chat_messages, %I.chat_outbox, %I.virtual_gateways, %I.boat_devices, %I.node_power, %I.alert_state TO %I', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'db', :'maint')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'maint') \gexec

-- backup: leitura completa
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', :'db', :'backup')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'backup') \gexec

SELECT format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', :'db', :'backup')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'backup') \gexec

RESET ROLE;
SQL

echo "OK: migração 02-native.sql aplicada com sucesso no banco '$DB' (schema '$DB')"
