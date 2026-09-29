#!/usr/bin/env bash
# F5 — matriz do bootstrap/restore do PostgreSQL (deploy/postgres/bootstrap-existing.sh).
# Casos: superusuário PG17 (idempotência, 2ª instalação, papel "estrangeiro" recusado);
# admin não-superusuário PG17 e PG14 (papel temporário revogado); backup -n pelo papel
# backup → restore em cluster novo por admin não-superusuário (dados, índices, ACL);
# dump de schema antigo; cadeia de versões recusada; recuperação pós-commit.
# Uso: deploy/sim/pg-matrix.sh   (precisa de docker; constrói rastro-pgtools:{17,14}-matrix)
set -uo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"
NET=rastro-pgmatrix
FALHAS=0
ok()   { echo "PASS $1"; }
fail() { echo "FAIL $1"; FALHAS=$((FALHAS + 1)); }
check() { if eval "$2"; then ok "$1"; else fail "$1"; fi; }

docker build -q -t rastro-pgtools:17-matrix "$RAIZ/deploy/postgres" >/dev/null
sed 's#^FROM postgres:17-alpine@sha256:[0-9a-f]*#FROM postgres:14-alpine#' "$RAIZ/deploy/postgres/Dockerfile" > "$AQUI/.Dockerfile.pg14"
docker build -q -t rastro-pgtools:14-matrix -f "$AQUI/.Dockerfile.pg14" "$RAIZ/deploy/postgres" >/dev/null
rm -f "$AQUI/.Dockerfile.pg14"
docker network create "$NET" >/dev/null 2>&1 || true
TMP="$(mktemp -d)"; chmod 0777 "$TMP"
SERVIDORES=()
limpar() { for s in "${SERVIDORES[@]}"; do docker rm -f "$s" >/dev/null 2>&1; done; docker network rm "$NET" >/dev/null 2>&1; rm -rf "$TMP"; }
trap limpar EXIT

PW="senha-de-teste-0123456789abcdef"
servidor() {  # nome imagem
  docker run -d --name "$1" --network "$NET" -e POSTGRES_PASSWORD=adminpw "$2" >/dev/null
  SERVIDORES+=("$1")
  for _ in $(seq 1 40); do docker exec "$1" pg_isready -U postgres -q 2>/dev/null && break; sleep 1; done; sleep 1
}
# pgtools <versão> <host> <user> <senha> <db> [args...]
pgtools() {
  local v="$1" h="$2" u="$3" p="$4" db="$5"; shift 5
  docker run --rm --network "$NET" -v "$TMP:/b" -e PGHOST="$h" -e PGUSER="$u" -e PGPASSWORD="$p" \
    -e RASTRO_DB="$db" -e RASTRO_PG_PASSWORD_INGEST="$PW" -e RASTRO_PG_PASSWORD_VIEWER="$PW" \
    -e RASTRO_PG_PASSWORD_MAINT="$PW" -e RASTRO_PG_PASSWORD_BACKUP="$PW" \
    "rastro-pgtools:$v-matrix" "$@" 2>&1
}
# sql <host> <user> <senha> <db> <sql>  (psql -At)
sql() {
  docker run --rm --network "$NET" -e PGPASSWORD="$3" --entrypoint psql rastro-pgtools:17-matrix \
    -X -At -h "$1" -U "$2" -d "$4" -c "$5" 2>&1
}
admin_nao_super() {  # host → cria papel "gerente" CREATEROLE CREATEDB
  sql "$1" postgres adminpw postgres "CREATE ROLE gerente LOGIN CREATEROLE CREATEDB PASSWORD 'gerentepw'" >/dev/null
}
INGESTA="INSERT INTO nodes (node_num,node_id,last_seen,updated_at) VALUES (1,'!00000001',now(),now()) ON CONFLICT (node_num) DO UPDATE SET last_seen=now(), updated_at=now(); INSERT INTO positions (node_num,pos_time,lat_i,lon_i) VALUES (1,'2026-01-01',101234000,-301234000),(1,'2026-01-02',101235000,-301235000) ON CONFLICT (node_num,pos_time) DO NOTHING; INSERT INTO device_telemetry (node_num,telem_time,battery_level) VALUES (1,'2026-01-01',77) ON CONFLICT (node_num,telem_time) DO NOTHING;"
SOMA="SELECT md5(string_agg(x, '|')) FROM (SELECT node_num::text||node_id AS x FROM nodes ORDER BY node_num) a UNION ALL SELECT md5(string_agg(id::text||node_num||pos_time||lat_i||lon_i, '|' ORDER BY id)) FROM positions UNION ALL SELECT md5(string_agg(id::text||node_num||telem_time||coalesce(battery_level::text,''), '|' ORDER BY id)) FROM device_telemetry"
ACL="SELECT string_agg(c.relname||':'||coalesce(array_to_string(c.relacl,','),'')||':'||coalesce((SELECT string_agg(a.attname||'='||array_to_string(a.attacl,','),';' ORDER BY a.attname) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL),''), ' ' ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='rastro'"

