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

TPL_DIR=${RASTRO_TPL_DIR:-/rastro}
if [ ! -d "$TPL_DIR" ]; then
  TPL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fi
SECRETS_DIR=${RASTRO_SECRETS_DIR:-/mosquitto/secrets}
DATA_DIR=${RASTRO_DATA_DIR:-/mosquitto/data}
PUBLIC_DIR=${RASTRO_PUBLIC_DIR:-/mosquitto/public}
PKI_DIR=$DATA_DIR/pki

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

# valida_sans <lista-separada-por-vírgula>: imprime "DNS:a,DNS:b" (lista normalizada).
# Cada nome tem de casar ^[A-Za-z0-9.-]{1,253}$; elementos vazios são ignorados, mas
# a lista precisa ter ao menos um nome.
valida_sans() {
  lista=$1
  if [ -z "$lista" ]; then
    erro "RASTRO_BROKER_SANS não definida — no modo TLS automático informe os nomes DNS do broker separados por vírgula (ex.: srv-captain--app-broker,broker.exemplo.org)"
  fi
  case "$lista" in
    *"$NL"*) erro "RASTRO_BROKER_SANS não pode conter nova linha" ;;
  esac
  saida=
  resto=$lista,
  while [ -n "$resto" ]; do
    nome=${resto%%,*}
    resto=${resto#*,}
    # elemento vazio é ignorado: o template termina a lista com um campo opcional
    # (domínio extra do MQTT) que costuma ficar em branco
    if [ -z "$nome" ]; then
      continue
    fi
    if [ "${#nome}" -gt 253 ]; then
      erro "RASTRO_BROKER_SANS: nome com mais de 253 caracteres"
    fi
    case "$nome" in
      *[!A-Za-z0-9.-]*)
        erro "RASTRO_BROKER_SANS: nome inválido — use só letras, dígitos, '.' e '-' (sem espaços)"
        ;;
    esac
    case "$nome" in
      [0-9]*.[0-9]*.[0-9]*.[0-9]*)
        saida=${saida:+$saida,}IP:$nome
        ;;
      *)
        saida=${saida:+$saida,}DNS:$nome
        ;;
    esac
  done
  if [ -z "$saida" ]; then
    erro "RASTRO_BROKER_SANS não tem nenhum nome — informe ao menos um nome DNS do broker"
  fi
  printf '%s' "$saida"
}

