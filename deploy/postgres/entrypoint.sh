#!/usr/bin/env bash
# Despachante da imagem rastro-pgtools:
#   (sem argumentos) | --restore <dump> | -h  → bootstrap-existing.sh (uso manual, compatível)
#   setup-service                              → setup-service.sh (app "<app>-setup" no CapRover)
# Também aceita RASTRO_PGTOOLS_MODE=setup-service (sem argumentos): o template CapRover usa
# variável de ambiente para não depender de como o CapRover repassa `command` ao ENTRYPOINT.
set -euo pipefail
[ $# -gt 0 ] || [ "${RASTRO_PGTOOLS_MODE:-}" != setup-service ] || set -- setup-service
case "${1:-}" in
  setup-service) shift; exec bash /rastro/setup-service.sh "$@" ;;
  ""|-*)         exec bash /rastro/bootstrap-existing.sh "$@" ;;
  *) echo "ERRO: comando desconhecido: $1 (use: setup-service | --restore <dump> | sem argumentos)" >&2; exit 2 ;;
esac
