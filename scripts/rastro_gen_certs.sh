#!/usr/bin/env bash
# Rastro — gera CA local + certificado TLS do broker mosquitto.
#
# Saída:
#   <out-dir>/ca.key          chave PRIVADA da CA — FORA de qualquer repositório git (0700/600)
#   <cert-dir>/ca.crt         certificado da CA — clientes e broker confiam nele
#   <cert-dir>/server.{crt,key}
# Validade: CA 3650 dias; servidor 825 dias.
#
# Uso: scripts/rastro_gen_certs.sh [opções]
#   --out-dir DIR    onde fica ca.key (padrão: ~/.local/share/rastro/ca; recusado se estiver
#                    dentro de um repositório git)
#   --cert-dir DIR   onde ficam ca.crt e server.* (padrão: deploy/mosquitto/certs, gitignored)
#   --san NOME       SAN do servidor, repetível: DNS:exemplo.org ou IP:10.0.0.5. Com --san,
#                    o certificado contém SÓ os nomes dados. Sem --san, o padrão local:
#                    localhost, 127.0.0.1 e "mosquitto" (serviço do compose).
#   --b64            no fim, imprime RASTRO_TLS_CA_B64 / _SERVER_CRT_B64 / _SERVER_KEY_B64 e
#                    RASTRO_MQTT_CA_B64 (base64 -w0) para colar no CapRover. ATENÇÃO: a linha
#                    _SERVER_KEY_B64 é a chave privada do broker — não deixe em histórico/chat.
#   --force          regenera tudo; faz backup <dir>.bak-<epoch> antes e exige digitar DESTRUIR
#   --yes            pula a confirmação do --force (CI)
#
# CapRover (app "rastro"): os clientes internos conectam em srv-captain--rastro-broker e o
# gateway pelo FQDN público, então:
#   scripts/rastro_gen_certs.sh --cert-dir ~/rastro-certs --san DNS:mqtt.exemplo.org \
#     --san DNS:srv-captain--rastro-broker --san DNS:rastro-broker --b64
# A verificação do certificado é SEMPRE ligada nos clientes: o SAN precisa bater com o
# nome usado na conexão.
set -euo pipefail
umask 077

uso() { sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CA_DIR="${HOME}/.local/share/rastro/ca"
CERT_DIR="$REPO_ROOT/deploy/mosquitto/certs"
FORCE=0; YES=0; B64=0
SANS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out-dir) [ $# -ge 2 ] || { echo "ERRO: --out-dir exige um diretório" >&2; exit 2; }; CA_DIR="$2"; shift 2 ;;
    --cert-dir) [ $# -ge 2 ] || { echo "ERRO: --cert-dir exige um diretório" >&2; exit 2; }; CERT_DIR="$2"; shift 2 ;;
    --san)
      [ $# -ge 2 ] || { echo "ERRO: --san exige um valor" >&2; exit 2; }
      if [[ ! "$2" =~ ^(DNS:[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|IP:[0-9A-Fa-f.:]{2,45})$ ]]; then
        echo "ERRO: --san inválido: use DNS:nome ou IP:endereço" >&2; exit 2
      fi
      SANS+=("$2"); shift 2 ;;
    --b64) B64=1; shift ;;
    --force) FORCE=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) uso; exit 0 ;;
    *) echo "ERRO: argumento desconhecido: $1 (veja --help)" >&2; exit 2 ;;
  esac
done

mkdir -p "$CERT_DIR" "$CA_DIR"
chmod 700 "$CA_DIR"
CA_DIR="$(cd "$CA_DIR" && pwd)"; CERT_DIR="$(cd "$CERT_DIR" && pwd)"
# A chave da CA nunca mora num repositório git (commit acidental = CA comprometida).
if git -C "$CA_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERRO: --out-dir ($CA_DIR) está dentro de um repositório git — escolha um diretório fora" >&2
  exit 1
fi

