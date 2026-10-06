# rastro-broker

Imagem do Mosquitto 2.1 que se configura sozinha na partida, a partir de variáveis de ambiente ou de arquivos montados. Feita para o CapRover (app `rastro`), serve em qualquer orquestrador.

- Só um listener: **8883 com TLS**. Não existe 1883 nem websocket.
- Dois usuários fixos: `gateway` (só publica) e `ingest` (só lê), sob `<prefixo>/positions|telemetry|status/#`.
- Roda como uid 1883, sem capabilities. Senhas são hasheadas na partida (`mosquitto_passwd -U`) e as variáveis com segredos são removidas do ambiente antes de iniciar o broker.

## Variáveis

| Nome | Obrigatória | Descrição |
|---|---|---|
| `RASTRO_MQTT_PASSWORD_GATEWAY` | sim (legado) | senha do usuário `gateway`; mínimo 24 caracteres, sem `:` nem quebra de linha |
| `RASTRO_MQTT_PASSWORD_INGEST` | sim | senha do usuário `ingest`; mesmas regras |
| `RASTRO_MQTT_PASSWORD_OUTBOX` | nativo | senha do usuário `outbox`; mínimo 24 caracteres (obrigatória com `RASTRO_NATIVE_NODES`) |
| `RASTRO_NATIVE_NODES` | não | mapeamento de nós e barcos no formato `!a0000001=b1,!a0000002=b2` para autoconfiguração |
| `RASTRO_NATIVE_SECRET` | não | segredo mestre para derivação determinística de senhas de nós (mínimo 24 caracteres) |
| `RASTRO_NATIVE_ROOT` | não (`univaja/mesh`) | prefixo raiz de tópicos nativos Meshtastic |
| `RASTRO_MQTT_TOPIC_PREFIX` | não (`rastro`) | prefixo dos tópicos legados: `[a-z0-9_-]`, 1–32 caracteres |
| `RASTRO_ACCOUNTS_FILE` | não | caminho do JSON de contas e ACL granular por nó/barco (WP-C; substitui senhas legadas) |
| `RASTRO_TLS_CA_B64`, `RASTRO_TLS_SERVER_CRT_B64`, `RASTRO_TLS_SERVER_KEY_B64` | ver TLS | PEMs em base64 de uma linha |
| `RASTRO_BROKER_DRY_RUN` | não | `1` valida e gera a configuração, imprime `OK: configuração gerada` e sai (testes) |

## Contas e ACL Nativa (WP-C / H2)

O broker suporta duas formas de configuração granular de contas e tópicos nativos:

1. **Via Variáveis de Ambiente (`RASTRO_NATIVE_NODES` e `RASTRO_NATIVE_SECRET`):**
   Adequado para provisionamento no CapRover. O entrypoint deriva automaticamente no boot as credenciais dos nós (via HMAC-SHA256) e os gateways virtuais por barco (`derive.py`), aplicando isolamento de tópicos via `accounts.py`:
   - **`ingest`**: leitura em `<root>/2/e/#` e também nos tópicos legados `<prefixo>/{positions,telemetry,status}/#`.
   - **`outbox`**: escrita nos gateways virtuais `<root>/2/e/EVU/<vgw>` derivados por barco.
   - **Nós (`!id`)**: escrita em `<root>/2/e/+/!id` e leitura no gateway virtual do seu barco (`<root>/2/e/EVU/<vgw>`).
   - **`gateway` (opcional)**: se `RASTRO_MQTT_PASSWORD_GATEWAY` estiver definida, mantém acesso de escrita em `<prefixo>/{positions,telemetry,status}/#`.
   - Senhas em texto puro são hasheadas com `mosquitto_passwd -U` e limpas do ambiente e de arquivos temporários antes do Mosquitto iniciar.

2. **Via Arquivo JSON Montado (`RASTRO_ACCOUNTS_FILE`):**
   Quando aponta para um arquivo JSON (ver `accounts.example.json`), `accounts.py` gera no boot o arquivo de senhas e a ACL nominal com isolamento estrito.

Em ambos os modos nativos:
- `retain_available true` (padrão; `RASTRO_RETAIN_AVAILABLE=false` desliga): o firmware Meshtastic envia um *last will* retido em `<raiz>/2/stat/<id>` e o Mosquitto 2 recusa a conexão se `retain` estiver indisponível. Nenhum serviço do Rastro publica mensagens retidas; nenhum envelope é retido.
- Proibição de `%u`; qualquer tópico ou operação não listada é rejeitada por padrão.


## TLS — exatamente uma fonte completa

1. **Arquivos montados (recomendado):** `ca.crt`, `server.crt` e `server.key` em `/mosquitto/secrets/`, legíveis pelo uid 1883 (ex.: `chown 1883` e `chmod 0400`).
2. **Variáveis B64:** as três juntas; uma ou duas sozinhas é erro. Gere com `scripts/rastro_gen_certs.sh --b64` (usa `base64 -w0`). O CapRover guarda variáveis de ambiente em texto puro no diretório de dados dele e nos backups — por isso a opção 1 é a recomendada.

O certificado do servidor precisa ter no SAN todos os nomes usados para conectar: o domínio público (gateway) e `srv-captain--<app>-broker` (ingest, dentro do CapRover). Os clientes sempre verificam o certificado.

## Volume de dados

`/mosquitto/data` guarda a fila QoS 1 da sessão persistente do ingest (sobrevive a restart). Volume nomeado novo já nasce com o dono certo; volume antigo ou diretório do host precisa pertencer ao uid 1883 — senão o contêiner sai com erro explicando isso.

## Build

```bash
docker build -t rastro-broker broker/
```
