#!/usr/bin/env bash
# Backup noturno Rastro — pg_dump local rotativo (Fase 5; R3: off-site é etapa separada).
#
# - Dump em formato custom (-Fc) só do schema dedicado (-n <db>), em deploy/backups/
#   (gitignored), dir criado 0700. Papel padrão: <db>_backup (SELECT apenas).
# - Restauração: deploy/postgres/bootstrap-existing.sh --restore <arquivo>.
# - Rotação: mantém os 14 dumps mais recentes, apaga os mais velhos.
# - Senha só via ambiente (PGPASSWORD exportado de RASTRO_PG_PASSWORD) — nunca argv.
# - Falha de pg_dump ⇒ mensagem PT-BR e exit != 0 (o .dump parcial é removido).
set -euo pipefail
umask 077  # dump contém trilhas (sensível) — 600 desde a criação (gate F5 NIT)

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="${RASTRO_BACKUP_DIR:-$RAIZ/deploy/backups}"

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERRO: pg_dump não encontrado no PATH" >&2
  exit 1
fi

if [ -z "${RASTRO_PG_PASSWORD:-}" ]; then
  echo "ERRO: RASTRO_PG_PASSWORD não definida" >&2
  exit 1
fi
export PGPASSWORD="$RASTRO_PG_PASSWORD"

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"

ARQ="$DESTINO/rastro-$(date +%Y%m%d-%H%M%S)-$$.dump"

if ! pg_dump \
    -h "${RASTRO_PG_HOST:-127.0.0.1}" \
    -p "${RASTRO_PG_PORT:-5432}" \
    -U "${RASTRO_PG_USER:-${RASTRO_PG_DB:-rastro}_backup}" \
    -d "${RASTRO_PG_DB:-rastro}" \
    -n "${RASTRO_PG_DB:-rastro}" \
    -Fc \
    -f "$ARQ"; then
  rm -f -- "$ARQ"   # remove dump parcial/corrompido para não entrar na rotação
  echo "ERRO: pg_dump falhou — backup NÃO gerado" >&2
  exit 1
fi
# Dump contém trilhas de posição — dado sensível (regra do projeto): acesso só do dono.
chmod 600 "$ARQ"

echo "Backup gerado: $ARQ ($(du -h "$ARQ" | cut -f1))"

# Rotação: mantém os 14 mais recentes (ls -1t, nomes gerados sem espaços).
mapfile -t dumps < <(ls -1t "$DESTINO"/rastro-*.dump 2>/dev/null || true)
if [ "${#dumps[@]}" -gt 14 ]; then
  for velho in "${dumps[@]:14}"; do
    echo "AVISO: removendo backup antigo $velho"
    rm -f -- "$velho"
  done
fi
