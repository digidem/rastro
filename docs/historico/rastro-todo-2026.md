# Histórico do TODO do Rastro (2026)
Arquivo de 2026-10-09: decisões e evidências extraídas do antigo `TODO.md`, removido do repositório.
Pendências abertas ficam no quadro tsk (`tsk list --all`); este arquivo guarda só decisões e evidência.

## Triagem 2026-10-08 — pendências reais

Lista revisada por Opus e executada onde possível. Itens riscados foram descartados.

**Humano (decisão, acesso físico, ou aprovação)**
- [ ] H2 Testes de campo: escritório⇄tripulação, Starlink off/on, 5–6 barcos ao mesmo tempo, TTL.
  - **Achado 2026-10-09 (um barco em teste):** dono relatou que mensagens "vazam para todos" independentemente do barco selecionado.
    - Código do painel: a lista filtra por barco (`ChatPanel.tsx` `mensagensFiltradas`) — exibição parece correta.
    - Causa provável de transmissão: mensagens de saída são enviadas com `to=0xFFFFFFFF` (broadcast) no canal EVU (`chat/outbox.py`). Todo rádio com a chave EVU na faixa recebe, independentemente do gateway virtual. O gateway virtual só escolhe o nó de uplink/downlink; não isola o rádio.
    - Ingest já descarta eco: `ingest/db.py` (passo 6) ignora pacotes cujo remetente é gateway virtual, então a mensagem de saída, quando re-uplinkada por outro barco, não aparece no painel como mensagem do barco.
    - Sobra o rádio: outros rádios com a chave EVU recebem a mensagem no próprio aparelho. Só confirmável em campo com 2+ barcos.
    - **Observado pelo dono (2026-10-09):** ao escolher "cidade" no dropdown "Enviar para", o barco também recebe a mensagem. Confirma o caminho do rádio: broadcast no canal compartilhado.
    - Decisão pendente do dono (não mudar rádio/ACL sem plano, AGENTS.md §5): pausar envios ao barco, manter com aviso, ou desenhar endereçamento privado por barco (exige PKI; fora da v1 pelo TODO).
- [x] H4 Mensagens do chat: histórico eterno (decidido 2026-10-08). Botão de exclusão de mensagens: não necessário (decidido 2026-10-09). Procedimento LGPD só se houver pedido.
- [x] H5 Canal de alerta: decidido 2026-10-09 — só os selos no mapa (📡/🪫), sem push nem e-mail. M3 descartado.
- [x] H6 Regras de reiniciar/desligar aprovadas 2026-10-09: nós solares/fixos só reiniciam; desligar só com tripulação por perto; nunca factory reset remoto. Construção (M2) continua bloqueada pelo caminho de comando pela malha (tarefa 4/5).
- [x] H8 Dump com coordenadas reais apagado em 2026-10-09 (decisão do dono). Pasta `~/rastro-backups/` ficou vazia.
- [x] H1 App iOS: não planejado (decidido 2026-10-09). Certificado TLS confiável para iOS fica fora do escopo por ora.

**Máquina**
- [x] M8 Imagem pública `communityfirst/rastro-broker:0.8.4` (produção, confirmada via `rastro_caprover.py status`): `mosquitto.conf.tmpl`, `aclfile.tmpl`, `entrypoint.sh`, `accounts.py`, `derive.py` são idênticos a `broker/` (verificado 2026-10-08 com `docker create` + `docker cp`, sem executar o contêiner).
- [x] M10 Ramos fail-closed de `provision_node.py` cobertos (`univaja-lora/scripts/provision/modeA/tests/test_provision_node_branches.py`, 30 testes). Cobertura de `provision_node.py` 81%→90% (medida com `coverage run` em venv temporário). Suíte modeA: 259 passam com o venv do gateway (que tem `meshtastic`); sem ele, 101 são pulados.
- [x] M9 Derivação de senha idêntica nas cópias (`broker/derive.py`, `native/derive.py`, `rastro_derive.py`).

**Descartados**: M1 (retenção: histórico eterno, decidido), M4 (alerta preditivo, especulativo), M6 (absorvido em H4), M7 (rotação de chave EVU: trava todos os barcos sem visita USB), H3 (usuários reais de chat: só após o 1º rádio), H13 (nome curto: cosmético, chave vem do `long_name`), H15 (absorvido em H11/H12), H10 (absorvido em H11/H12).

