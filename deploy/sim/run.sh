#!/usr/bin/env bash
# F5 — simula a instalação do template CapRover do Rastro, localmente, e roda a sonda.
#
# Uso: deploy/sim/run.sh <caminho/do/rastro.yml> [--keep] [--no-build]
#   rastro.yml: o template da loja (digidem/caprover-one-click-apps, public/v4/apps/).
#
# Passos: constrói as 5 imagens com os nomes do GHCR e a tag "simtest"; gera certificados
# com o SAN do nome interno do CapRover; senhas aleatórias; gera o compose A PARTIR do
# template (rastro_caprover_sim.py); sobe em rede interna (sem portas no host); espera o
# bootstrap; roda a sonda (deploy/sim/probe.py) e confere o conteúdo das imagens.
# Nada fica para trás (down -v), a menos que --keep.
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"
TEMPLATE="${1:?uso: run.sh <rastro.yml> [--keep] [--no-build]}"; shift
KEEP=0; BUILD=1
for a in "$@"; do case "$a" in --keep) KEEP=1 ;; --no-build) BUILD=0 ;; *) echo "arg? $a" >&2; exit 2 ;; esac; done

export SIM_TAG=simtest SIM_APP=sim SIM_DB="${SIM_DB:-rastro}" SIM_PROBE="$AQUI/probe.py"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/rastro-sim.XXXXXX")"
export SIM_CERTS="$TMP/certs"
PROJ="rastro-sim-$SIM_APP"
limpar() {
  if [ "$KEEP" = 0 ]; then
    docker compose -p "$PROJ" -f "$TMP/compose.yml" -f "$AQUI/compose.extra.yml" --profile probe down -v >/dev/null 2>&1 || true
    rm -rf "$TMP"
  else
    echo "mantido: $TMP (projeto $PROJ)"
  fi
}
trap limpar EXIT

if [ "$BUILD" = 1 ]; then
  echo "== build das imagens"
  docker build -q -t ghcr.io/digidem/rastro-broker:$SIM_TAG "$RAIZ/broker" >/dev/null
  docker build -q -t ghcr.io/digidem/rastro-ingest:$SIM_TAG -f "$RAIZ/services/rastro_gateway/Dockerfile" "$RAIZ/services" >/dev/null
  docker build -q -t ghcr.io/digidem/rastro-api:$SIM_TAG -f "$RAIZ/services/rastro_api/Dockerfile" "$RAIZ/services" >/dev/null
  docker build -q -t ghcr.io/digidem/rastro-web:$SIM_TAG "$RAIZ/web" >/dev/null
  docker build -q -t ghcr.io/digidem/rastro-pgtools:$SIM_TAG "$RAIZ/deploy/postgres" >/dev/null
fi

echo "== certificados (SAN do nome interno do CapRover)"
"$RAIZ/scripts/rastro_gen_certs.sh" --out-dir "$TMP/ca" --cert-dir "$SIM_CERTS" \
  --san "DNS:srv-captain--$SIM_APP-broker" --san "DNS:$SIM_APP-broker" >/dev/null
chmod 0644 "$SIM_CERTS/ca.crt"

senha() { openssl rand -hex 16; }
export SIM_PG_ADMIN_PW="$(senha)" SIM_PW_INGEST="$(senha)" SIM_PW_VIEWER="$(senha)" \
       SIM_PW_MAINT="$(senha)" SIM_PW_BACKUP="$(senha)" SIM_PW_GATEWAY="$(senha)" \
       SIM_PW_MQTT_INGEST="$(senha)" SIM_API_TOKEN="$(openssl rand -hex 24)"
