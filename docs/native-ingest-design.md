# Ingest nativo Meshtastic + chat — desenho de implementação (Tarefas 4 e 5)

Fonte de verdade dos requisitos: `TODO.md` (Tarefas 4/5). Este arquivo fixa interfaces e
responsabilidades de arquivos para implementação em paralelo. Sem produção, sem rádio: tudo
testável localmente (pytest + rig Docker). Dados de teste: PSK descartável e nomes fake.

## 0. Regras que valem para todo código

- Nunca logar payload decriptado, chave/PSK, senha, texto do chat, coordenadas. Logar tópico, ids, contagens.
- A chave EVU (`RASTRO_EVU_PSK_B64`, base64 de 16 ou 32 bytes; 1 byte = índice de PSK padrão) só existe
  nos processos `ingest` e `outbox`. Nunca em fixtures versionadas; nos testes use `TEST_PSK = bytes(range(32))`.
- O ingest NUNCA publica no broker (conta `ingest` só leitura). Outbox é outro processo/conta (somente escrita).
- Strings voltadas ao usuário em português; identificadores em inglês.
- Retenção (R6) e acesso ao servidor (R9) são decisões abertas: NÃO implementar política de retenção. Só hooks:
  `RASTRO_RAW_RETENTION_DAYS` e `RASTRO_CHAT_RETENTION_DAYS` (inteiros, vazio = sem limite) lidos por uma função
  `purge_expired(db, now)` que existe, é testada, mas NÃO é agendada por nada.
- Sem commit. Não tocar em arquivos fora da lista do seu pacote de trabalho (WP).

## 1. Protocolo (firmware 2.7.26)

- Tópico de uplink: `<root>/2/e/<canal>/<gateway_id>`; `<root>` default `univaja/mesh` (`RASTRO_NATIVE_ROOT`);
  `gateway_id` = `!` + 8 hex (id do nó que subiu). Canal `PKI` = opaco (não decifrar, só contar/registrar bruto).
- Payload = protobuf `ServiceEnvelope{packet: MeshPacket, channel_id, gateway_id}` (`meshtastic.protobuf.mqtt_pb2`).
- `MeshPacket` criptografado: campo `encrypted` (bytes). Cifra: AES-CTR, chave = PSK (16 ou 32 bytes).
  PSK de 1 byte N: chave padrão `d4f1bb3a20290759f0bcffabcf4e6901` com último byte `+ (N-1)` (N=0 → sem cifra, rejeitar).
  Nonce de 16 bytes: `id` como uint64 little-endian (8 B) + `from` como uint32 LE (4 B) + 4 bytes zero.
  Plaintext = `Data` serializado. Pacote já `decoded` (sem cifra) é aceito como está.
- `Data.portnum`: 1 = TEXT_MESSAGE_APP (UTF-8), 3 = POSITION_APP (`Position`), 4 = NODEINFO_APP (`User`),
  67 = TELEMETRY_APP (`Telemetry`, só `device_metrics`), demais = `unhandled` (conta, não grava domínio; bruto sim).
- Horário: `Position.time` (epoch s, relógio do dispositivo). Inválido se `0`, `< 1_600_000_000`, ou `> agora + 300 s`.
  Fallback sinalizado: `MeshPacket.rx_time` se válido (janela [agora−7d, agora+300s]) senão `agora` (chegada); marca
  `time_flag` = `invalid_zero|invalid_past|invalid_future` e `time_source='gateway'`.
- Dedupe: chave `(from, id)` do MeshPacket, entre gateways. Segunda ocorrência = descartada do domínio (bruto é gravado
  com `duplicate=true`).
- Gateway (quem subiu) ≠ remetente (`from`). Sempre guardar os dois.
- Chat de saída: `MeshPacket{from=<virtual node num>, to=0xFFFFFFFF, id=<aleatório 32 bits, nunca reutilizado>,
  channel=<hash do canal>, hop_limit=3, want_ack=False, encrypted=AES-CTR(Data{portnum=1, payload=texto utf-8})}` dentro de
  `ServiceEnvelope{channel_id="EVU", gateway_id=<virtual gw id>}`, publicado em
  `<root>/2/e/EVU/<virtual gw id>`, QoS 1, retain=False. Hash do canal = XOR de todos os bytes do nome do canal ("EVU")
  XOR todos os bytes da chave **expandida** (a mesma usada na cifra).
- NodeInfo do remetente virtual: `Data{portnum=4, payload=User{id="!<hex>", long_name="Rastro", short_name="RSTR",
  hw_model=PRIVATE_HW}}` embrulhado do mesmo jeito; publicado ao ativar um gateway virtual e a cada
  `RASTRO_NODEINFO_REPUBLISH_SECS` (default 21600).
- Alerta de ajuda no texto recebido: `is_alert` = contém `\x07` (sino do Meshtastic) OU casa (sem acento, caixa baixa)
  com `RASTRO_CHAT_HELP_KEYWORDS` (default `ajuda,socorro,sos,help,emergencia,urgente`).