echo "== A. PG17, superusuário"
servidor pgA postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
check "bootstrap" '[[ "$(pgtools 17 pgA postgres adminpw rastro)" == *"OK: banco"* ]]'
check "re-execução idempotente" '[[ "$(pgtools 17 pgA postgres adminpw rastro)" == *"OK: banco"* ]]'
check "2ª instalação (rastro2) no mesmo cluster" '[[ "$(pgtools 17 pgA postgres adminpw rastro2)" == *"OK: banco"* ]]'
check "rastro_ingest não conecta em rastro2" '[[ "$(sql pgA rastro_ingest "$PW" rastro2 "select 1")" == *"permission denied"* ]]'
sql pgA postgres adminpw postgres "CREATE DATABASE outro" >/dev/null
sql pgA postgres adminpw postgres "GRANT CONNECT ON DATABASE outro TO rastro_ingest" >/dev/null
check "papel com vínculo explícito a outro banco → recusado" '[[ "$(pgtools 17 pgA postgres adminpw rastro)" == *"ligados a OUTRO banco"* ]]'
sql pgA postgres adminpw postgres "REVOKE CONNECT ON DATABASE outro FROM rastro_ingest" >/dev/null
check "PUBLIC herdado não conta (re-execução passa)" '[[ "$(pgtools 17 pgA postgres adminpw rastro)" == *"OK: banco"* ]]'
sql pgA rastro_ingest "$PW" rastro "$INGESTA" >/dev/null
check "ingest grava pelo papel" '[ "$(sql pgA rastro_viewer "$PW" rastro "select count(*) from positions")" = 2 ]'
ACL_LIMPA="$(sql pgA postgres adminpw rastro "$ACL")"
SOMA_A="$(sql pgA postgres adminpw rastro "$SOMA")"
check "backup: pg_dump -n rastro pelo papel rastro_backup" \
  'docker run --rm --network "$NET" -v "$TMP:/b" -e PGPASSWORD="$PW" --entrypoint pg_dump rastro-pgtools:17-matrix -h pgA -U rastro_backup -d rastro -n rastro -Fc -f /b/a.dump'

echo "== B. PG17, admin não-superusuário: bootstrap e restore"
servidor pgB postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
admin_nao_super pgB
check "restore por admin não-superusuário" '[[ "$(pgtools 17 pgB gerente gerentepw rastro --restore /b/a.dump)" == *"OK: banco"* ]]'
check "dados idênticos (checksum)" '[ "$(sql pgB postgres adminpw rastro "$SOMA")" = "$SOMA_A" ]'
check "ACL idêntica à de um bootstrap limpo" '[ "$(sql pgB postgres adminpw rastro "$ACL")" = "$ACL_LIMPA" ]'
# PG16+: a pertença automática (ADMIN, sem SET) do CREATEROLE fica — por desenho; o que o
# script concede e depois revoga é o SET.
check "papel temporário revogado do admin (sem SET)" '[ "$(sql pgB postgres adminpw postgres "select pg_has_role('"'"'gerente'"'"','"'"'rastro_owner'"'"','"'"'SET'"'"')")" = f ]'
check "restore de novo no mesmo banco → recusado (schema existe)" '[[ "$(pgtools 17 pgB gerente gerentepw rastro --restore /b/a.dump)" == *"já existe"* ]]'
check "ingest grava após restore (identity/sequência ok)" '[ -z "$(sql pgB rastro_ingest "$PW" rastro "INSERT INTO positions (node_num,pos_time,lat_i,lon_i) VALUES (1,'"'"'2026-02-01'"'"',101234000,-301234000)" | grep -i error)" ]'

