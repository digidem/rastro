# rastro-pgtools — cliente PostgreSQL 17 + python3 + bootstrap do Rastro.
# Serve para rodar deploy/postgres/bootstrap-existing.sh DE DENTRO da rede onde o
# Postgres está (ex.: overlay do CapRover, onde o banco não tem porta pública):
#   docker run --rm --network captain-overlay-network \
#     -e PGHOST=srv-captain--postgres -e PGUSER=postgres -e PGPASSWORD=... \
#     -e RASTRO_PG_PASSWORD_INGEST=... -e RASTRO_PG_PASSWORD_VIEWER=... \
#     -e RASTRO_PG_PASSWORD_MAINT=... -e RASTRO_PG_PASSWORD_BACKUP=... \
#     communityfirst/rastro-pgtools
# Com o argumento `setup-service` roda o preparo como app do CapRover (ver setup-service.sh).
# Uma imagem por versão MAJOR do servidor (pg_restore nunca pode ser mais novo que o
# servidor): communityfirst/rastro-pgtools:<versão>-pg14 … -pg17. Use a do seu servidor.
# Também traz rastro_backup.sh (use --entrypoint).
# Build context: RAIZ do repositório (docker build -f deploy/postgres/Dockerfile .).
ARG PG_IMAGE=postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
FROM ${PG_IMAGE}
RUN apk add --no-cache python3 bash py3-psycopg
COPY deploy/postgres/entrypoint.sh deploy/postgres/setup-service.sh deploy/postgres/bootstrap-existing.sh deploy/postgres/migrate-02-native.sh deploy/postgres/scram_verifier.py scripts/rastro_backup.sh /rastro/
COPY deploy/postgres/init/*.sql /rastro/init/
RUN chmod 0555 /rastro/entrypoint.sh /rastro/setup-service.sh /rastro/bootstrap-existing.sh /rastro/migrate-02-native.sh /rastro/rastro_backup.sh \
 && chmod 0444 /rastro/scram_verifier.py /rastro/init/*.sql
# ponto de montagem do volume onde o setup publica conn.env (host/porta/sslmode, sem senha).
# Dono = uid do processo do setup (postgres, 70): se ingest/API montarem o volume novo antes,
# o Docker copia este dono e o setup continua podendo escrever (as três imagens usam o mesmo).
RUN mkdir -p /rastro-pgconn && chown 70:70 /rastro-pgconn
USER postgres
ENTRYPOINT ["bash", "/rastro/entrypoint.sh"]
