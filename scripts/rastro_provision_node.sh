#!/usr/bin/env bash
# Detecta um nó Meshtastic na USB e o provisiona para a frota (nome, papel,
# intervalo de posição). Toda string de operador em PT-BR; nada de segredo é
# impresso — o PSK do canal nunca é lido nem exportado por este script.
#
# Uso típico (nó cliente móvel):
#   scripts/rastro_provision_node.sh --long-name estacao-mobile-1 --short-name EM1 \
#       --role CLIENT --position-interval 300
set -euo pipefail

CLI="${RASTRO_MESHTESTIC_CLI:-meshtastic}"
PORT=""
WAIT_SECS=0
DETECT_ONLY=0
DRY_RUN=0
ASSUME_YES=0
LONG_NAME=""
SHORT_NAME=""
ROLE=""
BOARD=""
POSITION_INTERVAL=""

usage() {
  cat <<'FIM'
Uso: rastro_provision_node.sh [opções]

Detecta um nó Meshtastic na USB e aplica a configuração. Depois de gravar,
relê as configurações no próprio nó e sai com erro se algo não bateu — por
isso o script pode rodar sem supervisão (ex.: com --wait).

Opções de detecção:
  --port PORT            usa esta porta serial (padrão: autodetectar na USB)
  --wait SEGUNDOS        espera até SEGUNDOS o nó aparecer antes de desistir
  --detect-only          só detecta e informa; não grava nada
  --board MODELO         nome do modelo da placa para o id da frota
                         (ex.: heltec-v3); em USB nativa de ESP32 a placa não
                         pode ser distinguida por VID:PID — informe você

Opções de configuração (tudo opcional; o que não for informado não é tocado):
  --long-name NOME       nome longo do nó (máx. 16 caracteres)
  --short-name SIGLA     nome curto (máx. 4 caracteres)
  --role PAPEL           CLIENT, CLIENT_MUTE, ROUTER, ROUTER_CLIENT, TRACKER...
  --position-interval S  device.position_broadcast_secs em segundos
                         (alinhe com RASTRO_GW_POSITION_INTERVAL_ASSUMED_SECS,
                         (o padrão da ponte é 300 — DEFAULT_POSITION_INTERVAL_SECS
                         em services/rastro_gateway/bridge/gateway.py)

Outras opções:
  --fleet-json ARQUIVO   imprime no stdout um trecho fleet.json para o nó
  --yes                  não pede confirmação antes de gravar
  --dry-run              mostra o que seria feito e não grava
  -h, --help             esta ajuda

Saída: 0 ok · 1 porta indisponível · 2 configuração rejeitada ·
       3 relê e divergiu do pedido · 4 ferramenta ausente
FIM
}

log()  { printf '%s\n' "$*" >&2; }
fail() { log "$1"; exit "${2:-2}"; }

# Duas execuções esperando a mesma placa acordam juntas quando ela aparece: as
# duas abrem a porta e uma grava por cima da outra. O lock é um descritor, então
# é liberado mesmo se o processo for morto sem limpeza.
exec 9>/tmp/rastro_provision.lock || fail "FALHA: não consegui abrir /tmp/rastro_provision.lock"
flock -n 9 || fail "FALHA: já existe uma provisão rodando (/tmp/rastro_provision.lock); mate-a antes de repetir"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)              PORT="${2:?--port exige a porta}"; shift 2 ;;
    --wait)              WAIT_SECS="${2:?--wait exige segundos}"; shift 2 ;;
    --detect-only)       DETECT_ONLY=1; shift ;;
    --dry-run)           DRY_RUN=1; shift ;;
    --yes|-y)            ASSUME_YES=1; shift ;;
    --long-name)         LONG_NAME="${2:?--long-name exige o nome}"; shift 2 ;;
    --short-name)        SHORT_NAME="${2:?--short-name exige a sigla}"; shift 2 ;;
    --role)              ROLE="${2:?--role exige o papel}"; shift 2 ;;
    --board)             BOARD="${2:?--board exige o modelo}"; shift 2 ;;
    --position-interval) POSITION_INTERVAL="${2:?--position-interval exige segundos}"; shift 2 ;;
    --fleet-json)        FLEET_JSON="${2:?--fleet-json exige o arquivo}"; shift 2 ;;
    -h|--help)           usage; exit 0 ;;
    *)                   log "Opção desconhecida: $1"; usage; exit 2 ;;
  esac
done

command -v "$CLI" >/dev/null 2>&1 || fail "FALHA: CLI meshtastic não encontrado (defina RASTRO_MESHTESTIC_CLI)" 4
command -v udevadm >/dev/null 2>&1 || fail "FALHA: udevadm não encontrado; use --port" 4