if [ "$FORCE" -eq 1 ]; then
  STAMP="$(date +%s)"
  cp -a -- "$CA_DIR" "$CA_DIR.bak-$STAMP"
  cp -a -- "$CERT_DIR" "$CERT_DIR.bak-$STAMP"
  if [ "$YES" -ne 1 ]; then
    echo "backup: $CA_DIR.bak-$STAMP, $CERT_DIR.bak-$STAMP"
    read -r -p "Isso DESTRÓI a CA atual (backup feito). Digite DESTRUIR: " resposta || {
      echo "" >&2; echo "ERRO: confirmação não recebida — nada destruído" >&2; exit 1
    }
    [ "$resposta" = "DESTRUIR" ] || { echo "ERRO: resposta não é 'DESTRUIR' — nada destruído" >&2; exit 1; }
  fi
  rm -f "$CA_DIR"/ca.key "$CA_DIR"/ca.srl \
        "$CERT_DIR"/ca.crt "$CERT_DIR"/server.crt "$CERT_DIR"/server.key \
        "$CERT_DIR"/server.csr "$CERT_DIR"/server.ext
else
  # Trocar a CA em silêncio quebraria todo cliente que confia nela.
  for f in "$CA_DIR/ca.key" "$CERT_DIR"/ca.crt "$CERT_DIR"/server.crt "$CERT_DIR"/server.key; do
    if [ -e "$f" ]; then
      echo "ERRO: $f já existe — use --force para regenerar (troca a CA e exige redistribuir ca.crt)." >&2
      exit 1
    fi
  done
fi

if [ "${#SANS[@]}" -eq 0 ]; then
  SANS=(DNS:localhost IP:127.0.0.1 DNS:mosquitto)
fi
SAN_LINHA="$(IFS=,; echo "${SANS[*]}")"
CN="localhost"
for s in "${SANS[@]}"; do case "$s" in DNS:*) CN="${s#DNS:}"; break ;; esac; done

# 1) CA local (autoassinada, 3650 dias; SKI explícito — Python 3.13 usa VERIFY_X509_STRICT)
openssl genrsa -out "$CA_DIR/ca.key" 4096 2>/dev/null
openssl req -x509 -new -key "$CA_DIR/ca.key" -sha256 -days 3650 \
  -subj "/O=Rastro/CN=Rastro Broker CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -out "$CERT_DIR/ca.crt"

# 2) Certificado do servidor (825 dias, SAN explícito, SKI+AKI)
openssl genrsa -out "$CERT_DIR/server.key" 4096 2>/dev/null
openssl req -new -key "$CERT_DIR/server.key" -subj "/O=Rastro/CN=${CN}" -out "$CERT_DIR/server.csr"
cat > "$CERT_DIR/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName=${SAN_LINHA}
EOF
openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" \
  -CAserial "$CA_DIR/ca.srl" -CAcreateserial -days 825 -sha256 \
  -extfile "$CERT_DIR/server.ext" -out "$CERT_DIR/server.crt" 2>/dev/null

chmod 600 "$CA_DIR/ca.key" "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
rm -f "$CERT_DIR/server.csr" "$CERT_DIR/server.ext"

echo "== Validade (renovar quando faltarem < 60 dias) =="
openssl x509 -enddate -noout -in "$CERT_DIR/ca.crt"
openssl x509 -enddate -noout -in "$CERT_DIR/server.crt"
echo "SAN do servidor:"
openssl x509 -noout -ext subjectAltName -in "$CERT_DIR/server.crt"
echo "CA key: $CA_DIR/ca.key (NÃO montar no broker; nunca commitar)"

if [ "$B64" -eq 1 ]; then
  echo
  echo "== Valores para o CapRover (cole nos campos do app; não salve em chat/issue) =="
  echo "RASTRO_TLS_CA_B64=$(base64 -w0 < "$CERT_DIR/ca.crt")"
  echo "RASTRO_TLS_SERVER_CRT_B64=$(base64 -w0 < "$CERT_DIR/server.crt")"
  echo "RASTRO_TLS_SERVER_KEY_B64=$(base64 -w0 < "$CERT_DIR/server.key")"
  echo "RASTRO_MQTT_CA_B64=$(base64 -w0 < "$CERT_DIR/ca.crt")"
fi
