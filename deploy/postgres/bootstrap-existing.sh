#!/usr/bin/env bash
# Rastro — prepara um PostgreSQL EXISTENTE (ex.: o do CapRover) para o Rastro.
#
# Cria, de forma idempotente:
#   - papéis POR INSTALAÇÃO (prefixo = nome do banco <db>):
#       <db>_owner  (sem login, dono de tudo), <db>_ingest, <db>_viewer, <db>_maint, <db>_backup
#   - banco DEDICADO <db> (dono <db>_owner) e schema dedicado <db>
#   - tabelas/view de 01-schema.sql no schema <db>, GRANTs mínimos e search_path por papel.
#
# Conexão de administração pelas variáveis padrão do libpq (PGHOST, PGPORT, PGUSER,
# PGPASSWORD ou ~/.pgpass). O admin precisa ser superusuário OU ter CREATEROLE+CREATEDB
# (Postgres gerenciado); no segundo caso ele recebe temporariamente o papel <db>_owner,
# revogado ao sair (também em falha).
#
# Senhas (obrigatórias, >= 24 caracteres), lidas do ambiente e enviadas ao servidor
# SÓ como verificador SCRAM-SHA-256 (nunca em texto puro, nunca em argv):
#   RASTRO_PG_PASSWORD_INGEST, RASTRO_PG_PASSWORD_VIEWER,
#   RASTRO_PG_PASSWORD_MAINT,  RASTRO_PG_PASSWORD_BACKUP
#
# Uso:
#   RASTRO_DB=rastro deploy/postgres/bootstrap-existing.sh
#   RASTRO_DB=rastro deploy/postgres/bootstrap-existing.sh --restore <arquivo.dump>
#
# --restore: restaura um dump do backup (pg_dump -Fc -n <db>) num banco NOVO de mesmo
# nome. Não cria schema/tabelas antes; exige a cadeia de versões
# origem <= pg_dump <= pg_restore <= servidor; restauração em transação única.
# Recuperação se algo sobrar de uma execução interrompida: rodar sem --restore
# (reaplica GRANTs/search_path e a migração aditiva de 01-schema.sql) ou
# DROP DATABASE <db> e repetir.
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_SQL="$AQUI/init/01-schema.sql"
SCRAM="$AQUI/scram_verifier.py"

erro() { echo "ERRO: $*" >&2; exit 1; }
aviso() { echo "AVISO: $*" >&2; }

MODO=normal
DUMP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --restore) [ $# -ge 2 ] || erro "--restore exige o caminho do dump"; MODO=restore; DUMP="$2"; shift 2 ;;
    -h|--help) sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) erro "argumento desconhecido: $1 (veja --help)" ;;
  esac
done