- [x] M14 Testes de ramos do provisionamento: `test_mtool_branches.py` (30) e `test_status_branches.py` (26) em `univaja-lora` (commit `ce3b5a3`, local). Suíte modeA: 315 passam. Revisados por Sonnet e Opus.

## Tarefa 1: Limpeza Segura do Banco Legado `mqtt` — ✅ Concluída em 2026-10-01

> Executada com gate único (token + fingerprint/OIDs + inventário estrito) e `ALTER DATABASE mqtt ALLOW_CONNECTIONS false` antes do DROP. Banco era casco Prisma vazio (`_prisma_migrations`, 0 linhas). Pós-op: `mqtt` ausente, 0 sessões, `rastro` com mesmas tabelas e contagens, papéis `mqtt`/`rastro_*` presentes. Limitação: snapshots before/after e o dump de backup (em `/tmp/rastro_todo`) foram perdidos antes da verificação formal; comparação foi feita contra os valores registrados. Três bancos `prisma_migrate_shadow_db_*` (dono `mqtt`) permanecem.

### Contexto
Na instância do PostgreSQL do Javari, existem atualmente dois bancos relacionados a malhas:
1. **`rastro` (ATIVO - NÃO TOCAR):** Banco de dados oficial do Rastro. Utilizado ativamente pelos serviços `rastro-ingest` e `rastro-api`. Contém as tabelas `nodes`, `positions`, `device_telemetry` e a view `vw_ultima_posicao`, com os papéis dedicados `rastro_owner`, `rastro_ingest`, `rastro_viewer`, `rastro_maint` e `rastro_backup`.
2. **`mqtt` (LEGADO / OBSOLETO - ALVO DE LIMPEZA):** Banco herdado do protótipo anterior (`meshtastic-map`), caracterizado por tabelas criadas pelo Prisma ORM (incluindo `_prisma_migrations`, `nodes`, `map_reports`, etc.). O Rastro atual **não** utiliza esse banco nem o Prisma.

### Instruções de Execução

#### 1.1 Leitura das Credenciais
Localize o arquivo de credenciais adicionado pelo usuário (verifique `.env`, `deploy/.env` ou variáveis de ambiente informadas):
- `PGHOST` (ou `RASTRO_PG_HOST`)
- `PGPORT` (padrão: `5432`)
- `PGUSER` (usuário com privilégios administrativos / `DROP DATABASE`, ex.: `postgres`)
- `PGPASSWORD` (ou `POSTGRES_PASSWORD`)
- `DATABASE_URL` (se fornecido como connection string única, ex.: `postgresql://user:pass@host:5432/postgres`)

#### 1.2 Validações de Segurança Obrigatórias (Pre-Flight Checks)
Antes de executar qualquer comando de alteração ou deleção:
- [ ] **Conexão segura:** Conecte-se especificamente ao banco `postgres` (banco de manutenção padrão), **nunca** conectado diretamente ao banco que será dropado.
- [ ] **Verificação de existência dos bancos:**
  ```sql
  SELECT datname FROM pg_database WHERE datname IN ('rastro', 'mqtt');
  ```
  - Deve confirmar que `mqtt` existe.
  - Se `mqtt` não existir, a tarefa já foi concluída — aborte sem erros.
  - Se `rastro` existir, confirme que seu schema e dados estão operacionais.
- [ ] **Sanity Check do banco `rastro`:**
  Execute no banco `rastro` (usando nomes qualificados pelo schema):
  ```sql
  SELECT count(*) FROM rastro.nodes;
  SELECT count(*) FROM rastro.positions;
  ```
  Certifique-se de que nada que você fará terá como alvo o schema ou o banco `rastro`. Se o banco `rastro` estiver ausente ou inacessível, **interrompa a operação imediatamente**.
