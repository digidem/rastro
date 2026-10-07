# AGENTS.md — Rastro

Plataforma autônoma e offline-first para rastreamento de malha LoRa (Meshtastic) voltada ao monitoramento territorial.

---

## 1. Arquitetura do Produto

O repositório `rastro` é o monorepo do produto de rastreamento (imagens públicas `communityfirst/rastro-*` no Docker Hub):

```
                        [Rádio LoRa USB]
                               │
                      services/rastro_gateway
                               │ (MQTT v5 / WebSocket TLS)
                               ▼
                            [Broker] (Mosquitto 2 na porta 9001 / 443)
                               │
                      services/rastro_gateway/ingest
                               │ (psycopg, lotes transacionais, dedupe)
                               ▼
                       [PostgreSQL 14/17]
                       Banco: `rastro` | Schema: `rastro`
                               │
                      services/rastro_api (FastAPI)
                               │
                       web/ (SolidJS Viewer)
```

- **`services/rastro_gateway`**: Ponte serial USB (Python) com fila de spool resiliente em disco e uplink MQTT v5/v3.1.1.
- **`services/rastro_gateway/ingest`**: Ingestor que drena mensagens do broker e grava no PostgreSQL de forma idempotente (`positions_dedupe`).
- **`services/rastro_api`**: API REST FastAPI somente-leitura (`/api/nodes/latest`, `/api/tracks/:id`, proxy seguro de tiles OSM `/api/osm/`).
- **`web/`**: Visualizador tático em SolidJS com MapLibre GL, suporte a PMTiles offline, orientação de rumo geodésico para barcos, SVGs de hardware Meshtastic e painéis flutuantes.
- **`deploy/`**: Templates de Docker Compose, bootstrap idempotente do PostgreSQL e simulação CapRover.

---

## 2. Comandos Essenciais

| Tarefa | Comando |
|---|---|
| Testes unitários do visualizador | `cd web && pnpm test` (Vitest, 132 testes) |
| Servidor mock local (com dados de teste) | `cd web && VITE_HOST=0.0.0.0 pnpm dev:mock` (acessível via LAN/VPN) |
| Lint / Formatação do frontend | `cd web && pnpm biome check` |
| Testes unitários do Gateway / Ingest | `cd services/rastro_gateway && .venv/bin/pytest -q` (111 testes) |
| Simulação de deploy CapRover | `deploy/sim/run.sh <template.yml>` |
| Script de limpeza de banco legado | `python3 scripts/rastro_cleanup_legacy_db.py --dry-run` |

---

## 3. Lições e Decisões Críticas de Arquitetura