DB="${RASTRO_DB:-rastro}"
[[ "$DB" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || erro "RASTRO_DB inválido (use ^[a-z][a-z0-9_]{0,30}\$)"
ADMIN_DB="${RASTRO_ADMIN_DB:-postgres}"
OWNER="${DB}_owner"; INGEST="${DB}_ingest"; VIEWER="${DB}_viewer"; MAINT="${DB}_maint"; BACKUP="${DB}_backup"

command -v psql >/dev/null || erro "psql não encontrado no PATH"
command -v python3 >/dev/null || erro "python3 não encontrado no PATH"
[ -f "$SCHEMA_SQL" ] || erro "schema não encontrado: $SCHEMA_SQL"
if [ "$MODO" = restore ]; then
  command -v pg_restore >/dev/null || erro "pg_restore não encontrado no PATH"
  [ -f "$DUMP" ] || erro "dump não encontrado: $DUMP"
fi

verificador() {  # $1 = nome da variável de ambiente com a senha
  local nome="$1" valor
  valor="${!nome:-}"
  [ -n "$valor" ] || erro "$nome não definida"
  printf '%s' "$valor" | python3 "$SCRAM" || erro "$nome inválida (mínimo 24 caracteres)"
}
V_INGEST="$(verificador RASTRO_PG_PASSWORD_INGEST)"
V_VIEWER="$(verificador RASTRO_PG_PASSWORD_VIEWER)"
V_MAINT="$(verificador RASTRO_PG_PASSWORD_MAINT)"
V_BACKUP="$(verificador RASTRO_PG_PASSWORD_BACKUP)"

# psql sem ~/.psqlrc, parando no primeiro erro; SQL sempre pelo stdin (nada sensível em argv).
psql_em() { local banco="$1"; shift; psql -X -q -At -v ON_ERROR_STOP=1 -d "$banco" "$@"; }
VARS=(-v db="$DB" -v owner="$OWNER" -v ingest="$INGEST" -v viewer="$VIEWER" -v maint="$MAINT" -v backup="$BACKUP")

major_de() {  # "14.11 (Debian ...)" → 14 ; "17devel" → 17
  local v="${1%%[!0-9.]*}"; v="${v%%.*}"; [[ "$v" =~ ^[0-9]+$ ]] || return 1; echo "$v"
}

# --- 1. quem é o admin -------------------------------------------------------------------
read -r SERVER_NUM SUPER CRIAROLE CRIADB <<<"$(psql_em "$ADMIN_DB" <<'SQL' | tr '|' ' '
SELECT current_setting('server_version_num'), rolsuper, rolcreaterole, rolcreatedb
FROM pg_roles WHERE rolname = current_user;
SQL
)"
SERVER_MAJOR=$(( SERVER_NUM / 10000 ))
if [ "$SUPER" != t ] && { [ "$CRIAROLE" != t ] || [ "$CRIADB" != t ]; }; then
  erro "o admin precisa ser superusuário ou ter CREATEROLE e CREATEDB (use um usuário com CREATEROLE+CREATEDB, ex.: o superusuário do Postgres)"
fi

# --- 2. (restore) cadeia de versões, ANTES de criar qualquer coisa ------------------------
if [ "$MODO" = restore ]; then
  CAB="$(pg_restore -l "$DUMP" 2>/dev/null)" || erro "dump ilegível por este pg_restore ($(pg_restore --version | awk '{print $NF}')) — ferramenta mais antiga que o pg_dump que gerou o arquivo? Exigido: cadeia origem <= pg_dump <= pg_restore <= servidor"
  ORIGEM="$(sed -n 's/^; *Dumped from database version: *//p' <<<"$CAB" | head -1)"
  DUMPER="$(sed -n 's/^; *Dumped by pg_dump version: *//p' <<<"$CAB" | head -1)"
  [ -n "$ORIGEM" ] && [ -n "$DUMPER" ] || erro "cabeçalho de versão ausente no dump (não arrisco restaurar)"
  grep -Eq "SCHEMA - ${DB}( |\$)" <<<"$CAB" || erro "o dump não contém o schema '$DB' — restaure com RASTRO_DB igual ao nome do banco de origem"
  M_ORIGEM="$(major_de "$ORIGEM")" || erro "versão de origem ilegível: $ORIGEM"
  M_DUMPER="$(major_de "$DUMPER")" || erro "versão do pg_dump ilegível: $DUMPER"
  M_RESTORE="$(major_de "$(pg_restore --version | awk '{print $NF}')")" || erro "versão do pg_restore ilegível"
  if ! { [ "$M_ORIGEM" -le "$M_DUMPER" ] && [ "$M_DUMPER" -le "$M_RESTORE" ] && [ "$M_RESTORE" -le "$SERVER_MAJOR" ]; }; then
    erro "versões fora da cadeia origem($M_ORIGEM) <= pg_dump($M_DUMPER) <= pg_restore($M_RESTORE) <= servidor($SERVER_MAJOR)"
  fi
fi

# --- 3. papéis de outra instalação nunca são reaproveitados -------------------------------
ESTRANHOS="$(psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT r.rolname
FROM pg_roles r
WHERE r.rolname IN (:'owner', :'ingest', :'viewer', :'maint', :'backup')
  AND (
    EXISTS (SELECT 1 FROM pg_database d WHERE d.datname <> :'db' AND d.datdba = r.oid)
    OR EXISTS (SELECT 1 FROM pg_database d, aclexplode(d.datacl) a
               WHERE d.datname <> :'db' AND a.grantee = r.oid)
    OR EXISTS (SELECT 1 FROM pg_shdepend s
               WHERE s.refobjid = r.oid AND s.refclassid = 'pg_authid'::regclass
                 AND s.dbid <> 0
                 AND s.dbid <> COALESCE((SELECT oid FROM pg_database WHERE datname = :'db'), 0))
    OR EXISTS (SELECT 1 FROM pg_db_role_setting s
               WHERE s.setrole = r.oid AND s.setdatabase <> 0
                 AND s.setdatabase <> COALESCE((SELECT oid FROM pg_database WHERE datname = :'db'), 0))
  );
SQL
)"
[ -z "$ESTRANHOS" ] || erro "papéis já ligados a OUTRO banco (outra instalação?): $(tr '\n' ' ' <<<"$ESTRANHOS")— escolha outro RASTRO_DB"

