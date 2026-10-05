#!/usr/bin/env bash
# native-rig.sh — Rig de testes do broker nativo (WP-C).
#
# Constrói a imagem do broker com a tag "nativetest", inicializa o contêiner
# em rede docker privada mapeando 127.0.0.1:18883->8883, gera credenciais fake
# exclusivamente em runtime, extrai a CA e executa deploy/sim/native_rig_test.py.
# Remove contêineres e redes ao finalizar (trap).
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"

TMP="$(mktemp -d /tmp/rastro-native-rig.XXXXXX)"
NET="rastro-native-net-$$-$(date +%s)"
CONT="rastro-native-broker-$$-$(date +%s)"

limpar() {
  STATUS=$?
  echo "== Limpeza: removendo contêiner, rede e arquivos temporários..."
  docker rm -f "$CONT" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker run --rm --user 0:0 --entrypoint "" -v "$TMP:/clean" rastro-broker:nativetest rm -rf /clean/data /clean/public >/dev/null 2>&1 || true
  rm -rf "$TMP"
  exit "$STATUS"
}
trap limpar EXIT INT TERM

echo "== 1. Build da imagem rastro-broker:nativetest"
docker build -t rastro-broker:nativetest "$RAIZ/broker"

echo "== 2. Geração de credenciais fake e accounts.json em runtime"
ACCOUNTS_FILE="$TMP/accounts.json"
"$RAIZ/services/rastro_gateway/.venv/bin/python" - <<PYEOF
import json
import secrets

def pw():
    return secrets.token_hex(16)

data = {
    "root": "univaja/mesh",
    "ingest": {"password": pw()},
    "outbox": {"password": pw()},
    "gateway": {"password": pw(), "prefix": "rastro"},
    "virtual_gateways": [
        {"boat": f"b{i}", "gateway_id": f"!f000000{i}"} for i in range(1, 7)
    ],
    "nodes": [
        {"user": f"!a000000{i}", "password": pw(), "boat": f"b{i}"} for i in range(1, 7)
    ]
}

with open("$ACCOUNTS_FILE", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF

chmod 0644 "$ACCOUNTS_FILE"

echo "== 3. Criando rede privada docker e diretórios montados"
docker network create "$NET" >/dev/null
mkdir -p "$TMP/public" "$TMP/data"
chmod 0777 "$TMP/public" "$TMP/data"

echo "== 4. Subindo o broker nativo em $CONT (127.0.0.1:18883 -> 8883)"
docker run -d \
  --name "$CONT" \
  --network "$NET" \
  -p 127.0.0.1:18883:8883 \
  -v "$ACCOUNTS_FILE:/mosquitto/secrets/accounts.json:ro" \
  -v "$TMP/public:/mosquitto/public" \
  -v "$TMP/data:/mosquitto/data" \
  -e RASTRO_ACCOUNTS_FILE=/mosquitto/secrets/accounts.json \
  -e RASTRO_BROKER_SANS="localhost,127.0.0.1,rastro-broker" \
  rastro-broker:nativetest >/dev/null

echo "== 5. Aguardando inicialização do broker e publicação da CA"
CA_FILE="$TMP/public/ca.crt"
PRONTO=0
for _ in $(seq 1 30); do
  if [ -s "$CA_FILE" ] && grep -q -- "-----BEGIN CERTIFICATE-----" "$CA_FILE" 2>/dev/null; then
    PRONTO=1
    break
  fi
  sleep 0.2
done

if [ "$PRONTO" -ne 1 ]; then
  echo "ERRO: O broker não publicou ca.crt a tempo!" >&2
  docker logs "$CONT" >&2
  exit 1
fi

# Aguarda socket 18883 responder
sleep 0.5

echo "== 6. Executando bateria de testes nativos (native_rig_test.py)"
"$RAIZ/services/rastro_gateway/.venv/bin/python" "$AQUI/native_rig_test.py" \
  --host 127.0.0.1 \
  --port 18883 \
  --ca "$CA_FILE" \
  --accounts "$ACCOUNTS_FILE"

echo "== 7. Testes do rig concluídos com sucesso!"
