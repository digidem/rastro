# rastro-pgtools — cliente PostgreSQL 17 + python3 + bootstrap do Rastro.
# Serve para rodar deploy/postgres/bootstrap-existing.sh DE DENTRO da rede onde o
# Postgres está (ex.: overlay do CapRover, onde o banco não tem porta pública):
#   docker run --rm --network captain-overlay-network \
#     -e PGHOST=srv-captain--postgres -e PGUSER=postgres -e PGPASSWORD=... \
#     -e RASTRO_PG_PASSWORD_INGEST=... -e RASTRO_PG_PASSWORD_VIEWER=... \
#     -e RASTRO_PG_PASSWORD_MAINT=... -e RASTRO_PG_PASSWORD_BACKUP=... \
#     ghcr.io/digidem/rastro-pgtools
# Restaurar em servidor PG < 17: use ferramentas da MESMA versão do servidor (o script
# recusa pg_restore mais novo que o servidor).
# Build context: deploy/postgres/
FROM postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
RUN apk add --no-cache python3 bash
COPY bootstrap-existing.sh scram_verifier.py /rastro/
COPY init/01-schema.sql /rastro/init/01-schema.sql
RUN chmod 0555 /rastro/bootstrap-existing.sh && chmod 0444 /rastro/scram_verifier.py /rastro/init/01-schema.sql
USER postgres
ENTRYPOINT ["bash", "/rastro/bootstrap-existing.sh"]
