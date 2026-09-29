# build context: services/   (docker compose build com context: services/)
FROM python:3.12-slim
RUN useradd --system --create-home --uid 1000 rastro
WORKDIR /app
COPY rastro_gateway/pyproject.toml /app/rastro_gateway/pyproject.toml
COPY rastro_gateway/__init__.py /app/rastro_gateway/__init__.py
COPY rastro_gateway/common /app/rastro_gateway/common
COPY rastro_gateway/bridge /app/rastro_gateway/bridge
COPY rastro_gateway/ingest /app/rastro_gateway/ingest
RUN pip install --no-cache-dir /app/rastro_gateway
# ponto de montagem do volume compartilhado da CA (CapRover), dono uid 1883 (o do
# broker): se o ingest montar o volume antes do broker, o Docker copia este dono
# para o volume novo e o broker continua podendo publicar a CA nele.
RUN mkdir -p /rastro-pki && chown 1883:1883 /rastro-pki
USER rastro
CMD ["python", "-m", "rastro_gateway.ingest"]