# gera_pki_automatica: cria/renova a PKI privada em $PKI_DIR (volume persistente).
# A CA é criada UMA vez e nunca é regenerada sozinha; só o certificado do servidor é
# refeito (ausente, SANs diferentes, fora da CA ou vencendo em < 30 dias).
gera_pki_automatica() {
  sans=$(valida_sans "${RASTRO_BROKER_SANS:-}")
  mkdir -p "$PKI_DIR" 2>/dev/null && chmod 0700 "$PKI_DIR" 2>/dev/null \
    || erro "não foi possível preparar $PKI_DIR (o volume de dados precisa pertencer ao uid 1883)"

  if [ -e "$PKI_DIR/ca.crt" ] && [ ! -e "$PKI_DIR/ca.key" ]; then
    erro "$PKI_DIR/ca.crt existe mas ca.key não — a CA não é regenerada automaticamente; restaure ca.key ou apague $PKI_DIR de propósito"
  fi
  if [ -e "$PKI_DIR/ca.key" ] && [ ! -e "$PKI_DIR/ca.crt" ]; then
    erro "$PKI_DIR/ca.key existe mas ca.crt não — a CA não é regenerada automaticamente; restaure ca.crt ou apague $PKI_DIR de propósito"
  fi

  if [ ! -s "$PKI_DIR/ca.crt" ]; then
    printf 'Gerando a CA privada do broker em %s\n' "$PKI_DIR"
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$PKI_DIR/ca.key.new" 2>/dev/null \
      || erro "openssl falhou ao gerar a chave da CA"
    openssl req -x509 -new -key "$PKI_DIR/ca.key.new" -sha256 -days 3650 \
      -subj "/O=Rastro/CN=Rastro Broker CA" \
      -addext "basicConstraints=critical,CA:TRUE" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -addext "subjectKeyIdentifier=hash" \
      -addext "authorityKeyIdentifier=keyid:always" \
      -out "$PKI_DIR/ca.crt.new" 2>/dev/null \
      || erro "openssl falhou ao gerar o certificado da CA"
    chmod 0400 "$PKI_DIR/ca.key.new"
    chmod 0644 "$PKI_DIR/ca.crt.new"
    mv "$PKI_DIR/ca.key.new" "$PKI_DIR/ca.key" && mv "$PKI_DIR/ca.crt.new" "$PKI_DIR/ca.crt" \
      || erro "não foi possível gravar a CA em $PKI_DIR"
    # CA nova invalida qualquer servidor antigo
    rm -f "$PKI_DIR/server.crt" "$PKI_DIR/server.key" "$PKI_DIR/server.sans" "$PKI_DIR/ca.srl"
  fi

  if ! openssl x509 -checkend 2592000 -noout -in "$PKI_DIR/ca.crt" >/dev/null 2>&1; then
    printf 'AVISO: a CA em %s vence em menos de 30 dias — troque-a de propósito (apague %s e redistribua a nova ca.crt)\n' \
      "$PKI_DIR/ca.crt" "$PKI_DIR" >&2
  fi

  refaz=
  if [ ! -s "$PKI_DIR/server.crt" ] || [ ! -s "$PKI_DIR/server.key" ]; then
    refaz="certificado do servidor ausente"
  elif [ "$(cat "$PKI_DIR/server.sans" 2>/dev/null || true)" != "$sans" ]; then
    refaz="lista de SANs mudou"
  elif ! openssl verify -CAfile "$PKI_DIR/ca.crt" "$PKI_DIR/server.crt" >/dev/null 2>&1; then
    refaz="certificado do servidor não confere com a CA"
  elif ! openssl x509 -checkend 2592000 -noout -in "$PKI_DIR/server.crt" >/dev/null 2>&1; then
    refaz="certificado do servidor vence em menos de 30 dias"
  fi

  if [ -n "$refaz" ]; then
    printf 'Gerando o certificado do servidor (%s)\n' "$refaz"
    cn=${sans#DNS:}
    cn=${cn%%,*}
    rm -f "$PKI_DIR/server.key.new" "$PKI_DIR/server.csr" "$PKI_DIR/server.ext" "$PKI_DIR/server.crt.new"
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$PKI_DIR/server.key.new" 2>/dev/null \
      || erro "openssl falhou ao gerar a chave do servidor"
    openssl req -new -key "$PKI_DIR/server.key.new" -subj "/O=Rastro/CN=$cn" -out "$PKI_DIR/server.csr" 2>/dev/null \
      || erro "openssl falhou ao gerar o pedido de certificado do servidor"
    {
      printf 'basicConstraints=critical,CA:FALSE\n'
      printf 'keyUsage=critical,digitalSignature,keyEncipherment\n'
      printf 'extendedKeyUsage=serverAuth\n'
      printf 'subjectKeyIdentifier=hash\n'
      printf 'authorityKeyIdentifier=keyid,issuer\n'
      printf 'subjectAltName=%s\n' "$sans"
    } > "$PKI_DIR/server.ext"
    openssl x509 -req -in "$PKI_DIR/server.csr" -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" \
      -CAserial "$PKI_DIR/ca.srl" -CAcreateserial -days 825 -sha256 \
      -extfile "$PKI_DIR/server.ext" -out "$PKI_DIR/server.crt.new" 2>/dev/null \
      || erro "openssl falhou ao assinar o certificado do servidor"
    chmod 0400 "$PKI_DIR/server.key.new"
    chmod 0644 "$PKI_DIR/server.crt.new"
    rm -f "$PKI_DIR/server.csr" "$PKI_DIR/server.ext" "$PKI_DIR/server.key" "$PKI_DIR/server.crt"
    mv "$PKI_DIR/server.key.new" "$PKI_DIR/server.key" && mv "$PKI_DIR/server.crt.new" "$PKI_DIR/server.crt" \
      || erro "não foi possível gravar o certificado do servidor em $PKI_DIR"
    printf '%s\n' "$sans" > "$PKI_DIR/server.sans"
  fi
}

# publica_ca <caminho-da-ca>: copia SÓ o certificado público da CA para o volume
# compartilhado com ingest/web. Nunca copia chave.
publica_ca() {
  if ! touch "$PUBLIC_DIR/.rastro-write-test" 2>/dev/null ||
    ! rm -f "$PUBLIC_DIR/.rastro-write-test" 2>/dev/null; then
    erro "$PUBLIC_DIR não é gravável pelo usuário do contêiner — o volume compartilhado da CA precisa pertencer ao uid 1883"
  fi
  cp -- "$1" "$PUBLIC_DIR/ca.crt.new" \
    && chmod 0644 "$PUBLIC_DIR/ca.crt.new" \
    && mv -f "$PUBLIC_DIR/ca.crt.new" "$PUBLIC_DIR/ca.crt" \
    || erro "não foi possível publicar a CA em $PUBLIC_DIR/ca.crt"
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

# (c) senhas e ACL: se RASTRO_ACCOUNTS_FILE estiver definida (WP-C), gera via accounts.py;
# caso contrário, mantém comportamento legado com usuários fixos gateway e ingest.
if [ -n "${RASTRO_ACCOUNTS_FILE:-}" ]; then
  if [ ! -f "$RASTRO_ACCOUNTS_FILE" ] || [ ! -s "$RASTRO_ACCOUNTS_FILE" ]; then
    erro "RASTRO_ACCOUNTS_FILE definido mas o arquivo não existe ou está vazio: $RASTRO_ACCOUNTS_FILE"
  fi
  # Chama accounts.py para gerar passwd (texto puro) e aclfile
  if ! python3 "$TPL_DIR/accounts.py" "$RASTRO_ACCOUNTS_FILE" "$RUN/passwd" "$RUN/aclfile"; then
    erro "accounts.py falhou ao processar $RASTRO_ACCOUNTS_FILE"
  fi
  # hasheia no próprio lugar (-U não recebe senha alguma na linha de comando)
  if ! mosquitto_passwd -U "$RUN/passwd"; then
    erro "mosquitto_passwd falhou ao gerar os hashes de $RUN/passwd (binário ausente?)"
  fi
  RETAIN_AVAILABLE=${RASTRO_RETAIN_AVAILABLE:-false}
  unset RASTRO_ACCOUNTS_FILE
elif [ -n "${RASTRO_NATIVE_NODES:-}" ]; then
  # Modo nativo derivado (RASTRO_NATIVE_NODES + RASTRO_NATIVE_SECRET)
  if [ -z "${RASTRO_NATIVE_SECRET:-}" ]; then
    erro "RASTRO_NATIVE_SECRET não definida — obrigatória quando RASTRO_NATIVE_NODES estiver configurada"
  fi
  if [ "${#RASTRO_NATIVE_SECRET}" -lt 24 ]; then
    erro "RASTRO_NATIVE_SECRET muito curta: mínimo 24 caracteres"
  fi
  case "$RASTRO_NATIVE_SECRET" in
    *"$NL"*) erro "RASTRO_NATIVE_SECRET não pode conter nova linha" ;;
  esac
  if [ -z "${RASTRO_MQTT_PASSWORD_INGEST:-}" ]; then
    erro "RASTRO_MQTT_PASSWORD_INGEST não definida — obrigatória (senha do usuário 'ingest')"
  fi
  if [ "${#RASTRO_MQTT_PASSWORD_INGEST}" -lt 24 ]; then
    erro "RASTRO_MQTT_PASSWORD_INGEST muito curta: mínimo 24 caracteres"
  fi
  case "$RASTRO_MQTT_PASSWORD_INGEST" in
    *:*) erro "RASTRO_MQTT_PASSWORD_INGEST não pode conter ':' (é o separador do arquivo passwd)" ;;
    *"$NL"*) erro "RASTRO_MQTT_PASSWORD_INGEST não pode conter nova linha" ;;
  esac
  if [ -z "${RASTRO_MQTT_PASSWORD_OUTBOX:-}" ]; then
    erro "RASTRO_MQTT_PASSWORD_OUTBOX não definida — obrigatória no modo nativo (senha do usuário 'outbox')"
  fi
  if [ "${#RASTRO_MQTT_PASSWORD_OUTBOX}" -lt 24 ]; then
    erro "RASTRO_MQTT_PASSWORD_OUTBOX muito curta: mínimo 24 caracteres"
  fi
  case "$RASTRO_MQTT_PASSWORD_OUTBOX" in
    *:*) erro "RASTRO_MQTT_PASSWORD_OUTBOX não pode conter ':' (é o separador do arquivo passwd)" ;;
    *"$NL"*) erro "RASTRO_MQTT_PASSWORD_OUTBOX não pode conter nova linha" ;;
  esac
  if [ -n "${RASTRO_MQTT_PASSWORD_GATEWAY:-}" ]; then
    if [ "${#RASTRO_MQTT_PASSWORD_GATEWAY}" -lt 24 ]; then
      erro "RASTRO_MQTT_PASSWORD_GATEWAY muito curta: mínimo 24 caracteres"
    fi
    case "$RASTRO_MQTT_PASSWORD_GATEWAY" in
      *:*) erro "RASTRO_MQTT_PASSWORD_GATEWAY não pode conter ':' (é o separador do arquivo passwd)" ;;
      *"$NL"*) erro "RASTRO_MQTT_PASSWORD_GATEWAY não pode conter nova linha" ;;
    esac
  fi

  # Deriva as contas em arquivo privado temporário (modo 0600 em $RUN)
  if ! python3 "$TPL_DIR/derive.py" "$RUN/accounts.json"; then
    erro "derive.py falhou ao processar contas nativas a partir do ambiente"
  fi

  # Chama accounts.py para gerar passwd (texto puro) e aclfile
  if ! python3 "$TPL_DIR/accounts.py" "$RUN/accounts.json" "$RUN/passwd" "$RUN/aclfile"; then
    erro "accounts.py falhou ao processar contas nativas"
  fi

  # Remove arquivo temporário de contas com senhas em texto puro
  rm -f "$RUN/accounts.json"

  # hasheia no próprio lugar (-U não recebe senha alguma na linha de comando)
  if ! mosquitto_passwd -U "$RUN/passwd"; then
    erro "mosquitto_passwd falhou ao gerar os hashes de $RUN/passwd (binário ausente?)"
  fi

  # Remove segredos do ambiente antes do exec
  unset RASTRO_NATIVE_SECRET
  unset RASTRO_MQTT_PASSWORD_INGEST
  unset RASTRO_MQTT_PASSWORD_OUTBOX
  unset RASTRO_MQTT_PASSWORD_GATEWAY

  RETAIN_AVAILABLE=${RASTRO_RETAIN_AVAILABLE:-false}