# --- 4. papéis (idempotente; senha sempre reaplicada como verificador) --------------------
# Atributos: só um superusuário pode escrever (NO)SUPERUSER/(NO)REPLICATION; papéis
# criados por admin não-superusuário já nascem sem eles.
if [ "$SUPER" = t ]; then ATRIBUTOS="LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"; else ATRIBUTOS="LOGIN NOCREATEDB NOCREATEROLE"; fi
psql_em "$ADMIN_DB" "${VARS[@]}" -v atributos="$ATRIBUTOS" <<SQL
\\set v_ingest '$V_INGEST'
\\set v_viewer '$V_VIEWER'
\\set v_maint '$V_MAINT'
\\set v_backup '$V_BACKUP'
SELECT format('CREATE ROLE %I NOLOGIN', :'owner')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner') \\gexec
SELECT format('CREATE ROLE %I LOGIN', r) FROM unnest(ARRAY[:'ingest', :'viewer', :'maint', :'backup']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \\gexec
SELECT format('ALTER ROLE %I %s PASSWORD %L', r, :'atributos', v)
FROM (VALUES (:'ingest', :'v_ingest'), (:'viewer', :'v_viewer'), (:'maint', :'v_maint'), (:'backup', :'v_backup')) AS t(r, v) \\gexec
SQL
unset V_INGEST V_VIEWER V_MAINT V_BACKUP

# --- 5. admin não-superusuário: SET ROLE <db>_owner temporário, revogado ao sair ----------
CONCEDIDO=0
revogar() {
  if [ "$CONCEDIDO" = 1 ]; then
    psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL' || aviso "não consegui revogar o papel temporário — revogue à mão: REVOKE <db>_owner FROM <admin>"
SELECT format('REVOKE %I FROM %I', :'owner', current_user) \gexec
SQL
  fi
}
trap revogar EXIT
if [ "$SUPER" != t ]; then
  # PG16+: o admin CREATEROLE ganha, sozinho, pertença com ADMIN mas SEM SET nos papéis
  # que cria — o que importa aqui é poder fazer SET ROLE (CREATE DATABASE OWNER, SET ROLE).
  if [ "$SERVER_MAJOR" -ge 16 ]; then PRIV_PAPEL=SET; else PRIV_PAPEL=MEMBER; fi
  MEMBRO="$(psql_em "$ADMIN_DB" "${VARS[@]}" -v priv="$PRIV_PAPEL" <<'SQL'
SELECT pg_has_role(current_user, :'owner', :'priv');
SQL
)"
  if [ "$MEMBRO" != t ]; then
    if [ "$SERVER_MAJOR" -ge 16 ]; then COM_SET="WITH SET TRUE"; else COM_SET=""; fi
    CONCEDIDO=1
    psql_em "$ADMIN_DB" "${VARS[@]}" -v com_set="$COM_SET" <<'SQL'
SELECT format('GRANT %I TO %I %s', :'owner', current_user, :'com_set') \gexec
SQL
  fi
fi

# --- 6. banco dedicado ---------------------------------------------------------------------
EXISTE="$(psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT count(*) FROM pg_database WHERE datname = :'db';
SQL
)"
if [ "$EXISTE" = 0 ]; then
  psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'owner') \gexec
SQL
else
  DONO="$(psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'db';
SQL
)"
  [ "$DONO" = "$OWNER" ] || erro "o banco '$DB' já existe e pertence a '$DONO' (esperado '$OWNER') — não é um banco do Rastro"
  # objetos estranhos: de usuário, fora dos schemas de sistema, que não sejam do owner
  # (ignora o namespace public em si e membros de extensão, ex. plpgsql)
  N_ESTRANHOS="$(psql_em "$DB" "${VARS[@]}" <<'SQL'
WITH dono AS (SELECT oid FROM pg_roles WHERE rolname = :'owner'),
sis AS (SELECT oid FROM pg_namespace
        WHERE nspname IN ('pg_catalog', 'information_schema')
           OR nspname LIKE 'pg\_toast%' OR nspname LIKE 'pg\_temp\_%'),
ext AS (SELECT classid, objid FROM pg_depend WHERE deptype = 'e')
SELECT
  (SELECT count(*) FROM pg_class c
   WHERE c.relnamespace NOT IN (SELECT oid FROM sis)
     AND c.relowner <> (SELECT oid FROM dono)
     AND NOT EXISTS (SELECT 1 FROM ext WHERE classid = 'pg_class'::regclass AND objid = c.oid))
+ (SELECT count(*) FROM pg_namespace n
   WHERE n.oid NOT IN (SELECT oid FROM sis) AND n.nspname <> 'public'
     AND n.nspowner <> (SELECT oid FROM dono)
     AND NOT EXISTS (SELECT 1 FROM ext WHERE classid = 'pg_namespace'::regclass AND objid = n.oid))
+ (SELECT count(*) FROM pg_proc p
   WHERE p.pronamespace NOT IN (SELECT oid FROM sis)
     AND p.proowner <> (SELECT oid FROM dono)
     AND NOT EXISTS (SELECT 1 FROM ext WHERE classid = 'pg_proc'::regclass AND objid = p.oid));
SQL
)"
  [ "$N_ESTRANHOS" = 0 ] || erro "o banco '$DB' tem $N_ESTRANHOS objeto(s) que não pertencem a '$OWNER' — recuso mexer"
