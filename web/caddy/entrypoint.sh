#!/bin/sh
# Rastro — entrypoint do container web (caddy). POSIX sh, sem bashismos.
#
# Antes de subir o caddy ele faz duas coisas:
#   1. publica /config.json (título do deploy) que o viewer lê em runtime — o
#      título muda com uma variável do CapRover, sem rebuildar a imagem;
#   2. valida RASTRO_API_UPSTREAM, que o Caddyfile injeta no reverse_proxy.
# Validação com falha = sai já: deploy mal configurado aparece no log do
# container, não como 5xx aleatório no mapa.
set -eu

# Diretórios de trabalho do caddy (config/CA/certificados/locks) — graváveis
# para o usuário rastro (uid 10001); o resto da imagem é read-only.
export XDG_DATA_HOME="${XDG_DATA_HOME:-/tmp/caddy/data}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/tmp/caddy/config}"

WWW_DIR=/tmp/rastro-www
TITULO_PADRAO=Rastro
# ^[[:alnum:] À-ÿ._()-]{1,60}$ — letras (inclusive acentuadas), dígitos,
# espaço, ponto, sublinhado, parênteses e hífen. Sem aspas, barra, `<`, `;`:
# é essa whitelist que permite interpolar o valor direto no JSON aqui embaixo.
REGEX_TITULO='^[[:alnum:] À-ÿ._()-]{1,60}$'
# host:porta do serviço da API (no CapRover: srv-captain--rastro-api:8080).
REGEX_UPSTREAM='^[A-Za-z0-9._-]+:[0-9]{1,5}$'

erro() {
  printf 'ERRO: %s inválido\n' "$1" >&2
  exit 1
}

# --- título ---------------------------------------------------------------
# Nova linha dentro do título quebraria o JSON, e as âncoras do grep (^/$)
# valem por LINHA — então a nova linha é barrada antes da regex.
NL='
'
TITULO="${RASTRO_TITLE:-$TITULO_PADRAO}"
case "$TITULO" in
  *"$NL"*) erro RASTRO_TITLE ;;
esac
# locale C: byte a byte, o intervalo À-ÿ cobre as letras latinas acentuadas.
printf '%s\n' "$TITULO" | LC_ALL=C grep -Eq "$REGEX_TITULO" || erro RASTRO_TITLE

# --- upstream da API (obrigatório) ----------------------------------------
UPSTREAM="${RASTRO_API_UPSTREAM:-}"
[ -n "$UPSTREAM" ] || erro RASTRO_API_UPSTREAM
printf '%s\n' "$UPSTREAM" | LC_ALL=C grep -Eq "$REGEX_UPSTREAM" ||
  erro RASTRO_API_UPSTREAM

# Só depois das duas validações: container mal configurado não escreve nada.
mkdir -p "$WWW_DIR"
# Única chave do arquivo; o valor já passou pela whitelist (sem `"` nem `\`).
printf '{"title":"%s"}' "$TITULO" > "$WWW_DIR/config.json"

mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
