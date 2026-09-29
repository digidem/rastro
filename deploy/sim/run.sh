#!/usr/bin/env bash
# F5 — simula a instalação do template CapRover do Rastro, localmente, e roda a sonda.
#
# Uso: deploy/sim/run.sh <caminho/do/rastro.yml> [--keep] [--no-build]
#   rastro.yml: o template da loja (digidem/caprover-one-click-apps, public/v4/apps/).
#
# Passos: constrói as 5 imagens com os nomes do Docker Hub (communityfirst) e a tag "simtest";
# senhas aleatórias; gera o compose A PARTIR do template (rastro_caprover_sim.py) — o serviço
# <app>-setup do template prepara o Postgres simulado (superusuário), o broker gera o próprio
# TLS e publica a CA no volume compartilhado <app>-pki; sobe em rede interna (sem portas no
# host); espera o "OK" do setup; roda a sonda (deploy/sim/probe.py) e confere as imagens.
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
  docker build -q -t communityfirst/rastro-broker:$SIM_TAG "$RAIZ/broker" >/dev/null
  docker build -q -t communityfirst/rastro-ingest:$SIM_TAG -f "$RAIZ/services/rastro_gateway/Dockerfile" "$RAIZ/services" >/dev/null
  docker build -q -t communityfirst/rastro-api:$SIM_TAG -f "$RAIZ/services/rastro_api/Dockerfile" "$RAIZ/services" >/dev/null
  docker build -q -t communityfirst/rastro-web:$SIM_TAG "$RAIZ/web" >/dev/null
  docker build -q -t communityfirst/rastro-pgtools:$SIM_TAG -f "$RAIZ/deploy/postgres/Dockerfile" "$RAIZ" >/dev/null
fi
# o template usa a variante por versão do servidor (pgtools:<tag>-pg17)
docker tag communityfirst/rastro-pgtools:$SIM_TAG communityfirst/rastro-pgtools:$SIM_TAG-pg17

export SIM_FRONT="$TMP/front"
mkdir -p "$SIM_FRONT"
"$RAIZ/scripts/rastro_gen_certs.sh" --out-dir "$TMP/front-ca" --cert-dir "$SIM_FRONT/certs" \
  --san DNS:srv-captain--front >/dev/null
chmod 0755 "$SIM_FRONT" "$SIM_FRONT/certs"; chmod 0644 "$SIM_FRONT/certs"/*
cat > "$SIM_FRONT/nginx.conf" <<NGX
server {
  listen 443 ssl;
  ssl_certificate /certs/server.crt;
  ssl_certificate_key /certs/server.key;
  location / {
    proxy_pass http://srv-captain--$SIM_APP;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  }
}
NGX

senha() { openssl rand -hex 16; }
export SIM_PG_ADMIN_PW="$(senha)" SIM_PW_INGEST="$(senha)" SIM_PW_VIEWER="$(senha)" \
       SIM_PW_MAINT="$(senha)" SIM_PW_BACKUP="$(senha)" SIM_PW_GATEWAY="$(senha)" \
       SIM_PW_MQTT_INGEST="$(senha)" SIM_API_TOKEN="$(openssl rand -hex 24)"
# Só o necessário (como o usuário no CapRover): tag, senha de admin do Postgres, nome do banco
# e as senhas que a sonda precisa conhecer. O restante vem dos padrões do template.
python3 - "$TMP/values.json" <<'PY'
import json, os, sys
json.dump({
  "$$cap_tag": os.environ["SIM_TAG"],
  "$$cap_pg_admin_password": os.environ["SIM_PG_ADMIN_PW"],
  "$$cap_pg_database": os.environ["SIM_DB"],
  "$$cap_mqtt_pw_gateway": os.environ["SIM_PW_GATEWAY"],
  "$$cap_mqtt_pw_ingest": os.environ["SIM_PW_MQTT_INGEST"],
  "$$cap_pg_pw_ingest": os.environ["SIM_PW_INGEST"],
  "$$cap_pg_pw_viewer": os.environ["SIM_PW_VIEWER"],
  "$$cap_pg_pw_maint": os.environ["SIM_PW_MAINT"],
  "$$cap_pg_pw_backup": os.environ["SIM_PW_BACKUP"],
  "$$cap_api_token": os.environ["SIM_API_TOKEN"],
}, open(sys.argv[1], "w"))
PY

echo "== compose gerado do template"
(cd "$TMP" && uv run -q --with pyyaml python "$RAIZ/scripts/rastro_caprover_sim.py" \
  "$TEMPLATE" "$TMP/values.json" "$TMP/compose.yml" --app "$SIM_APP")
DC=(docker compose -p "$PROJ" -f "$TMP/compose.yml" -f "$AQUI/compose.extra.yml")

echo "== subindo"
"${DC[@]}" up -d --quiet-pull 2>&1 | grep -viE "^ *(container|network|volume)" || true
# o serviço <app>-setup imprime "OK: ..." (ou "ERRO: ...") e fica ocioso — espera até 3 min
SETUP="$SIM_APP-setup"; ESTADO=""
for _ in $(seq 1 90); do
  LOG="$("${DC[@]}" logs --no-log-prefix "$SETUP" 2>&1 || true)"
  if grep -q '^OK: PostgreSQL preparado' <<<"$LOG"; then ESTADO=ok; break; fi
  if grep -q '^ERRO:' <<<"$LOG"; then ESTADO=erro; break; fi
  sleep 2
done
if [ "$ESTADO" = ok ]; then echo "setup: OK"; else echo "setup: FALHOU ($ESTADO)"; tail -20 <<<"$LOG"; exit 1; fi
# ocioso de verdade: continua "running" (não reexecuta contra o banco)
[ "$("${DC[@]}" ps --format '{{.State}}' "$SETUP")" = running ] && echo "PASS setup ocioso após o OK" || { echo "FAIL setup não ficou ocioso"; RC_SETUP=1; }
sleep 5
"${DC[@]}" ps --format '{{.Service}} {{.State}}' | sort

echo "== sonda"
RC=${RC_SETUP:-0}
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
  achados=$(docker run --rm --entrypoint sh "communityfirst/rastro-$i:$SIM_TAG" -c \
    'find / -xdev \( -name .git -o -name "*.env" -o -name .env -o -name "test_*.py" -o -name "*.test.ts" -o -name conftest.py -o -name "*.key" -o -name "*.pem" \) 2>/dev/null | grep -vE "^/(proc|sys|usr/lib/ssl/|usr/lib/python3[^/]*/(test|site-packages/.*/tests?)|usr/share|etc/ssl|usr/local/lib/python3[^/]*/(test|site-packages))" | head -5')
  if [ -z "$achados" ]; then echo "PASS imagem $i limpa"; else echo "FAIL imagem $i: $achados"; RC=1; fi
done
if command -v trufflehog >/dev/null; then
  for i in broker ingest api web pgtools; do
    if trufflehog docker --image "communityfirst/rastro-$i:$SIM_TAG" --no-update --no-verification --fail >/dev/null 2>&1; then
      echo "PASS trufflehog $i"; else echo "FAIL trufflehog $i"; RC=1; fi
  done
fi

[ "$RC" = 0 ] && echo "== F5 SIM: PASS" || { echo "== F5 SIM: FAIL"; "${DC[@]}" logs --tail 20 2>&1 | tail -60; }
exit "$RC"
