#!/usr/bin/env bash
# Rastro — gera CA local + certificado TLS do broker mosquitto.
# Saída:
#   deploy/ca/ca.key                       (chave PRIVADA da CA — fora da árvore montada no broker; 0700/600)
#   deploy/mosquitto/certs/ca.crt          (certificado da CA — clientes e broker confiam nele)
#   deploy/mosquitto/certs/server.{crt,key}
# Validade: CA 3650 dias; servidor 825 dias.
# SAN: localhost, 127.0.0.1, hostname curto e FQDN, e "mosquitto" (nome do serviço compose —
# o ingester conecta por esse hostname com verificação de certificado SEMPRE LIGADA —
# não há como desligá-la, o SAN precisa bater).
# Uso: scripts/rastro_gen_certs.sh [--force] [--yes]
#   --force regenera tudo do zero; copia deploy/ca e deploy/mosquitto/certs para
#           <dir>.bak-<epoch> (gitignored) ANTES de apagar, e exige confirmação
#           digitada DESTRUIR — --yes pula a confirmação (CI).
# Após gerar/regerar: docker compose -f deploy/docker-compose.yml restart mosquitto
set -euo pipefail
umask 077

FORCE=0
YES=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --yes) YES=1 ;;
    *) echo "uso: $0 [--force] [--yes]" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="$REPO_ROOT/deploy/mosquitto/certs"
CA_DIR="$REPO_ROOT/deploy/ca"
mkdir -p "$CERT_DIR" "$CA_DIR"
chmod 700 "$CA_DIR"

if [ "$FORCE" -eq 1 ]; then
  # Backup ANTES de destruir (cp -a cria irmãos <dir>.bak-<epoch> — ver .gitignore):
  STAMP="$(date +%s)"
  [ -d "$CA_DIR" ] && cp -a -- "$CA_DIR" "$CA_DIR.bak-$STAMP"
  [ -d "$CERT_DIR" ] && cp -a -- "$CERT_DIR" "$CERT_DIR.bak-$STAMP"
  if [ "$YES" -ne 1 ]; then
    # Confirmação digitada: trocar a CA é destrutivo (clientes precisam do novo ca.crt).
    # Padrão digitado: "DESTRUIR". Sem --yes, aborta em qualquer outra resposta.
    echo "backup: $CA_DIR.bak-$STAMP, $CERT_DIR.bak-$STAMP"
    read -r -p "Isso DESTRÓI a CA atual (backup será feito). Digite DESTRUIR: " resposta || {
      echo "" >&2; echo "ERRO: confirmação não recebida — nada destruído" >&2; exit 1
    }
    if [ "$resposta" != "DESTRUIR" ]; then
      echo "ERRO: resposta não é 'DESTRUIR' — nada destruído" >&2
      exit 1
    fi
  fi
  rm -f "$CA_DIR"/ca.key \
        "$CERT_DIR"/ca.crt "$CERT_DIR"/server.crt "$CERT_DIR"/server.key \
        "$CERT_DIR"/server.csr "$CERT_DIR"/server.ext "$CERT_DIR"/ca.srl
else
  # Guarda completa: TROCAR A CA EM SILÊNCIO quebraria todo cliente que confia nela.
  # Qualquer material pré-existente sem --force = abortar.
  for f in "$CA_DIR/ca.key" "$CERT_DIR"/ca.crt "$CERT_DIR"/server.crt "$CERT_DIR"/server.key; do
    if [ -e "$f" ]; then
      echo "ERRO: $f já existe — use --force para regenerar (isso troca a CA e exige redistribuir ca.crt)." >&2
      exit 1
    fi
  done
fi

HOSTNAME_S="$(hostname -s)"
HOSTNAME_F="$(hostname -f 2>/dev/null || echo "$HOSTNAME_S")"

# 1) CA local (autoassinada, 3650 dias; SKI explícito — Python 3.13 usa VERIFY_X509_STRICT)
openssl genrsa -out "$CA_DIR/ca.key" 4096
openssl req -x509 -new -key "$CA_DIR/ca.key" -sha256 -days 3650 \
  -subj "/C=BR/O=Rastro/CN=Rastro Broker CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -out "$CERT_DIR/ca.crt"

# 2) Certificado do servidor (825 dias, SAN completo, SKI+AKI explícitos)
openssl genrsa -out "$CERT_DIR/server.key" 4096
openssl req -new -key "$CERT_DIR/server.key" \
  -subj "/C=BR/O=Rastro/CN=localhost" -out "$CERT_DIR/server.csr"
cat > "$CERT_DIR/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:mosquitto,DNS:${HOSTNAME_S},DNS:${HOSTNAME_F}
EOF
openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" \
  -CAcreateserial -days 825 -sha256 -extfile "$CERT_DIR/server.ext" -out "$CERT_DIR/server.crt"

chmod 600 "$CA_DIR/ca.key" "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
rm -f "$CERT_DIR/server.csr" "$CERT_DIR/server.ext"

echo "== Validade (renovar quando faltarem < 60 dias) =="
openssl x509 -enddate -noout -in "$CERT_DIR/ca.crt"
openssl x509 -enddate -noout -in "$CERT_DIR/server.crt"
echo "SAN do servidor:"
openssl x509 -noout -ext subjectAltName -in "$CERT_DIR/server.crt"
echo "CA key: $CA_DIR/ca.key (NÃO montar no broker; nunca commitar)"