1. **Separação Estrita de Bancos de Dados:**
   - O Rastro opera **exclusivamente no banco `rastro`**.
   - O banco específico `mqtt` contendo a tabela `_prisma_migrations` e modelos do protótipo legado `meshtastic-map` (Node/Prisma) não é utilizado pelo Rastro atual e é alvo de limpeza após verificação de procedência e backup preventivo.
   - O endpoint de conexão de manutenção deve ser `/postgres`, nunca bancos de aplicações terceiras (como `/superset_metastore`). Ver [TODO.md](file:///home/luandro/Dev/digidem/rastro/TODO.md).
2. **Auto-Framing de Câmera no Mapa (`fitBounds`):**
   - O enquadramento automático da câmera deve ser desacoplado de ticks reativos do relógio, buscas textuais ou filtros de condição.
   - Em `web/src/InitializeMap.tsx`, o `fitBounds` observa unicamente a lista de nós com fix confirmado (`posicionados = todos.filter(hasConfirmedPosition)`), evitando saltos indesejados de zoom enquanto o usuário navega.
3. **Cálculo de Rumo Geodésico para Embarcações:**
   - Rastreadores veiculares ou de mão frequentemente não enviam o campo de bússola/rumo no fix do GPS.
   - O rumo é computado dinamicamente em `web/src/lib/bearing.ts` via azimute do segmento mais recente com direção definida da trilha histórica do nó, orientando a silhueta SVG do barco no mapa.
4. **Vite em Rede Local / VPN:**
   - Para permitir acesso de testes em dispositivos móveis na mesma Wi-Fi ou via ZeroTier, o servidor mock deve rodar com `VITE_HOST=0.0.0.0`.
5. **Sensibilidade de Dados Territoriais:**
   - Nunca comitar arquivos `.env`, chaves de canais LoRa ou coordenadas geográficas reais de operações de campo.
   - Dados sintéticos de teste usam coordenadas fictícias ou delimitadas com nomes sanitizados (ex.: `FLEET_NODES` em `web/src/fixtures/nodesFixture.ts`).
6. **Resiliência de Uplink e Marca d'Água do Spool (MQTT v5/v3):**
   - No `rastro_gateway`, mensagens no spool em disco nunca avançam o watermark se o PUBACK falhar (código de erro MQTT v5 `>= 128`) ou expirar por timeout.
   - O loop de envio aplica backoff exponencial para garantir entrega *at-least-once* sem perda de pacotes LoRa em oscilações de rede.
   - A sincronização entre threads suporta *early acks* (casos em que o callback de confirmação dispara antes de o método de publicação retornar o identificador da mensagem).
7. **Validação Estrita de Procedência em Manutenção de Banco:**
   - Em servidores PostgreSQL compartilhados, verificar apenas tabelas utilitárias genéricas (ex.: `_prisma_migrations`) é insuficiente. A validação precisa procurar tabelas ou nomes de migração exclusivos da aplicação antes de permitir qualquer exclusão, operando em modo estrito de falha segura (*fail-closed*).
   - Scripts de manutenção não devem excluir papéis/roles de forma automática: papéis requerem verificação de `pg_shdepend` e checagem bidirecional em `pg_auth_members` (`WHERE roleid = '...' OR member = '...'`).
8. **Simulação de WebSockets e TLS com Nginx / CapRover:**
   - Ao reproduzir a terminação TLS e o proxy de WebSockets para o broker Mosquitto em testes locais (`deploy/sim`), o Nginx requer cabeçalhos explícitos `Upgrade` e `Connection`, além de `proxy_ssl_server_name on` para repasse de SNI.
   - Testes automatizados headless exigem injeção da autoridade certificadora (CA) simulada para evitar rejeições de certificado autoassinado.

---

## 4. Tarefas Pendentes
Consulte [TODO.md](file:///home/luandro/Dev/digidem/rastro/TODO.md) para pendências imediatas de infraestrutura, manutenção de banco de dados e diagramas visuais.

---

## 5. Produção com nós em campo — CONTRATO CONGELADO (2026-10-06)

Há rádios reais configurados com o que está abaixo. Mudar qualquer item sem um plano de migração aprovado pelo dono **deixa nós em campo sem conexão ou sem decodificar**, e eles só voltam com visita física/USB. Vale também para `../univaja-lora` (os dois repositórios têm esta mesma regra; mantenha-os iguais).

**Congelado (não alterar de forma silenciosa nem "só para testar"):**
- Derivação da senha MQTT do nó: `base64url(HMAC-SHA256(RASTRO_NATIVE_SECRET, b"node:"+usuario))` sem `=`, truncada em N caracteres (valor atual **30** (limite do firmware: 31 caracteres úteis; a versão anterior de 32 NUNCA funcionou no rádio). É o valor em produção desde a imagem `img-captain-rastro-broker:6`). Usuário = id do nó (`!xxxxxxxx`). Em `broker/derive.py`, `services/rastro_gateway/native/derive.py`, `scripts/rastro_node_credentials.py` e `../univaja-lora/scripts/provision/modeA/rastro_derive.py` o resultado tem de ser idêntico.
- `RASTRO_NATIVE_SECRET` (nunca rotacionar sem plano: muda a senha de TODOS os nós), chave/nome do canal EVU, raiz `univaja/mesh`, porta 8883 TLS, formato dos tópicos `<raiz>/2/e/<canal>/<id>`.
- Id do gateway virtual (`sha256(b"rastro-vgw:"+barco)`), chave do barco `<rio>-<n>` derivada do `long_name`, ACL por nó (cada barco só lê o tópico do próprio gateway virtual; nenhum barco lê o tráfego de outro), `retain` desligado.
- Esquema do banco: só migrações aditivas e idempotentes; nada de `DROP`/`RENAME`/mudança de tipo em tabelas usadas por ingest/api.

**Como mudar mesmo assim:** (1) plano escrito com impacto por nó e caminho de volta; (2) aprovação do dono; (3) testar em simulação (`deploy/sim`) e com um rádio de bancada; (4) compatibilidade dupla quando possível (ex.: aceitar a senha antiga e a nova no broker durante a transição); (5) só então publicar imagem nova e fazer deploy, um app por vez, conferindo `raw_envelopes`/`gateway_status` depois.

**Estado em produção (2026-10-06):** o app `rastro-broker` roda uma imagem construída da fonte pelo CapRover (`broker/captain-definition`), não `communityfirst/rastro-broker:0.8.0` (essa deriva 32 caracteres e **trancaria para fora o barco de Ituí**). **Nunca** faça `deploy-image` do broker em 0.8.0 nem instale pelo template com a tag padrão 0.8.0 até existir a 0.8.1 (todas as imagens com a mesma tag). Retorno seguro: `deploy-image rastro-broker img-captain-rastro-broker:6` (confirme que o CapRover mantém essa imagem).

**Segredo no provisionamento:** `../univaja-lora` lê `UNIVAJA_MQTT_SECRET` de `~/.config/univaja/provision.env` (modo 600) e ele TEM de ser igual a `RASTRO_NATIVE_SECRET` do `rastro-broker`. Conferir/gravar sem imprimir o valor: `python3 scripts/rastro_caprover.py univaja-secret [--execute]` (diferença ⇒ rádio novo recebe senha que o broker recusa).

**Limites de longo prazo:** girar `RASTRO_NATIVE_SECRET` tranca todos os barcos até visita USB (uma senha por usuário, sem aceitação dupla); troca de rádio = novo id: atualizar `RASTRO_NATIVE_NODES` (`scripts/rastro_nodes_from_fleet.py --apply`, reinicia broker e chat); a chave do barco vem do `long_name`.

**Divergência conhecida (RISCO):** o ACL em produção dá a cada barco `read <raiz>/2/e/EVU/+` e `PKI/+` (mais `write <raiz>/2/stat/<id>`). Com 2+ barcos com downlink, o barco A recebe e retransmite os uplinks do B (vazamento entre rios). O próprio rig (`deploy/sim/native_rig_test.py`, checagens 6/7) indica que o ACL estreito (só o gateway virtual do barco + `write stat`) basta; **provar em bancada com um Heltec antes de estreitar**, e estreitar antes de ligar o 2º barco. Até lá as checagens 6/7 e a de retain do rig estão divergentes de propósito.
