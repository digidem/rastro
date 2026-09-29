# build context: services/   (docker compose build com context: services/)
FROM python:3.12-slim
RUN useradd --system --create-home --uid 1000 rastro
WORKDIR /app
COPY rastro_api/pyproject.toml /app/rastro_api/pyproject.toml
COPY rastro_api/api/ /app/rastro_api/api/
# no repo a raiz do pacote é namespace (sem __init__.py); no contêiner vira pacote regular
RUN touch /app/rastro_api/__init__.py && pip install --no-cache-dir /app/rastro_api
USER rastro
# --host 0.0.0.0 vale DENTRO do contêiner; a exposição no host é controlada pelo compose (D8)
CMD ["uvicorn", "rastro_api.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
