#!/usr/bin/env bash
# native_e2e.sh — Rig E2E do ingest nativo (WP-G).
#
# Sobe em Docker: broker Mosquitto (imagem nativetest, TLS + contas/ACL) e um
# PostgreSQL 16 descartável, aplica o schema (01 + 02) com os papéis/grants do
# bootstrap, gera contas fake (6 barcos + ingest + outbox + monitor) e executa
# deploy/sim/native_e2e.py, que roda os processos REAIS ingest/outbox do venv
# com PSK de teste aleatória de runtime. Remove tudo ao finalizar (trap).
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"
VENV_PY="$RAIZ/services/rastro_gateway/.venv/bin/python"

TMP="$(mktemp -d /tmp/rastro-native-e2e.XXXXXX)"
NET="rastro-e2e-net-$$-$(date +%s)"
BROKER="rastro-e2e-broker-$$-$(date +%s)"
PGCONT="rastro-e2e-pg-$$-$(date +%s)"

limpar() {
  STATUS=$?
  echo "== Limpeza: removendo contêineres, rede e temporários..."
  docker rm -f "$BROKER" >/dev/null 2>&1 || true
  docker rm -f "$PGCONT" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker run --rm --user 0:0 --entrypoint "" -v "$TMP:/clean" rastro-broker:nativetest rm -rf /clean/data /clean/public >/dev/null 2>&1 || true
  rm -rf "$TMP"
  exit "$STATUS"
}
trap limpar EXIT INT TERM

echo "== 1. Build da imagem rastro-broker:nativetest"
docker build -t rastro-broker:nativetest "$RAIZ/broker"

echo "== 2. Geração de contas fake e segredos de teste em runtime"
ACCOUNTS_FILE="$TMP/accounts.json"
PG_ADMIN_PW="$(openssl rand -hex 16)"
PG_INGEST_PW="$(openssl rand -hex 16)"
PG_VIEWER_PW="$(openssl rand -hex 16)"

"$VENV_PY" - <<PYEOF
import json
import secrets

def pw():
    return secrets.token_hex(16)

data = {
    "root": "univaja/mesh",
    "ingest": {"password": pw()},
    "outbox": {"password": pw()},
    "virtual_gateways": [
        {"boat": f"b{i}", "gateway_id": f"!f{i:07d}"} for i in range(1, 7)
    ],
    "nodes": [
        {"user": f"!a{i:07d}", "password": pw(), "boat": f"b{i}"} for i in range(1, 7)
    ],
    "monitor": {"password": pw()},
}

with open("$ACCOUNTS_FILE", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF
chmod 0644 "$ACCOUNTS_FILE"

echo "== 3. Subindo PostgreSQL 16 descartável"
docker network create "$NET" >/dev/null
docker run -d \
  --name "$PGCONT" \
  --network "$NET" \
  -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD="$PG_ADMIN_PW" \
  postgres:16 >/dev/null

echo "== 4. Aguardando PostgreSQL aceitar conexões"
for _ in $(seq 1 60); do
  if docker exec "$PGCONT" pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
docker exec "$PGCONT" pg_isready -U postgres >/dev/null
PG_PORT="$(docker port "$PGCONT" 5432/tcp | head -1 | sed 's/.*://')"
echo "   PostgreSQL em 127.0.0.1:$PG_PORT"

echo "== 5. Subindo o broker nativo (127.0.0.1:18883 -> 8883)"
mkdir -p "$TMP/public" "$TMP/data" "$TMP/logs"
chmod 0777 "$TMP/public" "$TMP/data"
docker run -d \
  --name "$BROKER" \
  --network "$NET" \
  -p 127.0.0.1:18883:8883 \
  -v "$ACCOUNTS_FILE:/mosquitto/secrets/accounts.json:ro" \
  -v "$TMP/public:/mosquitto/public" \
  -v "$TMP/data:/mosquitto/data" \
  -e RASTRO_ACCOUNTS_FILE=/mosquitto/secrets/accounts.json \
  -e RASTRO_BROKER_SANS="localhost,127.0.0.1,rastro-broker" \
  rastro-broker:nativetest >/dev/null

echo "== 6. Aguardando broker publicar a CA"
CA_FILE="$TMP/public/ca.crt"
PRONTO=0
for _ in $(seq 1 60); do
  if [ -s "$CA_FILE" ] && grep -q -- "-----BEGIN CERTIFICATE-----" "$CA_FILE" 2>/dev/null; then
    PRONTO=1
    break
  fi
  sleep 0.2
done
if [ "$PRONTO" -ne 1 ]; then
  echo "ERRO: O broker não publicou ca.crt a tempo!" >&2
  docker logs "$BROKER" >&2
  exit 1
fi
sleep 0.5

echo "== 7. Executando o rig E2E (processos reais ingest/outbox + nós fake)"
set +e
"$VENV_PY" "$AQUI/native_e2e.py" \
  --broker-host 127.0.0.1 \
  --broker-port 18883 \
  --ca "$CA_FILE" \
  --accounts "$ACCOUNTS_FILE" \
  --pg-host 127.0.0.1 \
  --pg-port "$PG_PORT" \
  --pg-admin-password "$PG_ADMIN_PW" \
  --pg-ingest-password "$PG_INGEST_PW" \
  --pg-viewer-password "$PG_VIEWER_PW" \
  --log-dir "$TMP/logs"
STATUS_E2E=$?
set -e

echo "== 8. Fim do rig E2E (exit=$STATUS_E2E); limpeza no trap"
exit "$STATUS_E2E"