- [ ] **Confirmação rigorosa de procedência do banco `mqtt`:**
  Apenas a tabela `_prisma_migrations` não basta (identifica apenas uso de Prisma em geral). Execute no banco `mqtt` a verificação de migrations e tabelas exclusivas do `meshtastic-map`:
  ```sql
  -- Deve conter migrations específicas do meshtastic-map:
  SELECT migration_name FROM _prisma_migrations 
  WHERE migration_name LIKE '%create_nodes_table%'
     OR migration_name LIKE '%mqtt_connection_state%'
     OR migration_name LIKE '%create_positions_table%';

  -- E/ou tabelas específicas do modelo do meshtastic-map:
  SELECT table_name FROM information_schema.tables 
  WHERE table_schema = 'public' 
    AND table_name IN ('map_reports', 'service_envelopes', 'traceroutes');
  ```
  Se essas evidências do `meshtastic-map` não existirem, **aborte imediatamente**: o banco pode pertencer a outra aplicação Prisma.
- [ ] **Verificação de dependências ativas:**
  Verifique se há consumidores ativos antes do drop:
  ```sql
  SELECT pid, usename, client_addr, application_name 
  FROM pg_stat_activity 
  WHERE datname = 'mqtt';
  ```
- [ ] **Backup preventivo do banco `mqtt` (Recomendado):**
  Gere um dump comprimido do banco legado para salvaguarda caso ferramentas cliente estejam disponíveis:
  ```bash
  pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -Fc -d mqtt -f /tmp/mqtt_legacy_backup_$(date +%Y%m%d_%H%M%S).dump
  ```

