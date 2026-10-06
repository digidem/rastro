#!/usr/bin/env bash
# Constrói (e opcionalmente publica) todas as imagens communityfirst/rastro-* com UMA tag, a partir do
# HEAD commitado (git archive: ignora alterações não commitadas de outras sessões).
# Uso: scripts/rastro_release.sh 0.8.1 [--push]
# Não mexe no CapRover nem em produção. Não envia tags git. Publicar sobrescreve a tag no Docker Hub:
# nunca reutilize uma tag já em uso (ex.: 0.8.0).
set -euo pipefail
T=${1:?uso: rastro_release.sh X.Y.Z [--push]}
PUSH=${2:-}
[[ $T =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "tag inválida: $T" >&2; exit 2; }
RAIZ=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
if [ "$PUSH" = "--push" ] && docker manifest inspect "communityfirst/rastro-broker:$T" >/dev/null 2>&1; then
  echo "A tag $T já existe no Docker Hub; escolha outra." >&2; exit 3
fi
D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
git -C "$RAIZ" archive HEAD | tar -x -C "$D"
cd "$D"
docker build -q -t communityfirst/rastro-broker:$T broker >/dev/null
docker build -q -t communityfirst/rastro-ingest:$T -f services/rastro_gateway/Dockerfile services >/dev/null
docker build -q -t communityfirst/rastro-chat:$T -f services/rastro_gateway/Dockerfile.chat --build-arg BASE_TAG=$T services >/dev/null
docker build -q -t communityfirst/rastro-api:$T -f services/rastro_api/Dockerfile services >/dev/null
docker build -q -t communityfirst/rastro-web:$T web >/dev/null
docker build -q -t communityfirst/rastro-pgtools:$T-pg17 -f deploy/postgres/Dockerfile . >/dev/null
echo "construídas: broker ingest chat api web pgtools:$T-pg17 (tag $T)"
if [ "$PUSH" = "--push" ]; then
  for i in broker ingest chat api web; do docker push -q communityfirst/rastro-$i:$T; done
  docker push -q communityfirst/rastro-pgtools:$T-pg17
  echo "publicadas"
fi
