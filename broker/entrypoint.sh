#!/bin/sh
# Rastro — entrypoint do broker mosquitto da imagem (F3a). POSIX sh, sem bashismos.
#
# Só sabe montar a configuração a partir do ambiente/arquivos montados: renderiza os
# modelos de /rastro/ num diretório privado em /tmp e executa o mosquitto. Regras:
#   - NUNCA imprime senha, chave ou conteúdo de arquivo — mensagem cita só caminhos e
#     nomes de variáveis; sem `set -x` (o rastreamento despejaria segredos no log).
#   - Senha nunca é argumento de programa externa: entra no arquivo com `printf`
#     (embutido do próprio shell) e é hasheada por `mosquitto_passwd -U`, que recebe
#     apenas o caminho. Os env vars são removidos do ambiente antes do exec().
#   - Toda falha: mensagem PT-BR "ERRO: ..." no stderr e exit 1.
set -eu
umask 077

# Locale determinístico: a validação por padrão ([a-z0-9_-]) e o sed não podem depender
# do locale do contêiner (faixas de letras mudam com a colação).
LC_ALL=C
export LC_ALL

TPL_DIR=/rastro
SECRETS_DIR=/mosquitto/secrets
DATA_DIR=/mosquitto/data

# Nova linha literal, usada para rejeitar valor de variável com nova linha embutida.
NL='
'

# erro <mensagem>: falha PT-BR no stderr + exit 1. Toda saída de erro passa por aqui.
erro() {
  printf 'ERRO: %s\n' "$1" >&2
  exit 1
}

# decodifica <valor-base64> <destino>: o valor entra no base64 pela stdin (via printf do
# shell), nunca como argumento de programa externa.
decodifica() {
  if ! printf '%s' "$1" | base64 -d > "$2"; then
    rm -f "$2"
    erro "base64 inválido ao montar $2 (valor truncado ou não é base64?)"
  fi
}

# confere_pem <caminho>: compara apenas o cabeçalho; o conteúdo nunca vai para o log.
confere_pem() {
  if [ ! -s "$1" ]; then
    erro "$1 está vazio"
  fi
  case "$(head -n 1 -- "$1" 2>/dev/null)" in
    -----BEGIN*) : ;;
    *) erro "$1 não começa com '-----BEGIN': PEM inválido (base64 truncado ou conteúdo errado?)" ;;
  esac
}

# (a) diretório privado dos arquivos renderizados: mktemp -d já cria 0700 (+ umask 077)
RUN=$(mktemp -d /tmp/rastro-broker.XXXXXX) \
  || erro "não foi possível criar o diretório temporário em /tmp (mktemp -d)"

# (b) prefixo de tópico. Aceita só minúsculas, dígitos, '_' e '-', de 1 a 32 caracteres;
# isso também rejeita vazio, '/', '+', '#', espaços, maiúsculas e nova linha — e deixa o
# valor seguro para usar no sed adiante.
PREFIX=${RASTRO_MQTT_TOPIC_PREFIX:-rastro}
case "$PREFIX" in
  '' | *[!a-z0-9_-]*)
    erro "RASTRO_MQTT_TOPIC_PREFIX inválida: use só minúsculas, dígitos, '_' e '-' (1 a 32 caracteres); sem '/', '+', '#', espaços ou maiúsculas"
    ;;
esac
if [ "${#PREFIX}" -gt 32 ]; then
  erro "RASTRO_MQTT_TOPIC_PREFIX inválida: mais de 32 caracteres"
fi

# (c) senhas dos dois usuários fixos da imagem (gateway escreve, ingest só lê)
if [ -z "${RASTRO_MQTT_PASSWORD_GATEWAY:-}" ]; then
  erro "RASTRO_MQTT_PASSWORD_GATEWAY não definida — obrigatória (senha do usuário 'gateway')"
fi
if [ -z "${RASTRO_MQTT_PASSWORD_INGEST:-}" ]; then
  erro "RASTRO_MQTT_PASSWORD_INGEST não definida — obrigatória (senha do usuário 'ingest')"
fi
pw_gateway=$RASTRO_MQTT_PASSWORD_GATEWAY
pw_ingest=$RASTRO_MQTT_PASSWORD_INGEST

if [ "${#pw_gateway}" -lt 24 ]; then
  erro "RASTRO_MQTT_PASSWORD_GATEWAY muito curta: mínimo 24 caracteres"
fi
if [ "${#pw_ingest}" -lt 24 ]; then
  erro "RASTRO_MQTT_PASSWORD_INGEST muito curta: mínimo 24 caracteres"
fi
case "$pw_gateway" in
  *:*) erro "RASTRO_MQTT_PASSWORD_GATEWAY não pode conter ':' (é o separador do arquivo passwd)" ;;
  *"$NL"*) erro "RASTRO_MQTT_PASSWORD_GATEWAY não pode conter nova linha" ;;
esac
case "$pw_ingest" in
  *:*) erro "RASTRO_MQTT_PASSWORD_INGEST não pode conter ':' (é o separador do arquivo passwd)" ;;
  *"$NL"*) erro "RASTRO_MQTT_PASSWORD_INGEST não pode conter nova linha" ;;
esac

# texto puro só existe um instante, num arquivo 0600 dentro do diretório 0700 acima
{
  printf 'gateway:%s\n' "$pw_gateway"
  printf 'ingest:%s\n' "$pw_ingest"
} > "$RUN/passwd" || erro "falha ao escrever o arquivo passwd em $RUN"

# hasheia no próprio lugar (-U não recebe senha alguma na linha de comando)
if ! mosquitto_passwd -U "$RUN/passwd"; then
  erro "mosquitto_passwd falhou ao gerar os hashes de $RUN/passwd (binário ausente?)"
