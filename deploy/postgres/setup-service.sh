#!/usr/bin/env bash
# Rastro — serviço de preparo do PostgreSQL existente, para rodar como o app
# "<app>-setup" do template CapRover. Roda o bootstrap UMA vez e depois fica parado
# (sleep infinito) — nunca fica em laço contra o banco e não é reiniciado em loop pela
# política de restart do CapRover. Para repetir: "Save & Restart" no app.
#
# Entradas (ambiente):
#   RASTRO_PG_ADMIN_URL                                conexão de administração numa URL só:
#       postgresql://usuario:senha@host:5432/banco?sslmode=require
#       (usuário/senha com percent-encoding; o caminho é o banco ao qual o admin conecta,
#       padrão "postgres"; só o parâmetro sslmode é aceito). Tem precedência sobre:
#   RASTRO_PG_ADMIN_HOST / _PORT / _USER / _PASSWORD   (forma antiga, com a URL vazia)
#   RASTRO_DB                                          nome do banco (= prefixo dos papéis)
#   RASTRO_PG_PASSWORD_{INGEST,VIEWER,MAINT,BACKUP}    senhas dos papéis (>= 24 caracteres)
#   RASTRO_PGCONN_DIR                                  volume onde publicar conn.env (padrão /rastro-pgconn)
#
# Saída: após o OK, grava <RASTRO_PGCONN_DIR>/conn.env com SÓ host, porta e sslmode (sem
# usuário nem senha) — ingest e API leem dali; as credenciais de admin ficam só neste app.
#
# Segurança para um Postgres de PRODUÇÃO: timeouts de lock/statement/transação ociosa
# (nunca trava outras cargas), só cria banco/schema/papéis dedicados <db>*, recusa
# objetos de outras instalações (checagens do bootstrap-existing.sh), não apaga nada,
# é idempotente. A senha de admin fica no ambiente deste app: APAGUE o app depois.
set -euo pipefail
umask 077

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${RASTRO_SETUP_APP:-<app>-setup}"

parar() {  # $1 = mensagem final; sempre fica ocioso para o restart do CapRover não reexecutar
  echo "$1"
  exec sleep infinity
}
falha() { parar "ERRO: $1${2:+
DICA: $2}"; }

executar() {
  local v n
  RASTRO_DB="${RASTRO_DB:-rastro}"
  [[ "$RASTRO_DB" =~ ^[a-z][a-z0-9_]{0,30}$ ]] || { echo "ERRO: RASTRO_DB inválido (use ^[a-z][a-z0-9_]{0,30}\$)"; return 1; }
  local ph pp pu pw pdb psm
  if [ -n "${RASTRO_PG_ADMIN_URL:-}" ]; then
    # python3 lê a URL do AMBIENTE (nunca argv) e devolve atribuições já citadas; em erro
    # diz só o MOTIVO, jamais o valor da URL.
    local analisado
    analisado="$(python3 - <<'PY'
import os, shlex, sys
from urllib.parse import urlsplit, unquote, parse_qsl

def erro(motivo):
    print("ERRO: RASTRO_PG_ADMIN_URL inválida (%s). Formato: postgresql://usuario:senha@host:5432/banco?sslmode=require "
          "(caracteres especiais da senha em percent-encoding, ex.: @ = %%40, : = %%3A, / = %%2F, %% = %%25, # = %%23)" % motivo)
    sys.exit(1)

bruto = os.environ["RASTRO_PG_ADMIN_URL"].strip()
try:
    u = urlsplit(bruto)
    porta = u.port
    host = u.hostname
except ValueError:
    erro("não foi possível interpretar host/porta")
if u.scheme not in ("postgres", "postgresql"):
    erro("o esquema deve ser postgresql:// ou postgres://")
if u.fragment:
    erro("há '#' sem percent-encoding (use %23 na senha)")
if not host:
    erro("host ausente")
if not u.username:
    erro("usuário ausente")
if not u.password:
    erro("senha ausente")
porta = 5432 if porta is None else porta
if not 1 <= porta <= 65535:
    erro("porta fora do intervalo")
usuario, senha = unquote(u.username), unquote(u.password)
banco = unquote(u.path.lstrip("/")) or os.environ.get("RASTRO_ADMIN_DB") or "postgres"
if "/" in banco:
    erro("nome de banco inválido no caminho")
sslmode = ""
try:
    pares = parse_qsl(u.query, keep_blank_values=True, strict_parsing=bool(u.query))
except ValueError:
    erro("parâmetros ilegíveis")
for k, v in pares:
    if k != "sslmode":
        erro("só o parâmetro sslmode é aceito")
    if v not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        erro("sslmode deve ser disable, allow, prefer, require, verify-ca ou verify-full")
    sslmode = v
for valor in (host, usuario, senha, banco):
    if any(ord(c) < 32 or ord(c) == 127 for c in valor):
        erro("caracteres de controle não são permitidos")
for nome, valor in (("ph", host), ("pp", str(porta)), ("pu", usuario), ("pw", senha), ("pdb", banco), ("psm", sslmode)):
    print("%s=%s" % (nome, shlex.quote(valor)))
PY
)" || { echo "${analisado:-ERRO: RASTRO_PG_ADMIN_URL inválida}"; return 1; }
    eval "$analisado"; analisado=""
  else
    for n in RASTRO_PG_ADMIN_HOST RASTRO_PG_ADMIN_USER RASTRO_PG_ADMIN_PASSWORD; do
      [ -n "${!n:-}" ] || { echo "ERRO: defina RASTRO_PG_ADMIN_URL (ou, na forma antiga, $n)"; return 1; }
    done
    [[ "${RASTRO_PG_ADMIN_PORT:-5432}" =~ ^[0-9]{1,5}$ ]] || { echo "ERRO: RASTRO_PG_ADMIN_PORT inválida"; return 1; }
    ph="$RASTRO_PG_ADMIN_HOST"; pp="${RASTRO_PG_ADMIN_PORT:-5432}"; pu="$RASTRO_PG_ADMIN_USER"
    pw="$RASTRO_PG_ADMIN_PASSWORD"; pdb="${RASTRO_ADMIN_DB:-postgres}"; psm=""
  fi
  for n in RASTRO_PG_PASSWORD_INGEST RASTRO_PG_PASSWORD_VIEWER RASTRO_PG_PASSWORD_MAINT RASTRO_PG_PASSWORD_BACKUP; do
    v="${!n:-}"
    [ "${#v}" -ge 24 ] || { echo "ERRO: $n ausente ou com menos de 24 caracteres"; return 1; }
  done

  export PGHOST="$ph" PGPORT="$pp" PGUSER="$pu" PGPASSWORD="$pw" \
         PGCONNECT_TIMEOUT=10 PGAPPNAME=rastro-setup \
         PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s -c idle_in_transaction_session_timeout=60s"
  [ -z "$psm" ] || export PGSSLMODE="$psm"
  export RASTRO_DB RASTRO_ADMIN_DB="$pdb"
  # a URL/senha de admin não vazam para os filhos por outro caminho que não PG*
  unset RASTRO_PG_ADMIN_URL RASTRO_PG_ADMIN_PASSWORD
  CONN_HOST="$ph"; CONN_PORT="$pp"; CONN_SSLMODE="$psm"

  # --- preflight: o Postgres pode estar subindo; 12 tentativas x 10 s ---------------------
  local tentativa num="" saida=""
  for tentativa in $(seq 1 12); do
    if saida="$(psql -X -q -At -d "$RASTRO_ADMIN_DB" -c 'SHOW server_version_num' 2>&1)"; then
      num="$saida"; break
    fi
    echo "Aguardando o PostgreSQL em $PGHOST:$PGPORT ($tentativa/12): $(head -1 <<<"$saida")"
    # senha/usuário errados não se resolvem esperando: falha na hora
    case "$saida" in *"authentication failed"*|*"no pg_hba.conf entry"*|*"does not exist"*) break ;; esac
    [ "$tentativa" = 12 ] || sleep 10
  done
  [ -n "$num" ] || { echo "ERRO: não consegui conectar em $PGHOST:$PGPORT como '$PGUSER' (senha errada, host/porta errados ou banco parado)"; return 2; }
  [[ "$num" =~ ^[0-9]+$ ]] || { echo "ERRO: versão do servidor ilegível"; return 1; }
  local major=$(( num / 10000 ))
  echo "PostgreSQL $major (server_version_num=$num) em $PGHOST:$PGPORT"
  [ "$major" -ge 14 ] || { echo "ERRO: PostgreSQL $major não suportado (mínimo 14)"; return 1; }

  cat <<PLANO