# Limites do UserConfig do firmware: nome longo 16, nome curto 4.
[[ ${#LONG_NAME} -le 16 ]]  || fail "FALHA: nome longo tem ${#LONG_NAME} caracteres (máximo 16): $LONG_NAME" 2
[[ ${#SHORT_NAME} -le 4 ]]  || fail "FALHA: nome curto tem ${#SHORT_NAME} caracteres (máximo 4): $SHORT_NAME" 2

# --- detecção ---------------------------------------------------------------

# VID:PID conhecidos de placas Meshtastic: a tabela vem do próprio CLI
# (meshtastic.supported_device), então ela acompanha a versão instalada.
# RASTRO_NODE_VIDS acrescenta VID extras separados por espaço, ex.: "0403".
known_usb_ids() {
  local cli_python vidpids extra=()
  read -ra extra <<<"${RASTRO_NODE_VIDS:-}"
  cli_python="$(head -1 "$(command -v "$CLI")" 2>/dev/null | sed -n 's|^#!\(.*\)$|\1|p')"
  if [[ -n "$cli_python" && -x "$cli_python" ]]; then
    vidpids="$("$cli_python" - <<'FIM' 2>/dev/null || true
from meshtastic.supported_device import supported_devices as d
seen = set()
for x in d:
    if x.usb_vendor_id_in_hex:
        print(f"{x.usb_vendor_id_in_hex}:{x.usb_product_id_in_hex or ''}")
        print(x.usb_vendor_id_in_hex)
        seen.add(x.usb_vendor_id_in_hex)
FIM
)"
  fi
  printf '%s\n%s\n' "${vidpids:-}" "${extra[@]}" | sed '/^$/d' | sort -u
}

# Percorre as seriais USB e imprime uma linha por porta: devname|vid:pid|serial|by-id
scan_usb_ports() {
  local dev devname props vid pid serial byid
  for dev in /sys/class/tty/ttyACM* /sys/class/tty/ttyUSB*; do
    [[ -e "$dev" ]] || continue
    devname="/dev/${dev##*/}"
    props="$(udevadm info -q property -p "$dev" 2>/dev/null || true)"
    vid="$(sed -n 's/^ID_VENDOR_ID=//p' <<<"$props" | head -1)"
    pid="$(sed -n 's/^ID_MODEL_ID=//p' <<<"$props" | head -1)"
    serial="$(sed -n 's/^ID_SERIAL_SHORT=//p' <<<"$props" | head -1)"
    byid="$(find /dev/serial/by-id -maxdepth 1 -lname "*${dev##*/}" -printf '%f\n' 2>/dev/null | head -1)"
    printf '%s|%s:%s|%s|%s\n' "$devname" "${vid,,}" "${pid,,}" "$serial" "$byid"
  done
}

pick_port() {
  local ids line dev vidpid rest
  ids="$(known_usb_ids)"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    dev="${line%%|*}"; vidpid="${line#*|}"; vidpid="${vidpid%%|*}"; rest="${line#*|*|}"
    if grep -qxF "$vidpid" <<<"$ids" || grep -qxF "${vidpid%%:*}" <<<"$ids"; then
      printf '%s\n' "$dev"
      return 0
    fi
  done < <(scan_usb_ports)
  return 1
}

if [[ -z "$PORT" ]]; then
  deadline=$(( SECONDS + WAIT_SECS ))
  while :; do
    PORT="$(pick_port)" || PORT=""
    [[ -n "$PORT" ]] && break
    [[ $SECONDS -ge $deadline ]] && break
    sleep 1
  done
  if [[ -z "$PORT" ]]; then
    found="$(scan_usb_ports || true)"
    if [[ -n "$found" ]]; then
      log "FALHA: nenhuma serial reconhecidamente Meshtastic; portas USB vistas:"
      log "$found"
      log "Se for uma placa Meshtastic não listada, use --port /dev/ttyACM0 e/ou RASTRO_NODE_VIDS."
    else
      log "FALHA: nenhuma porta serial USB presente depois de ${WAIT_SECS}s."
      log "Nada enumerou na USB — verifique cabo (muitos são só de energia), porta e se a placa não está desligada/em deep sleep:"
      log "  dmesg | grep -iE 'usb|acm'   # deve mostrar 'new full-speed USB device' ao plugar"
    fi
    exit 1
  fi
  log "Porta detectada: $PORT"
fi

line="$(scan_usb_ports | grep -m1 "^$PORT|" || true)"
if [[ -n "$line" ]]; then
  VIDPID="${line#*|}"; VIDPID="${VIDPID%%|*}"
  SERIAL="${line#*|*|}"; SERIAL="${SERIAL%%|*}"
  BYID="${line##*|}"
else
  VIDPID="desconhecida"; SERIAL=""; BYID=""
fi
log "USB id $VIDPID · serial ${SERIAL:-?}${BYID:+ · by-id /dev/serial/by-id/$BYID}"

# O id da frota é calculado depois do handshake, porque precisa do modelo que o
# próprio nó reporta (antes disso `BOARD` pode estar vazio).

if [[ $DETECT_ONLY -eq 1 ]]; then
  printf '%s\n' "$PORT"
  exit 0
fi

# --- handshake Meshtastic ---------------------------------------------------

# A tabela de USB id diz que é uma placa dessas, não que roda Meshtastic: um
# ESP32-S3 em USB nativa (303a:1001) pode estar com outro firmware. Confirma.
log "Lendo o nó (confirma que é Meshtastic e mostra a versão do firmware)..."
INFO="$("$CLI" --port "$PORT" --timeout 60 --info 2>&1)" || {
  log "FALHA: sem resposta de Meshtastic em $PORT"
  log "$INFO"
  exit 1
}
NODE_NUM="$(grep -oE '![0-9a-fA-F]{8}' <<<"$INFO" | head -1)"
FW="$(sed -n 's/.*"firmwareVersion"[^"]*"\([^"]*\)".*/\1/p
       s/^.*[Ff]irmware version:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' <<<"$INFO" | head -1)"
[[ -n "$NODE_NUM" ]] || { log "FALHA: resposta sem id de nó — não parece um nó Meshtastic"; log "$INFO"; exit 1; }
# O --info do CLI 2.7 imprime JSON ("firmwareVersion", "pioEnv") e não o rótulo
# "Firmware version:" das versões antigas; os dois formatos passam por aqui.
[[ -n "$BOARD" ]] || BOARD="$(sed -n 's/.*"pioEnv"[^"]*"\([a-z0-9._-]*\)".*/\1/p' <<<"$INFO" | head -1)"
log "Nó $NODE_NUM · firmware ${FW:-?} · placa ${BOARD:-?} (--board sobrescreve)"

# Id da frota no padrão do registry: <placa>-<últimos 4 hex do MAC/serial>.
SUFFIX="$(tr -dc '0-9a-fA-F' <<<"${SERIAL:-$PORT}" | tr 'A-F' 'a-f' | tail -c 4)"
FLEET_ID="${BOARD:-placa}-${SUFFIX:-????}"

# PSK e URL do canal NÃO são impressos (docs/SEGURANCA.md: nada de chave em log).
HAS_PSK="$("$CLI" --port "$PORT" --timeout 60 --get channel.psk 2>/dev/null | grep -c 'psk' || true)"
log "Canal: PSK presente=$( (( ${HAS_PSK:-0} > 0 )) && echo sim || echo 'indefinido (sem criptografia)' ) · não impresso por segurança"


# --- aplicar ----------------------------------------------------------------

# O nó reinicia a cada comando gravado: TODAS as mudanças vão numa única
# chamada do CLI (recomendação oficial para User/Device config).
APPLY=()
[[ -n "$LONG_NAME" ]]         && APPLY+=(--set-owner "$LONG_NAME")
[[ -n "$SHORT_NAME" ]]        && APPLY+=(--set-owner-short "$SHORT_NAME")
[[ -n "$ROLE" ]]              && APPLY+=(--set device.role "$ROLE")
[[ -n "$POSITION_INTERVAL" ]] && APPLY+=(--set position.position_broadcast_secs "$POSITION_INTERVAL")
# O campo mora no grupo "position" do Config, não em "device":
# `--set device.position_broadcast_secs` é rejeitado e o CLI imprime a lista de
# nomes válidos no lugar de gravar — sem código de saída de erro.

if [[ ${#APPLY[@]} -eq 0 ]]; then
  # Sem nada a gravar ainda vale registrar o nó na frota: é assim que se cadastra
  # um aparelho já configurado sem reiniciá-lo de novo.
  log "Nenhuma opção de configuração informada — nada será gravado."
else
  log "Vou gravar em $NODE_NUM ($PORT): ${APPLY[*]}"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "ensaio: não gravei nada — $CLI --port $PORT ${APPLY[*]}"
    exit 0
  fi
  if [[ $ASSUME_YES -ne 1 ]]; then
    if [[ ! -t 0 ]]; then
      fail "FALHA: sem terminal para confirmar; releia com --yes para gravar sem perguntar" 2
    fi
    read -r -p "Confirmar gravação em $NODE_NUM? [s/N] " resp </dev/tty
    [[ "$resp" =~ ^[sSyY]$ ]] || { log "cancelado pelo operador"; exit 0; }
  fi

  "$CLI" --port "$PORT" --timeout 90 "${APPLY[@]}" >&2
  # Toda gravação reinicia o nó; sem rede o CLI reabre a porta antes de ele subir.
  sleep 8
fi

# --- reler e conferir -------------------------------------------------------
log "Relendo o nó para conferir o que foi gravado..."
FAILS=()
# Duas sessões separadas: `--info` e `--get` juntos não são documentados como
# combináveis, e uma falha aqui acusaria a gravação de ter falhado.
CHECK="$("$CLI" --port "$PORT" --timeout 60 --info 2>&1 || true)
$("$CLI" --port "$PORT" --timeout 60 --get device.role 2>&1 || true)"
if [[ -n "$LONG_NAME" ]]  && ! grep -qF -- "$LONG_NAME"  <<<"$CHECK"; then FAILS+=("long_name=$LONG_NAME"); fi
if [[ -n "$SHORT_NAME" ]] && ! grep -qF -- "$SHORT_NAME" <<<"$CHECK"; then FAILS+=("short_name=$SHORT_NAME"); fi
if [[ -n "$ROLE" ]]       && ! grep -qiF -- "$ROLE"      <<<"$CHECK"; then FAILS+=("role=$ROLE"); fi
# O intervalo merece verificação própria: foi ele que passou por nome de campo
# errado. --get só imprime campo fora do padrão, então ausência não é prova de
# valor errado — vira aviso, não falha.
if [[ -n "$POSITION_INTERVAL" ]]; then
  POS_NOW="$("$CLI" --port "$PORT" --timeout 60 --get position.position_broadcast_secs 2>&1 || true)"
  POS_GOT="$(grep -oE 'position_broadcast_secs:[[:space:]]*[0-9]+' <<<"$POS_NOW" | grep -oE '[0-9]+$' | head -1)"
  if [[ -n "$POS_GOT" && "$POS_GOT" != "$POSITION_INTERVAL" ]]; then
    FAILS+=("position_broadcast_secs=${POSITION_INTERVAL}s, o nó respondeu ${POS_GOT}s")
  elif [[ -z "$POS_GOT" ]]; then
    log "AVISO: o nó não ecoou position_broadcast_secs (valor padrão do firmware ou leitura falhou); confira com --get position"
  fi
fi
if [[ ${#FAILS[@]} -gt 0 ]]; then
  log "FALHA: o nó não confirmou: ${FAILS[*]}"
  log "--- resposta do nó ---"
  log "$CHECK"
  exit 3
fi
log "OK: nó confirmado pelo próprio readback."

# --- registro da frota ------------------------------------------------------
if [[ -n "${FLEET_JSON:-}" ]]; then
  HEX="${NODE_NUM#!}"
  NODE_DEC=$(( 16#$HEX ))
  LONG_OUT="${LONG_NAME:-$(sed -n 's/^.*Owner:[[:space:]]\{1,\}\(.*\)$/\1/p' <<<"$CHECK" | head -1)}"
  LONG_OUT="${LONG_OUT%% (*}"; LONG_OUT="${LONG_OUT#\{}"; LONG_OUT="${LONG_OUT%\}}"
  SHORT_OUT="${SHORT_NAME:-$(grep -oE '\([A-Za-z0-9 -]{1,4}\)' <<<"$CHECK" | head -1 | tr -d '()')}"
  cat >"$FLEET_JSON" <<FIM
{"schema_version": 1,
 "devices": [{"id": "$FLEET_ID",
              "identity": {"node_num": $NODE_DEC,
                           "long_name": "$LONG_OUT",
                           "short_name": "$SHORT_OUT"}}]}
FIM
  log "Escrito $FLEET_ID em $FLEET_JSON (aponte RASTRO_FLEET_NAMES_FILE para ele)"
fi

NOME_FINAL="$LONG_NAME"
if [[ -z "$NOME_FINAL" ]]; then
  NOME_FINAL="$(sed -n 's/^.*Owner:[[:space:]]\{1,\}\(.*\)$/\1/p' <<<"$CHECK" | head -1)"
fi
log "Resumo: $NODE_NUM nome=${NOME_FINAL:-?} papel=${ROLE:-inalterado} porta=$PORT id_da_frota=$FLEET_ID"