fi
# segredo fora do alcance do processo que herda este ambiente (exec abaixo)
unset pw_gateway pw_ingest
unset RASTRO_MQTT_PASSWORD_GATEWAY RASTRO_MQTT_PASSWORD_INGEST

# (d) material TLS — exatamente UMA fonte completa: os três arquivos montados
# (recomendado) ou as três variáveis base64. Os caminhos escolhidos vão no conf.
CAFILE=$SECRETS_DIR/ca.crt
CERTFILE=$SECRETS_DIR/server.crt
KEYFILE=$SECRETS_DIR/server.key

# arquivo montado mas ilegível para o uid do contêiner: erro específico (senão cairia em
# "nenhuma fonte TLS completa", que engana)
if [ -d "$SECRETS_DIR" ] && [ ! -x "$SECRETS_DIR" ]; then
  erro "$SECRETS_DIR existe mas não é acessível pelo uid $(id -u) — ajuste dono/permissão do diretório montado"
fi
for montado in "$CAFILE" "$CERTFILE" "$KEYFILE"; do
  if [ -e "$montado" ] && [ ! -r "$montado" ]; then
    erro "$montado existe mas não é legível pelo uid $(id -u) — ajuste dono/permissão (ex.: chown 1883 e chmod 0400)"
  fi
done
if [ -s "$CAFILE" ] && [ -s "$CERTFILE" ] && [ -s "$KEYFILE" ]; then
  # fonte = arquivos montados: usa os caminhos como estão (nada copia, nada decodifica)
  :
else
  quantas=0
  if [ -n "${RASTRO_TLS_CA_B64:-}" ]; then quantas=$((quantas + 1)); fi
  if [ -n "${RASTRO_TLS_SERVER_CRT_B64:-}" ]; then quantas=$((quantas + 1)); fi
  if [ -n "${RASTRO_TLS_SERVER_KEY_B64:-}" ]; then quantas=$((quantas + 1)); fi

  if [ "$quantas" -eq 3 ]; then
    decodifica "$RASTRO_TLS_CA_B64" "$RUN/ca.crt"
    decodifica "$RASTRO_TLS_SERVER_CRT_B64" "$RUN/server.crt"
    decodifica "$RASTRO_TLS_SERVER_KEY_B64" "$RUN/server.key"
    confere_pem "$RUN/ca.crt"
    confere_pem "$RUN/server.crt"
    confere_pem "$RUN/server.key"
    CAFILE=$RUN/ca.crt
    CERTFILE=$RUN/server.crt
    KEYFILE=$RUN/server.key
    unset RASTRO_TLS_CA_B64 RASTRO_TLS_SERVER_CRT_B64 RASTRO_TLS_SERVER_KEY_B64
  elif [ "$quantas" -gt 0 ]; then
    erro "configuração TLS incompleta: $quantas de 3 variáveis definidas — use RASTRO_TLS_CA_B64, RASTRO_TLS_SERVER_CRT_B64 e RASTRO_TLS_SERVER_KEY_B64 todas juntas, ou monte os três arquivos em $SECRETS_DIR"
  else
    erro "nenhuma fonte TLS completa: monte ca.crt, server.crt e server.key em $SECRETS_DIR ou defina RASTRO_TLS_CA_B64, RASTRO_TLS_SERVER_CRT_B64 e RASTRO_TLS_SERVER_KEY_B64"
  fi
fi

# (e) ACL renderizada — PREFIX já foi validado, logo é seguro para o sed
if ! sed "s/@PREFIX@/$PREFIX/g" "$TPL_DIR/aclfile.tmpl" > "$RUN/aclfile"; then
  erro "falha ao renderizar a ACL a partir de $TPL_DIR/aclfile.tmpl"
fi

# (f) conf renderizado (delimitador '|' porque os caminhos contêm '/')
if ! sed -e "s|@CAFILE@|$CAFILE|g" \
  -e "s|@CERTFILE@|$CERTFILE|g" \
  -e "s|@KEYFILE@|$KEYFILE|g" \
  -e "s|@PASSWD@|$RUN/passwd|g" \
  -e "s|@ACL@|$RUN/aclfile|g" \
  "$TPL_DIR/mosquitto.conf.tmpl" > "$RUN/mosquitto.conf"; then
  erro "falha ao renderizar a configuração a partir de $TPL_DIR/mosquitto.conf.tmpl"
fi

# (g) persistência: sem volume gravável não há fila QoS 1 sobrevivendo a restart (T4)
if ! touch "$DATA_DIR/.rastro-write-test" 2>/dev/null ||
  ! rm -f "$DATA_DIR/.rastro-write-test" 2>/dev/null; then
  erro "$DATA_DIR não é gravável pelo usuário do contêiner — o volume de dados precisa pertencer ao uid 1883 (chown -R 1883:1883 no volume/montagem do host)"
fi

# (h) só o dono lê os arquivos renderizados (passwd, conf, ACL e eventuais PEMs)
for arquivo in "$RUN"/*; do
  if [ -f "$arquivo" ]; then
    if ! chmod 0400 "$arquivo"; then
      erro "falha ao definir o modo 0400 em $arquivo"
    fi
  fi
done
if ! chmod 700 "$RUN"; then
  erro "falha ao definir o modo 0700 em $RUN"
fi

# (i) ensaio (usado por teste): valida e monta tudo, mas não sobe o broker
if [ "${RASTRO_BROKER_DRY_RUN:-}" = "1" ]; then
  printf 'OK: configuração gerada\n'
  exit 0
fi

# exec: o broker substitui este shell — o SIGTERM do orquestrador chega nele direto
exec mosquitto -c "$RUN/mosquitto.conf" \
  || erro "não foi possível executar o mosquitto com $RUN/mosquitto.conf"
