# build context: services/   (docker compose build com context: services/)
FROM python:3.12-slim
RUN useradd --system --create-home --uid 1000 rastro
WORKDIR /app
COPY rastro_api/pyproject.toml /app/rastro_api/pyproject.toml
COPY rastro_api/api/ /app/rastro_api/api/
# no repo a raiz do pacote é namespace (sem __init__.py); no contêiner vira pacote regular
RUN touch /app/rastro_api/__init__.py && pip install --no-cache-dir /app/rastro_api
# ponto de montagem do volume com conn.env (host/porta/sslmode do Postgres, sem senha) publicado
# pelo app -setup; dono uid 70 = o do processo do setup (imagem rastro-pgtools, postgres).
RUN mkdir -p /rastro-pgconn && chown 70:70 /rastro-pgconn
USER rastro
# --host 0.0.0.0 vale DENTRO do contêiner; a exposição no host é controlada pelo compose (D8)
# --no-access-log: o log de acesso traria as coordenadas de tile (área observada = sensível)
CMD ["uvicorn", "rastro_api.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