#### 1.3 Procedimento de Limpeza
Você pode usar o script pronto [scripts/rastro_cleanup_legacy_db.py](file:///home/luandro/Dev/digidem/rastro/scripts/rastro_cleanup_legacy_db.py):
```bash
# 1. Simulação:
python3 scripts/rastro_cleanup_legacy_db.py --dry-run
# 2. Execução:
python3 scripts/rastro_cleanup_legacy_db.py --execute
```

Ou executar manualmente via SQL conectado ao banco `postgres`:
```sql
-- Encerrar conexões ativas com o banco legado
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'mqtt' AND pid <> pg_backend_pid();

-- Dropar o banco legado
DROP DATABASE IF EXISTS mqtt;
```

#### 1.3.1 Verificação de Papéis (NÃO remover automaticamente)
O papel `mqtt` **não** deve ser removido automaticamente pelo script, pois pode possuir dependências, concessões de acesso ou membros em outros contextos. Se desejar avaliar a remoção manual:
```sql
-- 1. Verificar se o papel possui objetos atribuídos ou dependências no cluster (pg_shdepend):
SELECT classid::regclass, objid, deptype
FROM pg_shdepend
WHERE refobjid = 'mqtt'::regrole;

-- 2. Verificar associações bidirecionais de papéis (membros de mqtt ou grupos aos quais mqtt pertence):
SELECT roleid::regrole AS role_name, member::regrole AS member_of
FROM pg_auth_members
WHERE roleid = 'mqtt'::regrole
   OR member = 'mqtt'::regrole;

-- 3. Confirmar que nenhuma aplicação externa ativa utiliza este usuário para autenticação.
-- Apenas se os passos 1, 2 e 3 confirmarem ausência total de dependências:
-- DROP ROLE IF EXISTS mqtt;
```
> [!CAUTION]
> **NUNCA** remova ou altere os papéis: `rastro_owner`, `rastro_ingest`, `rastro_viewer`, `rastro_maint`, `rastro_backup`, `postgres`.

#### 1.4 Validação Pós-Limpeza
- [ ] Confirme que `mqtt` não consta mais na lista: `SELECT datname FROM pg_database WHERE datname = 'mqtt';` (deve retornar 0 linhas).
- [ ] Confirme que `rastro` continua respondendo: `SELECT count(*) FROM rastro.nodes;`.


### Tarefa 4/5 — status (2026-10-05; trecho antigo: código já commitado e em produção 0.8.x, ver «Estado ao fim da sessão» abaixo)

Desenho e interfaces: `docs/native-ingest-design.md`. Novo código: `services/rastro_gateway/native/` (crypto, envelope, service, alerts), `services/rastro_gateway/chat/` (outbox, processo `python -m rastro_gateway.chat`), `deploy/postgres/init/02-native.sql` + `migrate-02-native.sh`, `broker/accounts.py` (contas por nó + ACL gerada), `services/rastro_api/api/chat.py`, painel de chat em `web/src`. Rig: `deploy/sim/native-rig.sh`/`native_rig_test.py` (broker, MQTT v5) e `deploy/sim/native_e2e.sh` (ingest+outbox reais, Postgres descartável).

Feito e provado localmente: decodificação ServiceEnvelope→Position/Telemetry/NodeInfo/Text (PKI opaco); dedupe `(from,id)` entre gateways; `position.time` com horário inválido sinalizado (`time_flag`); replay nunca troca a posição mais nova; idade da posição + trilha quebrada em lacunas; envelope bruto gravado; ack só após commit; reconexão com sessão persistente sem perda; 6 contas simultâneas; ACL por nó gerada (sem `%u`), retain desligado, wildcard `EVU/+` recebe SUBACK e a ACL filtra a entrega (sem vazamento entre 6 barcos); TLS pela porta mapeada; sem fallback de broker; negativas com MQTT v5 (reason code 135); ingest nunca publica; outbox separado (TTL, taxa por barco, id de pacote não reutilizado, sem eco); UI diz «enviado ao gateway do barco», nunca «lido»; alertas (gateway mudo, posição parada, sem fix, móvel ausente, silêncio noturno) como lógica + estado em `alert_state` (ciclo só liga com `RASTRO_ALERTS_ENABLED=1`; entrega de alertas NÃO implementada); logs sem PSK/texto.

> **Versão atual publicada: 0.8.1** (ingest, chat, api, broker, web, pgtools-pg17; template one-click com app `-chat`, contas dos nós derivadas de segredo, seed de gateways virtuais; ingest/chat desligados por padrão). Em produção até 2026-10-05 estavam as 0.7.0.
> **Implantado em 2026-10-05:** imagens `communityfirst/rastro-{ingest,api,broker,web}:0.7.0` (+ `rastro-pgtools:0.7.0-pg17`) no CapRover; migração `02-native.sql` aplicada em produção (7/143/199 intactos). Correção pós-migração: o papel `rastro_ingest` NÃO lê coordenadas — o ciclo de alertas usa a função `node_activity_summary()` (SECURITY DEFINER); a migração revoga grants antigos. O repo `../univaja-lora` ainda usa o nome de canal `EVU` (263 ocorrências em planos/provisionamento): alinhar para `EVU` antes do campo.

**Estado ao fim da sessão de 2026-10-05/06 (tudo em produção, exceto o que está marcado):**
- Produção: `rastro-{ingest,api,broker,web,chat}` em `0.8.1` desde 2026-10-06 (imagens `communityfirst/rastro-*:0.8.1`, `rastro-pgtools:0.8.1-pg17`); migração `02-native.sql` aplicada (+ revogação de leitura de coordenadas do `rastro_ingest`; uso de `node_activity_summary()`); `rastro-setup` apagado (volume `rastro-pgconn` mantido); porta 8883 aberta pelo devops e mapeada no broker; broker com contas dos 5 barcos (`RASTRO_NATIVE_NODES`), chave EVU em ingest e chat, `RASTRO_NATIVE_ENABLED=1` (ingest) e `RASTRO_CHAT_ENABLED=1` (chat); `virtual_gateways`/`boat_devices` semeados (5 barcos: curuca-1, itaquai-1, itui-1, jaquirana-1, medio-javari-1). Provado: chat→broker→ingest em produção (5 NodeInfo virtuais em `raw_envelopes`), contas dos barcos publicam só no próprio tópico (testado de fora na 8883).
- Ferramentas (commitadas em `cdb3a30`): `scripts/rastro_caprover.py` (API CapRover, dry-run por padrão), `scripts/rastro_nodes_from_fleet.py` (gera `RASTRO_NATIVE_NODES` do `../univaja-lora/devices/fleet.json`; `--apply` grava em broker+chat), skill `~/.agents/skills/rastro-caprover/SKILL.md`. Template one-click (`../caprover-one-click-apps`, branch `rastro-0.5.1`: app `-chat`, variáveis nativas, tag 0.8.0) commitado em `aee41cc` (não enviado). CI do `rastro` corrigido localmente em `0c29c8f` (import relativo em `test_chat_seed`; **falta push** para confirmar o CI verde).

**EM PRODUÇÃO COM NÓS EM CAMPO — contrato congelado: ver AGENTS.md §5 (derivação da senha, segredo, canal EVU, tópicos, ACL, banco). Nada disso muda sem plano de migração aprovado.**

- [x] Senha MQTT em 30 caracteres, `retain` + `write stat/<id>`, `captain-definition`: commitados (`867b61b`, `8aef48e`, `659b657`) e enviados; produção roda a imagem `img-captain-rastro-broker:6` construída da fonte. Barco e móvel de Ituí em campo funcionando (286 envelopes/24h do barco).
- [ ] **NUNCA** `deploy-image rastro-broker …:0.8.0` nem instalar pelo template com a tag padrão 0.8.0 (derivam 32 caracteres e trancam o barco). Retorno anterior conhecido: `img-captain-rastro-broker:6` (conferir se a imagem ainda existe no CapRover). Produção atual: `communityfirst/rastro-broker:0.8.4` (conteúdo = `broker/`, verificado 2026-10-08).
- [x] Imagens `0.8.1` (broker, ingest, chat, api, web, `pgtools:0.8.1-pg17`) publicadas no Docker Hub em 2026-10-06 a partir do HEAD `7f187e5` (`scripts/rastro_release.sh`); sim PASS (50); template `caprover-one-click-apps` (branch `rastro-0.5.1`, `2938a2a`) com default 0.8.1 e enviado.
- [x] **Broker de produção trocado em 2026-10-06 ~23:45 (UTC-5)** para `communityfirst/rastro-broker:0.8.1`; o barco de Ituí (`!1ba19a84`) voltou a publicar em ~20 s (envelopes após o deploy), porta 8883 aberta. Retorno, se preciso: `deploy-image rastro-broker img-captain-rastro-broker:6`. ingest, chat, api e web também em 0.8.1 (2026-10-06 ~19:25 UTC-5, um app por vez; barco de Ituí continuou publicando).
- [x] **Estreitar o ACL dos barcos no Broker (Release 0.8.2)**:
  1. ACL estreito aplicado em `broker/accounts.py`: removidas as regras amplas `read EVU/+` e `read PKI/+`. Cada barco lê estritamente o gateway virtual do seu próprio barco (`read EVU/<vgw>`), escreve em `write e/+/<id>` e `write stat/<id>`.
  2. Testes unitários atualizados em `test_broker_accounts.py` e `test_broker_native_env.py` com asserções estritas contra `EVU/+` e `PKI/+` (300 testes passando).
  3. **Prova em bancada concluída com sucesso (2026-10-06)** com Heltec V4 real (`!a35a8024` / `univaja-medio-javari-barco-1`, firmware 2.7.26): rádio conectou via Wi-Fi ao broker, assinou curinga sem erro de SUBACK, recebeu downlink do próprio barco com PUBACK Mid 1 RC:0 e o broker filtrou 100% de mensagens de outros barcos. Rádio restaurado para produção e verificado.
  4. Rig simulado (`deploy/sim/native-rig.sh`): todos os 8 checks verdes (PASS), incluindo isolamento de retain e ausência de vazamento entre 6 barcos simultâneos.
  5. Imagem 0.8.2 pronta para build e publicação (`scripts/rastro_release.sh 0.8.2 --push`).
  6. Deploy: `deploy-image rastro-broker communityfirst/rastro-broker:0.8.2` (retorno: 0.8.1). Observar o barco de Ituí por `raw_envelopes`.
- [x] **Release 0.8.4 e Rollout em Produção (2026-10-07)**:
  1. Correções do frontend: supressão de estilos inline bloqueados pela CSP do Caddy (`3806525`), adiamento da camada de barcos até registro do ícone SVG (`ba1c615`), seleção explícita de OSM no teste de navegador do simulador (`6b5322e`).
  2. Suítes de testes unitários verdes: visualizador web (228 testes), gateway/ingest (300 testes) e API (63 testes).
  3. Gate de simulação CapRover (`deploy/sim/run.sh`): 100% verde (`== F5 SIM: PASS`), incluindo auditoria TruffleHog e teste com navegador headless.
  4. Imagens `0.8.4` publicadas no Docker Hub (`broker`, `ingest`, `chat`, `api`, `web`, `pgtools:0.8.4-pg17`).
  5. Deploy realizado no CapRover (`scripts/rastro_caprover.py deploy-image --execute`) para todos os cinco apps de produção (`rastro-ingest`, `rastro-api`, `rastro-chat`, `rastro-broker`, `rastro`).
  6. Validação em produção: `https://rastro.javari.guardianconnector.net` respondendo HTTP/2 200, `/api/healthz` OK (4.2ms) e sessões ativas no PostgreSQL `rastro`.
  7. Template one-click atualizado para `defaultValue: '0.8.4'` e commits enviados para `origin/main`.
- [x] **Release 0.8.5/0.8.6 — Alertas de Campo + Retenção Eterna (2026-10-08)**:
  1. Motor enxuto: só `gateway_mudo` + `bateria_critica` (nós fixos via `RASTRO_FIXED_NODES`, `<20%` ou `<3,55 V` em `node_power`); `sem_fix`/`posicao_parada`/`movel_ausente`/janela noturna removidos; kinds aposentados limpos por `Db.retire_alert_kinds` (idempotente, preserva histórico). Semântica last-known mantida por design (consulta Opus 5.5; badge mostra a idade da leitura).
  2. Guard preserve-until-plausible: leitura impossível (ex. `101%`/`-0.001 V`) não dispara E não limpa; só leitura plausível saudável limpa (consulta senior gpt-6.1-sol). NaN/Inf = lixo.
  3. Retenção eterna: `purge_expired` e `RASTRO_*_RETENTION_DAYS` removidos (R6 resolvida); só `prune_packet_seen` (dedupe 7 dias, hook).
  4. API `GET /api/alerts` (array autenticado; `last_seen` de `gateway_status.last_uplink`; grant aditivo `gateway_status`→`rastro_viewer` nos 3 scripts de schema).
  5. Viewer: polling + badges 📡/🪫 com idade da leitura; fixtures `dev:test`.
  6. Suítes: gateway 300, API 101, web 233; sim `F5 SIM: PASS`.
  7. Migração aditiva aplicada com backup (`rastro_pre_0.8.5_2026-10-08_154519.dump`) e rehearsal em container descartável (dump do Azure exige role `azure_pg_admin` + DB `OWNER rastro_owner`; ver skill rastro-caprover).
  8. Imagens 0.8.5/0.8.6 publicadas; deploy `api`/`ingest`/`web` 0.8.5 (broker/chat 0.8.4 intocados); `RASTRO_ALERTS_ENABLED=1` + `RASTRO_FIXED_NODES=!f2664e10` no ingest; primeiro ciclo: `gateway_mudo` (medio-javari-barco-1, real) + `bateria_critica` (atalaia-teto, leitura lixo — motiva o guard).
  9. Template `caprover-one-click-apps` default 0.8.5 (`0079106`); 0.8.6 sobe com o guard.
  10. **0.8.7 (2026-10-08/09):** `gateway_mudo` ignora os gateways virtuais do chat (exclusão por `virtual_gateways` em `gateway_uplink_times`; os 6 badges falsos 'Rastro' eram os vgw — silêncio deles é design). Handoff: 6 alertas ativos resolvidos por operação de dados única (chave ausente nunca gera clear no motor). Restaram 2 reais: rádio medio-javari mudo + atalaia-teto (lixo de leitura, guard preserva).
- [x] Decisão do dono R6 (retenção) RESOLVIDA 2026-10-08: histórico eterno — `purge_expired`/`RASTRO_*_RETENTION_DAYS` removidos; `prune_packet_seen` permanece hook (dedupe 7 dias). R9 (acesso ao servidor) segue aberta. Texto do chat também fica para sempre (sem retenção); é sensível, então o acesso é o controle principal.
- [ ] Logs de contêiner não são legíveis pela API do CapRover: diagnóstico = ler logs no painel do CapRover ou olhar o banco (`raw_envelopes`, `gateway_status`).
- [ ] Riscos aceitos/conhecidos: replay antigo com horário inválido cai no fallback «agora» (sinalizado por `time_flag`); `rastro_ingest` também grava `virtual_gateways`/`boat_devices` (tabelas de configuração, sem coordenadas).

## Verificação de premissas e pendências (2026-10-01, plano da frota v7)
- [x] Premissas de firmware/docs verificadas contra o firmware 2.7.26.54e0d8d e as docs 2.7 (`univaja-lora/drafts/verificacao-premissas.md`): o cliente MQTT do nó usa TCP puro (sem WebSocket), não valida certificado TLS e imprime a senha no log; a fila é de 16 mensagens e só 1 entrada é reenviada após reconexão; assina `<raiz>/2/e/<canal>/+` e `<raiz>/2/e/PKI/+`; telemetria do dispositivo exige `device_telemetry_enabled`.
- [x] **Retenção (decidido 2026-10-08):** histórico mantido sem purga (posições e log bruto são eternos). Não há configuração de retenção no app web. Removidos do repo em 2026-10-09 (aprovado pelo dono): `scripts/rastro_retention.py`, `deploy/systemd/rastro-retention.{service,timer}` e referências em Dockerfile/docs. **Pendente no host:** desativar o timer antigo, se ainda estiver instalado: `sudo systemctl disable --now rastro-retention.timer` (não é feito pelo repo).
- [x] Logs de chat e PSK: `chat/outbox.py` e `chat/__main__.py` registram só tipo de exceção e IDs; PSK com `repr=False`, nunca logada (verificado 2026-10-08).
- [x] Logs de erro agora registram só o tipo da exceção: `native/service.py` (sem `log.exception`, sem traceback) e `ingest/db.py` (sem os 120 caracteres da mensagem do banco). Gateway: 301 testes passam (2026-10-08). Implantado em produção em 2026-10-09 como `rastro-ingest:0.8.8` (deploy aprovado pelo dono; `deploy-image` enfileirado, conferir se o mapa segue atualizando).
- [x] Retenção do texto do chat: não há (histórico eterno, decidido 2026-10-08).
- [x] **Humano:** botão de exclusão de mensagens: não necessário (decidido 2026-10-09).
- [x] Alertas de energia: `bateria_critica` ativo para nós fixos. Limiares são padrões no código (`native/alerts.py`: 20 % e 3,55 V), configuráveis por env; não estão setados em produção.

## Parada de barco sob multipath (plano `TASK.md`, 2026-10-09)

- [x] Deploy: aplicar `deploy/postgres/migrate-03-qualidade.sh` (backup automático). A API lê as colunas novas via `to_jsonb` e o ingest detecta colunas por conexão; a ordem é livre, mas reiniciar `rastro-ingest` depois de migrar para ele passar a gravar as colunas. Feito em 2026-10-09 (aprovado pelo dono): ensaio em cópia descartável (duas execuções, contagem igual), migração em produção, colunas anuláveis e grants conferidos (ingest grava `hdop`, não lê `lat_i`, não apaga). Depois `rastro-api`, `rastro-ingest` e `rastro` (web) em `0.8.9`; `F5 SIM: PASS`. Ingest novo conectou às 16:46:10 UTC e gravou posição; `hdop` segue nulo até o item de `position_flags` acima. Template da loja com `$$cap_tag` padrão `0.8.9` (não commitado). Backups em `~/rastro-backups` (modo 600, têm coordenadas reais): apagar quando não precisar mais do retorno.

## M15 — decisão 2026-10-09 (mtool, em univaja-lora)

- [ ] M15 Bugs achados na revisão (em `univaja-lora/scripts/provision/modeA/mtool.py`). Decidido 2026-10-09 (Sonnet analisou, Opus revisou): (a) `reopen()` captura `BaseException` e engole Ctrl-C (~linha 366-369): **correção pendente, em lote no próximo commit do `mtool.py`** (2 linhas + teste; detalhe em `univaja-lora/GAPS.md` §4.4). Não aplicada sozinha porque qualquer edição invalida o hash do T3 (`helper_hash()` hasheia o arquivo inteiro) e bloqueia adminkey em produção até recapturar o T3. Revertida em 2026-10-09. (b) `cmd_showpin()` deixa `/dev/tty` aberto na recusa (~linha 916-927): **não corrigir** (vazamento inofensivo em CLI de tiro único). Opcional: `finally` só para limpar a tela, não aplicado.