echo "== C. PG14, admin não-superusuário, ferramentas 14"
servidor pgC postgres:14-alpine
admin_nao_super pgC
check "bootstrap PG14 não-superusuário" '[[ "$(pgtools 14 pgC gerente gerentepw rastro)" == *"OK: banco"* ]]'
check "tabelas no schema dedicado (PG14)" '[ "$(sql pgC postgres adminpw rastro "select count(*) from pg_tables where schemaname='"'"'rastro'"'"' and tableowner='"'"'rastro_owner'"'"'")" = 3 ]'
check "cadeia de versões: dump 17 → pg_restore 14 recusado" '[[ "$(pgtools 14 pgC gerente gerentepw rastro9 --restore /b/a.dump)" == *"cadeia origem"* ]]'
check "nada criado depois da recusa" '[ -z "$(sql pgC postgres adminpw postgres "select 1 from pg_database where datname='"'"'rastro9'"'"'")" ]'

echo "== D. dump de schema antigo (sem um índice) e recuperação pós-commit"
servidor pgD postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
pgtools 17 pgD postgres adminpw rastro >/dev/null
sql pgD postgres adminpw rastro "DROP INDEX rastro.positions_received_at_brin" >/dev/null
docker run --rm --network "$NET" -v "$TMP:/b" -e PGPASSWORD="$PW" --entrypoint pg_dump rastro-pgtools:17-matrix -h pgD -U rastro_backup -d rastro -n rastro -Fc -f /b/antigo.dump
servidor pgE postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
check "restore de schema antigo" '[[ "$(pgtools 17 pgE postgres adminpw rastro --restore /b/antigo.dump)" == *"OK: banco"* ]]'
check "índice ausente continua ausente (sem migração silenciosa)" '[ -z "$(sql pgE postgres adminpw rastro "select 1 from pg_indexes where indexname='"'"'positions_received_at_brin'"'"'")" ]'
check "modo normal depois = migração aditiva (índice criado)" '[[ "$(pgtools 17 pgE postgres adminpw rastro)" == *"OK: banco"* ]] && [ -n "$(sql pgE postgres adminpw rastro "select 1 from pg_indexes where indexname='"'"'positions_received_at_brin'"'"'")" ]'
# recuperação pós-commit: pg_restore aplicado "à mão" sem os GRANTs, depois bootstrap normal
servidor pgF postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
pgtools 17 pgF postgres adminpw rastro >/dev/null
sql pgF postgres adminpw postgres "DROP DATABASE rastro" >/dev/null
sql pgF postgres adminpw postgres "CREATE DATABASE rastro OWNER rastro_owner" >/dev/null
docker run --rm --network "$NET" -v "$TMP:/b" -e PGPASSWORD=adminpw --entrypoint pg_restore rastro-pgtools:17-matrix -h pgF -U postgres -d rastro --single-transaction --no-privileges --no-owner --role=rastro_owner /b/a.dump
check "pós-commit sem GRANTs: viewer negado" '[[ "$(sql pgF rastro_viewer "$PW" rastro "select count(*) from rastro.positions")" == *"permission denied"* ]]'
check "recuperação: bootstrap normal reaplica GRANTs" '[[ "$(pgtools 17 pgF postgres adminpw rastro)" == *"OK: banco"* ]] && [ "$(sql pgF rastro_viewer "$PW" rastro "select count(*) from positions")" = 2 ]'

echo "--- $FALHAS falha(s)"
[ "$FALHAS" = 0 ]
