# Provisão de nós

Como preparar um rádio Meshtastic novo para entrar na frota e aparecer no mapa.
Feito uma vez por aparelho, na bancada, com o nó na USB do notebook.

## 1. Nome e papel

Duas convenções convivem na frota. Os nós fixos em produção usam
`<LOCAL>_CAMPO_<NN>` em maiúsculas — `LOCAL_A_CAMPO_01`, `LOCAL_B_CAMPO_01`,
`LOCAL_C_CAMPO_01` — com nome curto de três letras, dígito e `M` (`LA1M`,
`LB1M`, `LC1M`), e a base é `Central_Base`. Nó móvel novo entra como
`<frota>-<papel>-<n>` — `estacao-mobile-1` — porque não tem local fixo. O nome é
o que aparece no visualizador quando não há `fleet.json`; o `id` da frota (abaixo)
é o que aparece em `nodes.fleet_id`.

| Papel | Quando usar | Observação |
|---|---|---|
| `CLIENT` | nó móvel (barco, veículo, pessoa) | padrão; retransmite o mesh e aceita mensagem direta |
| `CLIENT_MUTE` | móvel que não deve retransmitir | economiza tempo no ar do mesh |
| `ROUTER` / `ROUTER_CLIENT` | repetidor fixo, alimentado | já nasce `is_unmessageable` |
| `TRACKER` | móvel que só interessa pela posição | já nasce `is_unmessageable`; a posição sai por movimento |

Limites do `UserConfig` do firmware: **nome longo 16 caracteres, nome curto 4**.
`estacao-mobile-1` tem exatamente 16 — cabe, sem margem para sufixo. Na CLI 2.7
os nomes são gravados com `--set-owner` / `--set-owner-short`; **não existe
`--set-longname`** (confirme com `meshtastic --help | grep owner`).

## 2. Provisão

```bash
scripts/rastro_provision_node.sh \
  --long-name estacao-mobile-1 --role CLIENT --position-interval 300 \
  --fleet-json ~/.local/share/rastro/fleet.json
```

Com o nó na USB é só isso: a porta é autodetectada, o modelo da placa vem do
próprio nó e o `id` da frota sai `heltec-v4-aaaa`. `--short-name` ficou de fora
de propósito — sem ele o firmware deriva o nome curto do nome longo sozinho
(`estacao-mobile-1` virou `EM1M`), que pode não ser o da sua convenção; passe
`--short-name AT1M` se quiser controlá-lo.

O script detecta a porta pela tabela de USB id do próprio CLI (acompanha a
versão instalada), confirma que a placa responde como Meshtastic antes de
gravar, e **relê o nó depois de gravar** — se o valor não bateu, sai com código
3 e mostra a resposta crua. Sem terminal (execução em segundo plano) exige
`--yes`. `--dry-run` não grava; `--detect-only` só informa a porta. `--wait 1800`
fica esperando o nó aparecer na USB — útil quando o aparelho ainda está sem
cabo/bateria.

Todas as mudanças vão **numa única chamada** ao CLI: o nó reinicia a cada
comando gravado, e dois comandos separados significam duas sessões de reboot no
meio da provisão.

O que o script **não** toca: canal, PSK, região LoRa, posição fixa. Aparelho
que já está em produção pode ter tudo isso configurado; sobrescrever em bancada
é a forma clássica de perder um nó de vista.

## 3. Canal e PSK (fora do script, de propósito)

Nó com o canal errado não aparece no mapa de jeito nenhum: ele conversa com uma
malha que não é a sua. Não há PSK no repositório (`docs/SEGURANCA.md` §4 — nada
de chave em arquivo versionado, issue ou chat). Copie do nó que já está no ar:

```bash
# nó que já funciona (ex.: o da base) → arquivo com a URL do canal
meshtastic --port /dev/serial/by-id/... --export-config /tmp/canal.yaml
# nó novo
meshtastic --port /dev/serial/by-id/... --configure /tmp/canal.yaml
rm -P /tmp/canal.yaml   # o arquivo contém o PSK em claro
```

`--export-config` grava o PSK em claro: não deixe o arquivo em diretório
versionado, não anexe em issue, apague com `rm -P` ao terminar. Alternativa sem
arquivo: a URL do canal (`meshtastic --ch-set url` / QR do app) digitada à mão.


## 4. Intervalo de posição

A ponte emite `AVISO: <nó> sem posição há Ns (esperado ~Ns)` usando
`RASTRO_GW_POSITION_INTERVAL_ASSUMED_SECS` (padrão **300 s** —
`DEFAULT_POSITION_INTERVAL_SECS` em `services/rastro_gateway/bridge/gateway.py`)
e o mínimo aceito é 30 s. Um nó que fala em 900 s vira ruído no log da ponte; um
nó que fala em 30 s inunda o ar. Alinhe os dois lados:

```bash
meshtastic --port /dev/serial/by-id/... --get position.position_broadcast_secs
scripts/rastro_provision_node.sh --port ... --position-interval 300 --yes
```

O `position.` do nome não é decorativo: os grupos do `Config` são `device`,
`position`, `power`, `network`, `display`, `lora`, `bluetooth`, `security`, e um
nome **errado** (`device.position_broadcast_secs`) não faz o CLI falhar — ele
imprime a lista de nomes aceitos e sai com código 0 sem gravar nada. Por isso o
script confere o valor relendo o nó, e por isso `--get <grupo>` (ex.: `--get
position`) é a forma de descobrir o nome certo: ele lista os campos fora do padrão.

Dois campos mexem na cadência sozinhos e fazem o aviso da ponte mentir:
`position.position_broadcast_smart_enabled` (transmite por movimento, não por
tempo) e `power.is_power_saving`/`power.ls_secs` (o nó dorme entre transmissões).

## 5. Registro da frota

`--fleet-json` escreve o trecho no formato do registry
(`services/rastro_gateway/common/fleet_names.py`):

```json
{"schema_version": 1,
 "devices": [{"id": "heltec-v4-aaaa",
              "identity": {"node_num": 2863311530,
                           "long_name": "estacao-mobile-1",
                           "short_name": "EM1M"}}]}
```

- `id` = `<placa>-<últimos 4 hex do MAC>` — é o que aparece em `nodes.fleet_id`.
- `node_num` é inteiro; o `!hex` do app é o mesmo número em hexadecimal.
- Sem o arquivo, o nó aparece como `!hexid`. O caminho vem de
  `RASTRO_FLEET_NAMES_FILE`; nada de PSK ou coordenada aqui.
- Em USB nativa de ESP32 (`303a:1001`) **não dá para saber a placa pelo
  VID:PID** — todas as placas ESP32‑S3 compartilham esse id. O script lê o modelo
  do próprio nó (`pioEnv` do `--info`, ex. `heltec-v4`); `--board` só serve para
  sobrescrever quando o nó não souber responder.

## 6. No host da ponte

```bash
# /etc/rastro/gateway.env
RASTRO_GW_SERIAL_PORT=/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:DD:EE:FF-if00
```

O caminho exato aparece em `ls /dev/serial/by-id/` com o nó na USB — copie de lá,
não digite o MAC de cabeça nem de outro aparelho.

Sempre o caminho `/dev/serial/by-id/...`, nunca `/dev/ttyACM0`: a numeração
troca a cada reboot/hibernação e a ponte (`Restart=always`) ficaria presa a
uma porta que agora é outro aparelho. O usuário `rastro` precisa estar em
`dialout`; a unit já abre `DeviceAllow=char-ttyACM rw` e `char-ttyUSB rw`.

## 7. Verificação

| Passo | Como |
|---|---|
| O nó respondeu o que foi gravado | o próprio `rastro_provision_node.sh` (relê e sai 3 se divergiu) |
| O nó vê a malha | `meshtastic --port ... --nodes` (aparecem os vizinhos, não só ele) |
| A ponte recebe | `journalctl -u rastro-gateway -f` → pacotes do nó, sem `sem posição há` |
| Fim a fim | `RASTRO_E2E_NODES="!xxxxxxxx" python scripts/rastro_e2e_check.py` |

## 8. Triagem quando a placa não aparece na USB

| Sintoma | Causa provável | Como confirmar |
|---|---|---|
| Nada no `dmesg` ao plugar | cabo **só de energia**, porta morta, placa desligada, ou hibernação profunda | `journalctl -k --since -2min \| grep -i usb` — plugar tem que gerar `new full-speed USB device` |
| Enumerou, sem `/dev/ttyACM0` | placa em modo boot/DFU, ou falta de driver (`cdc_acm`) | `lsusb -d <vid>:<pid>` responde; `ls /dev/ttyACM*` vazio |
| `/dev/ttyACM0` existe e some em seguida | USB nativa de ESP32: o USB‑Serial‑JTAG cai quando o nó reinicia ou hiberna | `journalctl -k \| grep -i 'USB disconnect'`; é limpo (sem erro) → não é defeito de cabo |
| Porta presente, CLI diz `File Not Found` | a porta fechou entre a listagem e a abertura (reboot do nó no meio) | rode de novo; se repetir, o nó está reinando em loop |
| Abre, mas sem resposta Meshtastic | outro firmware na placa | o `--info` do script responde sem id de nó |

A hibernação é a causa traiçoeira: o aparelho está **fisicamente plugado e
carregando**, mas sem o USB‑Serial‑JTAG vivo o host não vê porta nenhuma.
Replugue o cabo (ou segure o reset) antes de concluir que a placa morreu.