else
  # Modo legado (gateway/ingest)
  RETAIN_AVAILABLE=${RASTRO_RETAIN_AVAILABLE:-true}
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

  # (e) ACL renderizada legado — PREFIX já foi validado, logo é seguro para o sed
  if ! sed "s/@PREFIX@/$PREFIX/g" "$TPL_DIR/aclfile.tmpl" > "$RUN/aclfile"; then
    erro "falha ao renderizar a ACL a partir de $TPL_DIR/aclfile.tmpl"
  fi
fi

# (g) persistência: sem volume gravável não há fila QoS 1 sobrevivendo a restart (T4)
if ! touch "$DATA_DIR/.rastro-write-test" 2>/dev/null ||
  ! rm -f "$DATA_DIR/.rastro-write-test" 2>/dev/null; then
  erro "$DATA_DIR não é gravável pelo usuário do contêiner — o volume de dados precisa pertencer ao uid 1883 (chown -R 1883:1883 no volume/montagem do host)"
fi

# (d) material TLS — precedência: (1) os três arquivos montados, (2) as três variáveis
# base64, (3) modo automático (PKI privada gerada em $PKI_DIR, SANs de
# RASTRO_BROKER_SANS). Os caminhos escolhidos vão no conf.
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
    # nenhuma variável B64: arquivos montados pela metade são erro (não cair no automático)
    if [ -s "$CAFILE" ] || [ -s "$CERTFILE" ] || [ -s "$KEYFILE" ]; then
      erro "arquivos TLS montados em $SECRETS_DIR estão incompletos — são necessários ca.crt, server.crt e server.key juntos (ou remova-os para usar o modo automático)"
    fi
    # (3) modo automático
    gera_pki_automatica
    CAFILE=$PKI_DIR/ca.crt
    CERTFILE=$PKI_DIR/server.crt
    KEYFILE=$PKI_DIR/server.key
  fi
