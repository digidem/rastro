#!/usr/bin/env bash
# Rastro — serviço de preparo do PostgreSQL existente, para rodar como o app
# "<app>-setup" do template CapRover. Roda o bootstrap UMA vez e depois fica parado
# (sleep infinito) — nunca fica em laço contra o banco e não é reiniciado em loop pela
# política de restart do CapRover. Para repetir: "Save & Restart" no app.
#
# Entradas (ambiente):
#   RASTRO_PG_ADMIN_HOST / _PORT / _USER / _PASSWORD   conexão de administração
#   RASTRO_DB                                          nome do banco (= prefixo dos papéis)
#   RASTRO_PG_PASSWORD_{INGEST,VIEWER,MAINT,BACKUP}    senhas dos papéis (>= 24 caracteres)
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
  for n in RASTRO_PG_ADMIN_HOST RASTRO_PG_ADMIN_USER RASTRO_PG_ADMIN_PASSWORD; do
    [ -n "${!n:-}" ] || { echo "ERRO: $n não definida"; return 1; }
  done
  for n in RASTRO_PG_PASSWORD_INGEST RASTRO_PG_PASSWORD_VIEWER RASTRO_PG_PASSWORD_MAINT RASTRO_PG_PASSWORD_BACKUP; do
    v="${!n:-}"
    [ "${#v}" -ge 24 ] || { echo "ERRO: $n ausente ou com menos de 24 caracteres"; return 1; }
  done
  [[ "${RASTRO_PG_ADMIN_PORT:-5432}" =~ ^[0-9]{1,5}$ ]] || { echo "ERRO: RASTRO_PG_ADMIN_PORT inválida"; return 1; }

  export PGHOST="$RASTRO_PG_ADMIN_HOST" PGPORT="${RASTRO_PG_ADMIN_PORT:-5432}" \
         PGUSER="$RASTRO_PG_ADMIN_USER" PGPASSWORD="$RASTRO_PG_ADMIN_PASSWORD" \
         PGCONNECT_TIMEOUT=10 PGAPPNAME=rastro-setup \
         PGOPTIONS="-c lock_timeout=5s -c statement_timeout=120s -c idle_in_transaction_session_timeout=60s"
  export RASTRO_DB

  # --- preflight: o Postgres pode estar subindo; 12 tentativas x 10 s ---------------------
  local tentativa num="" saida=""
  for tentativa in $(seq 1 12); do
    if saida="$(psql -X -q -At -d "${RASTRO_ADMIN_DB:-postgres}" -c 'SHOW server_version_num' 2>&1)"; then
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
  bash "$AQUI/bootstrap-existing.sh"
}

set +e
executar
RC=$?
set -e
if [ "$RC" = 0 ]; then
  parar "OK: PostgreSQL preparado para o Rastro (banco '$RASTRO_DB') — agora APAGUE o app ${APP} (ele guarda a senha de admin do Postgres)"
elif [ "$RC" = 2 ]; then
  falha "não foi possível concluir o preparo." "confira host, porta, usuário e senha de admin do Postgres; depois corrija as variáveis do app e use Save & Restart."
else
  falha "o preparo foi interrompido (nada foi apagado)." "leia a mensagem acima; corrija as variáveis do app ${APP} e use Save & Restart. O bootstrap é idempotente."
fi
