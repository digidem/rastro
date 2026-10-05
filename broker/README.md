# rastro-broker

Imagem do Mosquitto 2.1 que se configura sozinha na partida, a partir de variáveis de ambiente ou de arquivos montados. Feita para o CapRover (app `rastro`), serve em qualquer orquestrador.

- Só um listener: **8883 com TLS**. Não existe 1883 nem websocket.
- Dois usuários fixos: `gateway` (só publica) e `ingest` (só lê), sob `<prefixo>/positions|telemetry|status/#`.
- Roda como uid 1883, sem capabilities. Senhas são hasheadas na partida (`mosquitto_passwd -U`) e as variáveis com segredos são removidas do ambiente antes de iniciar o broker.

## Variáveis

| Nome | Obrigatória | Descrição |
|---|---|---|
| `RASTRO_MQTT_PASSWORD_GATEWAY` | sim (legado) | senha do usuário `gateway`; mínimo 24 caracteres, sem `:` nem quebra de linha |
| `RASTRO_MQTT_PASSWORD_INGEST` | sim (legado) | senha do usuário `ingest`; mesmas regras |
| `RASTRO_MQTT_TOPIC_PREFIX` | não (`rastro`) | prefixo dos tópicos: `[a-z0-9_-]`, 1–32 caracteres |
| `RASTRO_ACCOUNTS_FILE` | não | caminho do JSON de contas e ACL granular por nó/barco (WP-C; substitui senhas legadas) |
| `RASTRO_TLS_CA_B64`, `RASTRO_TLS_SERVER_CRT_B64`, `RASTRO_TLS_SERVER_KEY_B64` | ver TLS | PEMs em base64 de uma linha |
| `RASTRO_BROKER_DRY_RUN` | não | `1` valida e gera a configuração, imprime `OK: configuração gerada` e sai (testes) |

## Contas e ACL Nativa (WP-C)

Quando `RASTRO_ACCOUNTS_FILE` aponta para um arquivo JSON (ver `accounts.example.json`), `accounts.py` gera no boot o arquivo de senhas e a ACL nominal com isolamento estrito:

- **`ingest`**: somente leitura em `<root>/2/e/#` (sem escrita).
- **`outbox`**: somente escrita nos tópicos dos gateways virtuais `<root>/2/e/EVU/<vgw>` (sem leitura).
- **Nós (`!id`)**: somente escrita em `<root>/2/e/+/!id` e somente leitura no gateway virtual do respectivo barco (`<root>/2/e/EVU/<vgw>`), sem vazamento entre embarcações.
- `retain_available false` ativado para evitar replay de mensagens retidas a novos assinantes.
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