== PLANO (nada fora disto será tocado)
   - banco:   $RASTRO_DB (dedicado)
   - schema:  $RASTRO_DB (dentro desse banco)
   - papéis:  ${RASTRO_DB}_owner, ${RASTRO_DB}_ingest, ${RASTRO_DB}_viewer, ${RASTRO_DB}_maint, ${RASTRO_DB}_backup
   Nenhum outro banco, tabela ou papel será criado, alterado ou apagado. Se algum destes
   nomes já pertencer a outra coisa, o preparo RECUSA e para. Nada é apagado (DROP).
   Repetir é seguro: as senhas dos papéis são reaplicadas (sincronizadas com este app).
PLANO
  bash "$AQUI/bootstrap-existing.sh" || return $?
  publicar_conexao
}

# Publica host/porta/sslmode (NADA de usuário/senha) no volume compartilhado com ingest e API.
publicar_conexao() {
  local dir="${RASTRO_PGCONN_DIR:-/rastro-pgconn}" tmp
  if [ ! -d "$dir" ]; then
    echo "AVISO: $dir não existe — conexão não publicada (ingest/API precisarão de RASTRO_PG_HOST)"
    return 0
  fi
  tmp="$(mktemp "$dir/.conn.env.XXXXXX")" || { echo "ERRO: não consegui escrever em $dir"; return 1; }
  {
    printf 'RASTRO_PG_HOST=%s\n' "$CONN_HOST"
    printf 'RASTRO_PG_PORT=%s\n' "$CONN_PORT"
    printf 'RASTRO_PG_SSLMODE=%s\n' "$CONN_SSLMODE"
  } >"$tmp" && chmod 0644 "$tmp" && mv -f "$tmp" "$dir/conn.env" \
    || { rm -f "$tmp"; echo "ERRO: não consegui gravar $dir/conn.env"; return 1; }
  echo "Conexão publicada em $dir/conn.env (RASTRO_PG_HOST, RASTRO_PG_PORT, RASTRO_PG_SSLMODE; sem usuário/senha)"
}

set +e
executar
RC=$?
set -e
if [ "$RC" = 0 ]; then
  parar "OK: PostgreSQL preparado para o Rastro (banco '$RASTRO_DB') — agora APAGUE o app ${APP} (ele guarda a senha de admin do Postgres)"
elif [ "$RC" = 2 ]; then
  falha "não foi possível concluir o preparo." "confira a URL de admin (host, porta, usuário, senha, banco e ?sslmode=) do Postgres; depois corrija as variáveis do app e use Save & Restart."
else
  falha "o preparo foi interrompido (nada foi apagado)." "leia a mensagem acima; corrija as variáveis do app ${APP} e use Save & Restart. Se o erro for de privilégio, use na URL um usuário com CREATEROLE+CREATEDB (ex.: o superusuário do Postgres). O bootstrap é idempotente."
fi