## 2. Pacotes de trabalho e dono de arquivos

Layout novo: `services/rastro_gateway/native/` (pacote), testes em `services/rastro_gateway/tests/`. Adicionar
`rastro_gateway.native` e `rastro_gateway.chat` em `packages` do `pyproject.toml` (+ dep `cryptography>=42`; já instalada no .venv).

| WP | Dono de | Conteúdo |
|----|---------|----------|
| A | `native/crypto.py`, `native/envelope.py`, `tests/test_native_crypto.py`, `tests/test_native_envelope.py` | decodificação pura (sem I/O) |
| B | `deploy/postgres/init/02-native.sql`, `deploy/postgres/migrate-02-native.sh`, `ingest/db.py` (só acrescentar métodos), `tests/test_native_db.py` | tabelas + gravação |
| C | `broker/*` (Dockerfile, entrypoint.sh, mosquitto.conf.tmpl, accounts.py, accounts.example.json), `deploy/sim/native-rig.sh`, `deploy/sim/native_rig_test.py` | contas/ACL/TLS + rig |
| D | `native/alerts.py`, `native/service.py`, `ingest/mqtt_in.py` e `ingest/__main__.py` (integrar), `tests/test_native_*.py` | ingest ao vivo |
| E | `chat/outbox.py`, `chat/__main__.py`, `tests/test_chat_*.py` | publicação de chat |
| F | `services/rastro_api/api/chat.py`, `main.py` (registrar), `web/src/**chat**`, testes | API + UI |

Interfaces compartilhadas: `native/model.py` (já escrito; não alterar sem avisar). Tipos de saída da decodificação:
`DecodedEnvelope` e variantes (`PositionFix`, `TelemetryFix`, `NodeInfo`, `TextMessage`, `Opaque`).

### WP-A — API pública exigida
```python
# crypto.py
def expand_psk(psk: bytes) -> bytes            # 1 byte -> chave padrão ajustada; 16/32 -> igual; senão ValueError
def channel_hash(channel_name: str, key: bytes) -> int
def crypt(key: bytes, packet_id: int, from_num: int, data: bytes) -> bytes   # CTR é simétrico
# envelope.py
def parse_topic(root: str, topic: str) -> tuple[str, str] | None             # (channel, gateway_id) ou None
def decode_envelope(payload: bytes, *, channel: str, gateway_id_topic: str, psk: bytes, now: float) -> DecodedEnvelope
def build_text_envelope(...)                     # NÃO é do WP-A (é WP-E); WP-A só expõe helpers de crypto/hash
```
`decode_envelope` nunca levanta: erro → `DecodedEnvelope(kind="malformed", reason=...)`. Pacote com `pki_encrypted` ou
canal `PKI` → `Opaque`. Cifra com chave errada produz protobuf inválido → `kind="undecryptable"`.

### WP-B — esquema (`02-native.sql`, aditivo e idempotente: `IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`)
- `positions` += `packet_id BIGINT`, `gateway_num BIGINT`, `time_flag TEXT`, `observed_at TIMESTAMPTZ`.
- `raw_envelopes(id, received_at, topic, channel, gateway_num, from_num, packet_id, duplicate BOOL, raw BYTEA)`; índice por `received_at`.
- `packet_seen(from_num BIGINT, packet_id BIGINT, first_gateway BIGINT, first_seen TIMESTAMPTZ, PRIMARY KEY(from_num,packet_id))`.
- `gateway_status(gateway_num PK, last_uplink TIMESTAMPTZ, uplinks BIGINT)`.
- `node_info(node_num PK, long_name, short_name, hw_model, updated_at)`.
- `chat_messages(id, direction CHECK in ('in','out'), boat_id TEXT, from_num, packet_id, text TEXT, is_alert BOOL, observed_at, received_at, UNIQUE(from_num,packet_id))`.
- `chat_outbox(id, boat_id TEXT, text TEXT, created_by TEXT, created_at, expires_at, status CHECK in ('queued','sent','expired','failed'), sent_at, packet_id BIGINT, error TEXT)`.
- `virtual_gateways(boat_id PK, gateway_id TEXT UNIQUE, virtual_node_num BIGINT, active BOOL)`; `boat_devices(node_num, boat_id, valid_from, valid_to)`.
- `node_power(node_num PK, battery_level, voltage, updated_at)` (hook p/ alertas de energia — só gravar, sem alerta ainda).
- `alert_state(key PK, node_num, kind, since, last_notified, cleared_at)`.
- Grants: papéis `rastro_ingest` (INSERT/SELECT/UPDATE nas novas tabelas), `rastro_viewer` (SELECT em `chat_messages`, `chat_outbox`, `alert_state`, `virtual_gateways`; INSERT em `chat_outbox` via função/grant de coluna), um papel novo NÃO é criado. Conferir como `01-schema.sql` trata grants (provavelmente no entrypoint).
- Métodos novos em `Db`: `store_native(decoded_list) -> counts` (idempotente, uma transação, mesma disciplina de ack que `store_batch`), `purge_expired(now)`, `claim_outbox(limit)`, `mark_outbox(id, status, packet_id=None, error=None)`.