fi

# a CA em uso (qualquer dos três modos) é publicada para ingest/web
publica_ca "$CAFILE"

# (f) conf renderizado (delimitador '|' porque os caminhos contêm '/')
if ! sed -e "s|@CAFILE@|$CAFILE|g" \
  -e "s|@CERTFILE@|$CERTFILE|g" \
  -e "s|@KEYFILE@|$KEYFILE|g" \
  -e "s|@PASSWD@|$RUN/passwd|g" \
  -e "s|@ACL@|$RUN/aclfile|g" \
  -e "s|@RETAIN_AVAILABLE@|$RETAIN_AVAILABLE|g" \
  "$TPL_DIR/mosquitto.conf.tmpl" > "$RUN/mosquitto.conf"; then
  erro "falha ao renderizar a configuração a partir de $TPL_DIR/mosquitto.conf.tmpl"
fi

# (f2) listener WebSockets opcional (RASTRO_MQTT_WEBSOCKETS=1, padrão do template
# CapRover): SEM TLS de propósito — ele só é alcançável pela rede interna, atrás do
# nginx do CapRover, que termina o HTTPS (porta 443) e repassa o WebSocket. Assim quem
# só tem a 443 aberta consegue falar MQTT. Senha e ACL valem igual (opções globais).
case "${RASTRO_MQTT_WEBSOCKETS:-0}" in
  1)
    {
      printf '\n# listener WebSockets (atrás do proxy do CapRover; sem TLS aqui)\n'
      printf 'listener 9001 0.0.0.0\n'
      printf 'protocol websockets\n'
    } >> "$RUN/mosquitto.conf" \
      || erro "falha ao acrescentar o listener WebSockets em $RUN/mosquitto.conf"
    ;;
  0 | '') : ;;
  *) erro "RASTRO_MQTT_WEBSOCKETS inválida: use 0 ou 1" ;;
esac

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
