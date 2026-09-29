#!/usr/bin/env bash
# Rastro — Criação da role somente-leitura rastro_viewer (Fase 7, D8, plano §4).
# Executado automaticamente no primeiro boot de volume vazio, ou manualmente em volume existente:
#   docker compose -f deploy/docker-compose.yml exec -T postgres bash /docker-entrypoint-initdb.d/02-viewer-role.sh
# Senha entra como variável do psql (:'pass') e é embutida com %L do format() — sem
# interpolação direta no SQL (nenhuma injeção possível, inclusive com "'" na senha).
set -euo pipefail

PASS="${RASTRO_PG_VIEWER_PASSWORD:?defina RASTRO_PG_VIEWER_PASSWORD no deploy/.env}"

psql -v ON_ERROR_STOP=1 -v pass="$PASS" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
	SELECT format('CREATE ROLE rastro_viewer LOGIN PASSWORD %L', :'pass')
	WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rastro_viewer')
	\gexec
	SELECT format('ALTER ROLE rastro_viewer WITH PASSWORD %L', :'pass')
	WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'rastro_viewer')
	\gexec
	GRANT USAGE ON SCHEMA public TO rastro_viewer;
	GRANT SELECT ON nodes, positions, device_telemetry, vw_ultima_posicao TO rastro_viewer;
EOSQL