python3 - "$TMP/values.json" <<PY
import json, sys, base64, os
c = os.environ["SIM_CERTS"]
b64 = lambda n: base64.b64encode(open(f"{c}/{n}", "rb").read()).decode()
json.dump({
  "\$\$cap_tag": os.environ["SIM_TAG"],
  "\$\$cap_mqtt_pw_gateway": os.environ["SIM_PW_GATEWAY"],
  "\$\$cap_mqtt_pw_ingest": os.environ["SIM_PW_MQTT_INGEST"],
  "\$\$cap_tls_ca_b64": b64("ca.crt"), "\$\$cap_tls_crt_b64": b64("server.crt"),
  "\$\$cap_tls_key_b64": b64("server.key"),
  "\$\$cap_pg_host": "srv-captain--postgres", "\$\$cap_pg_database": os.environ["SIM_DB"],
  "\$\$cap_pg_user_ingest": os.environ["SIM_DB"] + "_ingest",
  "\$\$cap_pg_user_viewer": os.environ["SIM_DB"] + "_viewer",
  "\$\$cap_pg_pw_ingest": os.environ["SIM_PW_INGEST"],
  "\$\$cap_pg_pw_viewer": os.environ["SIM_PW_VIEWER"],
  "\$\$cap_api_token": os.environ["SIM_API_TOKEN"],
}, open(sys.argv[1], "w"))
PY

echo "== compose gerado do template"
(cd "$TMP" && uv run -q --with pyyaml python "$RAIZ/scripts/rastro_caprover_sim.py" \
  "$TEMPLATE" "$TMP/values.json" "$TMP/compose.yml" --app "$SIM_APP")
DC=(docker compose -p "$PROJ" -f "$TMP/compose.yml" -f "$AQUI/compose.extra.yml")

echo "== subindo"
"${DC[@]}" up -d --quiet-pull 2>&1 | grep -viE "^ *(container|network|volume)" || true
"${DC[@]}" wait pg-bootstrap >/dev/null && echo "bootstrap: OK" || { "${DC[@]}" logs pg-bootstrap | tail -20; exit 1; }
sleep 5
"${DC[@]}" ps --format '{{.Service}} {{.State}}' | sort

echo "== sonda"
RC=0
"${DC[@]}" --profile probe run --rm probe || RC=1

echo "== resiliência: ingest parado + restart do broker (fila QoS1 persistente)"
"${DC[@]}" stop "$SIM_APP-ingest" >/dev/null 2>&1
"${DC[@]}" --profile probe run --rm probe publica 5 | grep -E "^(PASS|FAIL)" | tail -1 || RC=1
"${DC[@]}" restart "$SIM_APP-broker" >/dev/null 2>&1
sleep 3
"${DC[@]}" start "$SIM_APP-ingest" >/dev/null 2>&1
"${DC[@]}" --profile probe run --rm probe conta 5 | grep -E "^(PASS|FAIL)" || RC=1
"${DC[@]}" --profile probe run --rm probe conta 5 >/dev/null 2>&1 || true

echo "== portas declaradas / publicadas"
for s in $("${DC[@]}" ps --format '{{.Name}}'); do
  echo "$s declared=$(docker inspect -f '{{index .Config.Labels "rastro.sim.declared-ports"}}' "$s") published=$(docker port "$s" | wc -l)"
done

echo "== conteúdo das imagens (sem testes, .git, .env, chaves)"
for i in broker ingest api web pgtools; do
  achados=$(docker run --rm --entrypoint sh "ghcr.io/digidem/rastro-$i:$SIM_TAG" -c \
    'find / -xdev \( -name .git -o -name "*.env" -o -name .env -o -name "test_*.py" -o -name "*.test.ts" -o -name conftest.py -o -name "*.key" -o -name "*.pem" \) 2>/dev/null | grep -vE "^/(proc|sys|usr/lib/ssl/|usr/lib/python3[^/]*/(test|site-packages/.*/tests?)|usr/share|etc/ssl|usr/local/lib/python3[^/]*/(test|site-packages))" | head -5')
  if [ -z "$achados" ]; then echo "PASS imagem $i limpa"; else echo "FAIL imagem $i: $achados"; RC=1; fi
done
if command -v trufflehog >/dev/null; then
  for i in broker ingest api web pgtools; do
    if trufflehog docker --image "ghcr.io/digidem/rastro-$i:$SIM_TAG" --no-update --no-verification --fail >/dev/null 2>&1; then
      echo "PASS trufflehog $i"; else echo "FAIL trufflehog $i"; RC=1; fi
  done
fi

[ "$RC" = 0 ] && echo "== F5 SIM: PASS" || { echo "== F5 SIM: FAIL"; "${DC[@]}" logs --tail 20 2>&1 | tail -60; }
exit "$RC"