### WP-C — broker
- Mantém listener TCP/TLS 8883 + WebSocket opcional. `retain` efetivamente desligado: `retain_available false`.
- Entrypoint: se `RASTRO_ACCOUNTS_FILE` (JSON, montado, fora do repo) existe, `broker/accounts.py` gera `passwd` (mosquitto_passwd
  -U sobre cópia temporária) e `aclfile` completos; sem o arquivo, comportamento antigo (`gateway`/`ingest`).
  Regerar a cada boot = idempotente; troca de senha = editar arquivo + reiniciar (ou SIGHUP).
- `accounts.example.json` (dados fake): `{"root":"univaja/mesh","ingest":{"password":"..."},"outbox":{"password":"..."},
  "gateway":{...legado...},"nodes":[{"user":"!a0000001","password":"...","boat":"b1"}...6],
  "virtual_gateways":[{"boat":"b1","gateway_id":"!f0000001"}...6]}`.
- ACL gerada: ingest `read <root>/2/e/#` (sem write); outbox `write <root>/2/e/EVU/<vgw>` para cada vgw (sem read);
  nó `!id`: `write <root>/2/e/+/!id` e `read <root>/2/e/EVU/<vgw do seu barco>`; nenhum `%u`; negar tudo o mais.
- Teste obrigatório (rig, MQTT **v5**, não 3.1.1): para cada conta, publicação permitida entregue; publicação negada recebe
  reason code 135 (not authorized) no PUBACK; subscribe `<root>/2/e/EVU/+` do nó recebe SUBACK e SÓ recebe mensagens
  do vgw do seu barco (6 barcos, sem vazamento); 6 contas simultâneas; ingest não consegue publicar; cliente sem conta
  recusado; handshake TLS pela porta mapeada (`docker -p 18883:8883`) com CA do broker; cliente sem CA falha (sem fallback
  para broker público: o teste confere que o host/porta configurados são os únicos contatados).

### WP-D — ingest vivo
- `native/service.py`: `NativeIngest` assina `<root>/2/e/#` (QoS 1, sessão persistente, ack só após commit — mesma disciplina
  de `Ingester`). Para cada mensagem: grava bruto (retenção via hook), `decode_envelope`, dedupe `(from,id)`, grava domínio.
  Reconexão/replay: mensagem redeliverada ou replay antigo nunca troca a posição mais nova (garantido por `pos_time`; ordenar a view).
- `native/alerts.py` (funções puras + persistência em `alert_state`): `gateway_mudo`, `posicao_parada`, `sem_fix`,
  `movel_ausente`, com faixa de silêncio noturno configurável (`RASTRO_QUIET_START`/`RASTRO_QUIET_END`, HH:MM, fuso
  `RASTRO_TZ`) em que `gateway_mudo`/`movel_ausente` não disparam. Só avaliar e registrar em `alert_state`; entrega
  (push/e-mail) fora de escopo.
- Feature flag: `RASTRO_NATIVE_ENABLED=1` liga a assinatura nativa no `__main__` sem alterar o caminho legado.

### WP-E — outbox
- `chat/outbox.py`: `build_text_packet(...)`, `build_nodeinfo_envelope(...)`, `Outbox.run_once()`: `claim_outbox` → publica →
  `mark_outbox('sent')` (apenas após PUBACK ok) ; expirados viram `expired` sem publicar; taxa limitada
  (`RASTRO_OUTBOX_RATE_PER_MIN` default 6 por barco); `id` de pacote nunca reutilizado (aleatório + conjunto recente + checagem
  `chat_messages`); dedupe por `(from,id)`. Barco sem gateway virtual ativo → `failed` ("sem gateway virtual"). Processo
  separado: `python -m rastro_gateway.chat`, conta `outbox`, TLS, MQTT v5 (para ver reason codes). Nenhum caminho do ingest
  importa `chat.outbox`.

### WP-F — API/UI
- `GET /api/chat/messages?since=` (auth), `POST /api/chat/send {boat, text}` (auth; texto ≤ 200 bytes; insere em `chat_outbox`
  com TTL `RASTRO_CHAT_TTL_SECS` default 900), `GET /api/chat/boats`. UI: painel de chat; alerta sonoro+visual para `is_alert`;
  estado "enviado ao gateway do barco" / "expirada (não entregue)" — nunca "lido". `GET /api/nodes/latest` passa a trazer
  `age_s`, `time_flag`; trilha quebra em lacunas > `RASTRO_TRACK_GAP_SECS` (default 1800) — `GET /api/nodes/{n}/track`
  devolve múltiplos LineStrings por padrão (MultiLineString somente com ?format=multi).