fi

# --- 7. (restore) schema não pode existir; restaura em transação única --------------------
if [ "$MODO" = restore ]; then
  TEM_SCHEMA="$(psql_em "$DB" "${VARS[@]}" <<'SQL'
SELECT count(*) FROM pg_namespace WHERE nspname = :'db';
SQL
)"
  [ "$TEM_SCHEMA" = 0 ] || erro "o schema '$DB' já existe no banco — restauração só em banco vazio (DROP DATABASE $DB e repita)"
  pg_restore --single-transaction --exit-on-error --no-privileges --no-owner \
    --role="$OWNER" -d "$DB" "$DUMP" || erro "pg_restore falhou (nada foi aplicado: transação única)"
  echo "OK: dump restaurado no schema $DB"
fi

# --- 8. schema, migração aditiva e GRANTs (exatos: revoga tudo e concede o mínimo) --------
psql_em "$DB" "${VARS[@]}" -v schema_sql="$SCHEMA_SQL" -v modo="$MODO" <<'SQL'
SELECT format('SET ROLE %I', :'owner') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I, %I, %I, %I', :'db', :'ingest', :'viewer', :'maint', :'backup') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'db') \gexec
SELECT format('SET search_path = %I', :'db') \gexec
SELECT (:'modo' = 'normal') AS aplicar_schema \gset
\if :aplicar_schema
\i :schema_sql
\endif
SELECT format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I, %I, %I, %I', :'db', :'ingest', :'viewer', :'maint', :'backup') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM PUBLIC, %I, %I, %I, %I', :'db', :'ingest', :'viewer', :'maint', :'backup') \gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC, %I, %I, %I, %I', :'db', :'ingest', :'viewer', :'maint', :'backup') \gexec
-- ingest: só insere histórico; SELECT apenas nas colunas do alvo de ON CONFLICT (não lê rastros)
SELECT format('GRANT INSERT ON %I.positions, %I.device_telemetry TO %I', :'db', :'db', :'ingest') \gexec
SELECT format('GRANT SELECT (node_num, pos_time) ON %I.positions TO %I', :'db', :'ingest') \gexec
SELECT format('GRANT SELECT (node_num, telem_time) ON %I.device_telemetry TO %I', :'db', :'ingest') \gexec
SELECT format('GRANT INSERT ON %I.nodes TO %I', :'db', :'ingest') \gexec
SELECT format('GRANT UPDATE (node_id, friendly_name, fleet_id, last_seen, updated_at) ON %I.nodes TO %I', :'db', :'ingest') \gexec
SELECT format('GRANT SELECT (node_num, node_id, friendly_name, fleet_id) ON %I.nodes TO %I', :'db', :'ingest') \gexec
-- viewer: leitura
SELECT format('GRANT SELECT ON %I.nodes, %I.positions, %I.device_telemetry, %I.vw_ultima_posicao TO %I', :'db', :'db', :'db', :'db', :'viewer') \gexec
-- maint: retenção e limpeza de teste
SELECT format('GRANT SELECT, DELETE ON %I.nodes, %I.positions, %I.device_telemetry TO %I', :'db', :'db', :'db', :'maint') \gexec
-- backup: pg_dump do schema (tabelas, view e sequências de identidade)
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', :'db', :'backup') \gexec
SELECT format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', :'db', :'backup') \gexec
-- PG15+: public pertence a pg_database_owner (= owner): fecha CREATE para PUBLIC
DO $$ BEGIN
  REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'public não pertence ao owner (PG <= 14); tentativa como admin a seguir';
END $$;
RESET ROLE;
SQL

# search_path por papel, com escopo explícito neste banco
psql_em "$ADMIN_DB" "${VARS[@]}" <<'SQL'
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I', r, :'db', :'db')
FROM unnest(ARRAY[:'owner', :'ingest', :'viewer', :'maint', :'backup']) AS r \gexec
SQL

# PG <= 14: public ainda aceita CREATE de PUBLIC; tenta fechar (admin gerenciado pode não
# poder — o REVOKE de quem não é dono só dá WARNING e sai 0, então CONFERE depois)
psql_em "$DB" <<'SQL' >/dev/null 2>&1 || true
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
PUBLICO_CRIA="$(psql_em "$DB" "${VARS[@]}" <<'SQL'
SELECT has_schema_privilege(:'ingest', 'public', 'CREATE');
SQL
)"
if [ "$PUBLICO_CRIA" = t ]; then
  aviso "CREATE em public continua liberado (Postgres gerenciado <= 14?) — papéis de login podem criar objetos em public, sem acesso aos dados do schema $DB"
fi

echo "OK: banco '$DB' pronto (papéis ${DB}_{owner,ingest,viewer,maint,backup}; schema '$DB'; modo $MODO)"
