# TODO — Rastro

## Visão Geral (Status)

| # | Tarefa | Status | Bloqueio | Prioridade |
|---|--------|--------|----------|------------|
| 1 | Limpeza segura do banco legado `mqtt` | ✅ Concluída (2026-10-01) | `DROP DATABASE mqtt` executado; `rastro` intacto (7 nós / 143 posições). Script agora é inspetor read-only (DROP removido); 3 shadow DBs `prisma_migrate_shadow_db_*` dropados (2026-10-01, vazios). Papel `mqtt` REMOVIDO (2026-10-01, a pedido do dono; origem: protótipo `meshtastic-map` criado por ele, sem uso): REVOKE ALL das 8 tabelas de `warehouse.public` + ACL padrão do `cmiadmin` (só entradas do `mqtt`; demais ACLs idênticas, verificado por diff) e `DROP ROLE mqtt` | 🔴 Alta |
| 2 | Conexão admin: `/superset_metastore` → `/postgres` | ✅ Concluída (2026-10-01) | `RASTRO_PG_ADMIN_URL` do app `rastro-setup` e `DB=` do `.env` agora apontam para `/postgres`; `superset_metastore` sem tabelas do Rastro. Considerar apagar `rastro-setup` (docs/OPERACAO-caprover.md §3) | 🔴 Alta |
| 3 | Versionar logo oficial e descartar diagramas PNG obsoletos | ✅ Concluída (2026-10-06) | `docs/rastro_logo.svg` comprimido com SVGO (-39.1%) e commitado; PNGs legados `rastro_flow*.png` descartados | 🟡 Baixa |
| 4 | Ingest nativo Meshtastic (ServiceEnvelope) dos nós Nó Solar do Barco | 🟡 Imagens 0.7.0 implantadas (ingest/api/broker/web) + migração 02 aplicada em produção (2026-10-05); ingest nativo DESLIGADO (RASTRO_NATIVE_ENABLED não definido) | Pendências do dono: checklist em «Tarefa 4 — status» (porta 8883/firewall Azure, certificado real, contas reais, PSK EVU, migração 02 em produção) | 🔴 Alta |
| 5 | Chat no mapa: escritório ⇄ tripulação via MQTT (EVU downlink) | 🟡 Imagens 0.7.0 implantadas; chat sem uso até criar o app `rastro-chat` (imagem rastro-ingest:0.7.0, comando `python -m rastro_gateway.chat`) e o broker com contas; testes de campo pendentes | Mesmas pendências da tarefa 4 + conta `outbox` + testes de campo com humanos | 🔴 Alta |
| 6 | Ações remotas por nó no mapa: **reiniciar** e **desligar** | 📝 Planejado | Botão por nó no visualizador (requer autenticação admin). Regras: nós **solares/fixed = somente reiniciar** (desligar é irreversível por rádio — ninguém no local para apertar o botão); desligar oferecido só em nós com tripulação por perto (morto até religarem fisicamente); **nunca** factory-reset remoto; transporte = AdminMessage PKC pela malha (depende do caminho de comando da tarefa 4/5 — hoje inexistente; guardrails no repo `univaja-lora`, skill `remote-management` §3.3) | 🔴 Alta |
| 7 | Apagar o app `rastro-setup` no CapRover | ✅ Concluída (2026-10-05, a pedido do dono; volumes preservados) | Mantém a senha de admin do PostgreSQL em texto no ambiente (docs/OPERACAO-caprover.md §3). Só recriar se precisar reprovisionar | 🟠 Média |
| 8 | Commitar arquivos pendentes (`AGENTS.md`, `TODO.md`, `scripts/rastro_cleanup_legacy_db.py`, `.gitignore`, `.agents/`) | ⏳ Aguardando | Requer aprovação do usuário para commit | 🟡 Baixa |
| 9 | Provisionamento USB (`tsk` thread `provisao-usb`, #8–#11): commitar script/doc/skill; verificar ingestão de `atalaia-mobile-1` ponta a ponta; escolher nome curto; exercitar ramos não testados do script | ⏳ Aberto | Ver `tsk list --all` | 🟠 Média |
| 10 | Web App: Fixtures e ambiente local com dados reais do servidor | ✅ Concluída (2026-10-06) | Proxy no `vite.config.ts` apontando para `:8080`, `web/.env.development` com token, fixtures atualizados com baterias 101%, nós > 7d e `age_s` | 🔴 Alta |
| 11 | Web App: Detecção de nós de barco pelo nome ("barco") e ícone de embarcação | ✅ Concluída (2026-10-06) | Substring "barco" (case-insensitive/normalizado) determina `boat`, usando `boat.svg` no mapa (rotacionado pelo azimute) e na lista | 🔴 Alta |
| 12 | Web App: Ordenação da lista por último visto e filtro de nós > 7 dias com toggle | ✅ Concluída (2026-10-06) | Ordenar decrescente por `posTime`/`last_seen`, esconder por padrão nós inativos (> 7 dias) com botão toggle para exibir | 🔴 Alta |
| 13 | Web App: Exibição do modelo de hardware e imagem SVG na lista de nós (apenas na sidebar) | ✅ Concluída (2026-10-06) | Mostrar nome/badge do hardware do nó e SVG exclusivo na sidebar (barco/hardware); mapa sem SVG de hardware | 🔴 Alta |
| 14 | Web App: Trilhas contínuas e suaves para nós de barco deselecionados | ✅ Concluída (2026-10-06) | Trilhas coletivas suaves de barcos quando deselecionado; ao selecionar um nó, esconde todos os outros caminhos | 🔴 Alta |
| 15 | Web App: Botão de camadas com Satélite como mapa padrão | ✅ Concluída (2026-10-06) | Seletor de camada no mapa (Satélite como default, OSM e PMTiles vetorial local) com persistência em localStorage | 🔴 Alta |
| 16 | Web App: Navegação responsiva mobile em 2 colunas com 2 linhas e ícones | ✅ Concluída (2026-10-06) | Controles mobile em 2 colunas (extremas esquerda e direita) com 2 linhas de botões (apenas ícones), menus acessíveis e auto-fechamento | 🔴 Alta |
| 17 | Web App: Clique fora no mapa desseleciona nós e fecha popups | ✅ Concluída (2026-10-06) | Ao clicar em área livre do mapa, `LocalState.select(null)` e remoção de popups; trilhas coletivas restauradas | 🔴 Alta |
| 18 | Web App: Revisão da detecção de barcos e inferência de nós não-embarcações (`movel`, `teto`, `cartao`) | ✅ Concluída (2026-10-06) | Rejeição estrita de barcos para rádios portáteis/fixos; inferência de hardware e desambiguação de pins sobrepostos no mapa | 🔴 Alta |
| 19 | Web App: Algoritmo de barco parado/ancorado (Dwell & Anchor Detection) contra trilhas falsas | ✅ Concluída (2026-10-06) | Algoritmo ST-DAH implementado em `lib/dwell.ts` para suprimir hairballs e estabilizar azimute no mapa | 🔴 Alta |
| 20 | Web App: Logo animado como Loader e Favicon (`docs/rastro_logo.svg`) | ✅ Concluída (2026-10-06) | Loader animado SolidJS (`LogoLoader.tsx`) com radar/pulso e `web/public/favicon.svg` com fundo transparente | 🟡 Média |

> **Arquivos ainda não monitorados** (verificados via `git status`): `.agents/`, `deploy/sim.nonexistent_placeholder`.

---

Este documento orienta o próximo agente a executar as tarefas de manutenção na instância PostgreSQL do Javari (produção / CapRover) assim que o usuário disponibilizar o arquivo `.env` com as credenciais administrativas, além da tarefa de versionamento dos diagramas.

---

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

---

## Tarefa 2: Transição do Banco de Conexão Administrativa de `/superset_metastore` para `/postgres` — ✅ Concluída em 2026-10-01

### Contexto
Ao configurar a conexão administrativa inicial do PostgreSQL (seja no formulário do CapRover para o app `<app>-setup`, seja nos clientes SQL ou em scripts de automação), foi informada a URL apontando para `/superset_metastore`:
`postgresql://postgres:<senha>@host:5432/superset_metastore`

O `superset_metastore` é o banco de metadados de uma instalação do Apache Superset existente no mesmo servidor. Conectar-se a ele como banco administrativo padrão é inadequado e arriscado, pois:
1. O banco administrativo padrão nativo do PostgreSQL é o `/postgres`.
2. Conexões administrativas ou scripts temporários podem inadvertidamente criar objetos ou tabelas dentro do banco do Superset.

### O que os serviços do Rastro utilizam:
- **`rastro-ingest` e `rastro-api`**: Já se conectam **diretamente ao banco `rastro`** (`RASTRO_PG_DB=rastro`). Eles **NÃO** gravam dados em `superset_metastore`.
- **`<app>-setup`**: Foi o único serviço do Rastro que recebeu a URL com `/superset_metastore` para realizar o provisionamento inicial do banco `rastro`.

### Instruções para o Próximo Agente

#### 2.1 Garantir que o banco `postgres` existe e está saudável
Conecte-se com as credenciais de superusuário e verifique:
```sql
SELECT datname FROM pg_database WHERE datname = 'postgres';
```
- Se o banco `postgres` existir (comportamento padrão de qualquer instalação PostgreSQL), ele está pronto para uso.
- Caso não exista (muito raro, apenas se alguém o tiver apagado), crie-o:
  ```sql
  CREATE DATABASE postgres WITH OWNER postgres;
  ```

#### 2.2 Inspecionar se houve vazamento de tabelas do Rastro para dentro do `superset_metastore`
Conecte-se temporariamente ao banco `superset_metastore` e verifique se há tabelas ou schemas do Rastro que tenham sido criados lá por engano:
```sql
-- Conectado em superset_metastore:
SELECT table_schema, table_name 
FROM information_schema.tables 
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
  AND (table_name IN ('nodes', 'positions', 'device_telemetry', 'vw_ultima_posicao')
       OR table_schema = 'rastro');
```
- Se retornar **0 linhas**: confirma que nenhuma tabela do Rastro foi criada dentro de `superset_metastore`.
- Se retornar tabelas do Rastro: **NÃO delete cegamente**. Verifique a procedência, os dependentes e consumidores reais, gere um dump preventivo do banco e somente após validação humana proceda à remoção cuidadosa dos objetos do Rastro.

#### 2.3 Atualizar referências e variáveis de ambiente
1. **No arquivo `.env` fornecido pelo usuário:**
   Certifique-se de que a string de conexão ou as variáveis usem `postgres` como database:
   ```env
   PGDATABASE=postgres
   # ou na connection string:
   DATABASE_URL=postgresql://postgres:<senha>@<host>:5432/postgres
   ```
2. **No CapRover (App `<app>-setup`):**
   - Conforme documentado em `docs/OPERACAO-caprover.md` (§3), o app `<app>-setup` roda uma única vez no provisionamento inicial e deve ser **apagado** para não manter a senha de admin em texto puro no ambiente.
   - Verifique com o usuário se o app `<app>-setup` ainda existe no CapRover.
   - Se ainda existir ou caso venha a ser recriado no futuro para reaplicação de migrações:
     Acesse o CapRover $\rightarrow$ App `<app>-setup` $\rightarrow$ **Configuration** $\rightarrow$ Altere `RASTRO_PG_ADMIN_URL`:
     ```text
     DE:   postgresql://postgres:<senha>@srv-captain--postgres:5432/superset_metastore
     PARA: postgresql://postgres:<senha>@srv-captain--postgres:5432/postgres
     ```
3. **Em ferramentas de administração (DBeaver / pgAdmin / TablePlus):**
   - Atualize a conexão para apontar para o banco `postgres` (ou diretamente para `rastro` quando for visualizar os dados de telemetria).

---

## Tarefa 3: Versionamento do Logo Oficial e Descarte de Diagramas PNG — ✅ Concluída em 2026-10-06 (commit `2952d39`)

### Contexto
- `docs/rastro_logo.svg`: Vetor oficial de identidade visual do Rastro, base para o favicon do visualizador e componente animado `LogoLoader.tsx`. Comprimido via SVGO (-39.1%, de 205.7 KiB para 125.3 KiB) preservando o `viewBox="0 0 450 500"` intacto e adicionado ao controle de versão.
- `rastro_flow*.png`: Diagramas de fluxo preliminares que nunca foram versionados e tornaram-se obsoletos frente à arquitetura consolidada no `AGENTS.md`. Descartados do disco conforme orientação do projeto.

---

## Tarefa 4: Ingest nativo Meshtastic (ServiceEnvelope) — 📝 Planejado (2026-10-01)

Origem: plano da frota de seis rios em `univaja-lora` (`drafts/decisoes-pre-implantacao.md`). O nó Nó Solar do Barco (Heltec V4, no barco) publica por **MQTT nativo do firmware 2.7.26** (TCP/TLS, não WebSocket) em `univaja/mesh/2/e/<canal>/<id-do-gateway>`; **não haverá ponte USB paralela na implantação** (só na bancada, aposentada depois); o ingest nativo é o único caminho.
- [ ] Decodificar `ServiceEnvelope → MeshPacket → AES-CTR (chave EVU) → Data → Position/Telemetry/NodeInfo/Text`; pacotes `PKI` ficam opacos.
- [ ] Separar **gateway** (quem subiu) de **remetente** (`from`); deduplicar por `(from, id)` entre Nós Solares dos Barcos/Cartão.
- [ ] Usar o horário do pacote (`position.time`), não o de chegada; marcar horário inválido (0/1970/futuro) e usar fallback sinalizado.
- [ ] Mostrar na UI a **idade** de cada posição, quebrar a linha da trilha em lacunas e nunca deixar um pacote atrasado (replay) substituir uma posição mais nova.
- [ ] Listener do broker: Mosquitto com TCP/TLS (ex.: 8883) com **certificado de CA** (o app iOS valida certificado) + mapeamento de porta no CapRover; manter o listener WebSocket; contas por nó (`%u` = id do nó), ACL por tópico, `retain` desligado; testar cada conta com publicação MQTT **v5** (a 3.1.1 confirma publicações negadas).
- [ ] Chave EVU só no serviço de ingest isolado: sem log de payload/chave, backups restritos; procedimento de troca de chave documentado.
- [ ] Alertas separados: gateway mudo (Nó Solar do Barco sem upload), posição parada, sem fix, móvel ausente; faixa de silêncio noturno (barcos com Starlink desligada à noite estão paradas).
- [ ] Guardar o envelope bruto (retenção curta) antes de decodificar, para reprocessar após bug do adaptador; modelo de dados: texto, NodeInfo, id do pacote, gateway vs remetente, horário observado vs recebido, estado do outbox do chat, cadastro de gateways virtuais (6 barcos + cidade), vínculo dispositivo→barco com validade; provisionar contas/ACL de forma persistente e idempotente (o entrypoint de hoje só cria `gateway`/`ingest`; usar arquivo de ACL gerado, não `%u`, para permitir rotação de senha).
- [ ] Certificado do listener TCP: arquivos montados, recarga do Mosquitto após renovação, checagem externa de validade.
- [ ] Testes: reconexão, replay, 6 contas simultâneas, ausência de fallback para MQTT público, SUBACK/entrega sob ACL, handshake TLS do Heltec V4 pelo mapeamento de porta do CapRover, porta 8883 liberada no firewall do Azure.

## Tarefa 5: Chat no mapa — escritório ⇄ tripulação — 📝 Planejado (2026-10-01)

Pedido do dono: o mapa terá uma interface de chat para o escritório enviar mensagens à tripulação por MQTT (EVU com downlink nas Nó Solar do Barcos) e receber os pedidos de ajuda dos monitores. Desenho verificado no firmware 2.7.26 (ver `univaja-lora` `drafts/decisoes-pre-implantacao.md`, seção #22).
- [ ] **Recebimento:** decodificar `TEXT_MESSAGE_APP` (hoje ignorado) e mostrar no chat com **alerta sonoro/visual** para pedidos de ajuda; guardar horário de observação e de recebimento; retenção curta e configurável (texto de monitores é sensível).
- [ ] **Envio:** montar `MeshPacket` de texto em broadcast (`to=0xffffffff`, `id` nunca reutilizado, `hop_limit` adequado), cifrar com AES-CTR da chave EVU (nonce = id + from), embrulhar em `ServiceEnvelope` e publicar em `univaja/mesh/2/e/EVU/<gateway-virtual>`; **um gateway virtual por barco** para a mensagem chegar só ao rio certo; `retain` desligado.
- [ ] Publicar o NodeInfo "Rastro" do remetente virtual (nome exibido no app); descobrir como os apps exibem remetente desconhecido.
- [ ] Segurança/robustez: o ingest **não republica** pacotes recebidos (evita eco); saída (outbox) separada, deduplicação `(from, id)`, limite de taxa; autenticação do usuário do chat; log sem conteúdo das mensagens.
- [ ] **Validade (TTL) e estado:** barco offline = "não entregue" e a mensagem expira (sem comando velho horas depois); a UI diz "enviado ao gateway do barco", nunca "lido" (não há recibo de entrega).
- [ ] Mensagem direta 1‑para‑1 exige PKI (chaves de identidade) — fora da v1; usar broadcast por barco.
- [ ] Testes de campo: escritório→tripulação e tripulação→escritório com humano lendo; barco com Starlink desligada e religada; seis barcos ao mesmo tempo sem vazar mensagem entre rios.


### Tarefa 4/5 — status (2026-10-05, só código + rig local; nada em produção, nada commitado)

Desenho e interfaces: `docs/native-ingest-design.md`. Novo código: `services/rastro_gateway/native/` (crypto, envelope, service, alerts), `services/rastro_gateway/chat/` (outbox, processo `python -m rastro_gateway.chat`), `deploy/postgres/init/02-native.sql` + `migrate-02-native.sh`, `broker/accounts.py` (contas por nó + ACL gerada), `services/rastro_api/api/chat.py`, painel de chat em `web/src`. Rig: `deploy/sim/native-rig.sh`/`native_rig_test.py` (broker, MQTT v5) e `deploy/sim/native_e2e.sh` (ingest+outbox reais, Postgres descartável).

Feito e provado localmente: decodificação ServiceEnvelope→Position/Telemetry/NodeInfo/Text (PKI opaco); dedupe `(from,id)` entre gateways; `position.time` com horário inválido sinalizado (`time_flag`); replay nunca troca a posição mais nova; idade da posição + trilha quebrada em lacunas; envelope bruto gravado; ack só após commit; reconexão com sessão persistente sem perda; 6 contas simultâneas; ACL por nó gerada (sem `%u`), retain desligado, wildcard `EVU/+` recebe SUBACK e a ACL filtra a entrega (sem vazamento entre 6 barcos); TLS pela porta mapeada; sem fallback de broker; negativas com MQTT v5 (reason code 135); ingest nunca publica; outbox separado (TTL, taxa por barco, id de pacote não reutilizado, sem eco); UI diz «enviado ao gateway do barco», nunca «lido»; alertas (gateway mudo, posição parada, sem fix, móvel ausente, silêncio noturno) como lógica + estado em `alert_state` (ciclo só liga com `RASTRO_ALERTS_ENABLED=1`; entrega de alertas NÃO implementada); logs sem PSK/texto.

> **Versão atual publicada: 0.8.1** (ingest, chat, api, broker, web, pgtools-pg17; template one-click com app `-chat`, contas dos nós derivadas de segredo, seed de gateways virtuais; ingest/chat desligados por padrão). Em produção até 2026-10-05 estavam as 0.7.0.
> **Implantado em 2026-10-05:** imagens `communityfirst/rastro-{ingest,api,broker,web}:0.7.0` (+ `rastro-pgtools:0.7.0-pg17`) no CapRover; migração `02-native.sql` aplicada em produção (7/143/199 intactos). Correção pós-migração: o papel `rastro_ingest` NÃO lê coordenadas — o ciclo de alertas usa a função `node_activity_summary()` (SECURITY DEFINER); a migração revoga grants antigos. O repo `../univaja-lora` ainda usa o nome de canal `EVU` (263 ocorrências em planos/provisionamento): alinhar para `EVU` antes do campo.

**Estado ao fim da sessão de 2026-10-05/06 (tudo em produção, exceto o que está marcado):**
- Produção: `rastro-{ingest,api,broker,web,chat}` em `0.8.1` desde 2026-10-06 (imagens `communityfirst/rastro-*:0.8.1`, `rastro-pgtools:0.8.1-pg17`); migração `02-native.sql` aplicada (+ revogação de leitura de coordenadas do `rastro_ingest`; uso de `node_activity_summary()`); `rastro-setup` apagado (volume `rastro-pgconn` mantido); porta 8883 aberta pelo devops e mapeada no broker; broker com contas dos 5 barcos (`RASTRO_NATIVE_NODES`), chave EVU em ingest e chat, `RASTRO_NATIVE_ENABLED=1` (ingest) e `RASTRO_CHAT_ENABLED=1` (chat); `virtual_gateways`/`boat_devices` semeados (5 barcos: curuca-1, itaquai-1, itui-1, jaquirana-1, medio-javari-1). Provado: chat→broker→ingest em produção (5 NodeInfo virtuais em `raw_envelopes`), contas dos barcos publicam só no próprio tópico (testado de fora na 8883).
- Ferramentas (commitadas em `cdb3a30`): `scripts/rastro_caprover.py` (API CapRover, dry-run por padrão), `scripts/rastro_nodes_from_fleet.py` (gera `RASTRO_NATIVE_NODES` do `../univaja-lora/devices/fleet.json`; `--apply` grava em broker+chat), skill `~/.agents/skills/rastro-caprover/SKILL.md`. Template one-click (`../caprover-one-click-apps`, branch `rastro-0.5.1`: app `-chat`, variáveis nativas, tag 0.8.0) commitado em `aee41cc` (não enviado). CI do `rastro` corrigido localmente em `0c29c8f` (import relativo em `test_chat_seed`; **falta push** para confirmar o CI verde).

**EM PRODUÇÃO COM NÓS EM CAMPO — contrato congelado: ver AGENTS.md §5 (derivação da senha, segredo, canal EVU, tópicos, ACL, banco). Nada disso muda sem plano de migração aprovado.**

**Pendências (ordem sugerida):**
- [x] Senha MQTT em 30 caracteres, `retain` + `write stat/<id>`, `captain-definition`: commitados (`867b61b`, `8aef48e`, `659b657`) e enviados; produção roda a imagem `img-captain-rastro-broker:6` construída da fonte. Barco e móvel de Ituí em campo funcionando (286 envelopes/24h do barco).
- [ ] **NUNCA** `deploy-image rastro-broker …:0.8.0` nem instalar pelo template com a tag padrão 0.8.0 (derivam 32 caracteres e trancam o barco). Retorno seguro: `img-captain-rastro-broker:6` (conferir retenção da imagem no CapRover).
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
- [ ] Verificar o broker em produção: sha256 de `/rastro/*` no contêiner contra `broker/` local (só leitura) e nomes (não valores) das variáveis do app.
- [ ] `univaja-lora` sem remoto git (`fee73ba`, `b08dcf5` só locais). Docs/AGENTS/TUTORIAL do 30 caracteres estão na árvore, misturados com o rename EVU de outra sessão, não commitados. Rodar `fleet_sync.py --check` antes de commitar o `fleet_sync.py` modificado.
- [x] Web: Todos os 189 testes unitários passando em 13 arquivos (Vitest). Suporte a dados reais via proxy HTTPS/local e auto-login em ambiente de desenvolvimento.
- [ ] **1º rádio real**: configurar um nó de barco (servidor `137.116.59.230:8883` ou `rastro-broker.javari.guardianconnector.net`, TLS, usuário = id do nó, senha = `python3 scripts/rastro_node_credentials.py --secret <RASTRO_NATIVE_SECRET do app rastro-broker> --node '!id'`, raiz `univaja/mesh`, canal EVU com uplink+downlink, Wi-Fi ligado). Depois conferir `raw_envelopes`/`gateway_status`/`positions` (sem imprimir coordenadas). Ainda NÃO testado com firmware real: envelopes do Heltec V4, fila de 16 mensagens, como o app Meshtastic mostra o remetente «Rastro».
- [ ] Testes de campo (tarefa 5): escritório→tripulação e tripulação→escritório com humano; Starlink desligada/religada; 5–6 barcos ao mesmo tempo sem vazar mensagem entre rios; barco offline = mensagem expira (TTL).
- [ ] Sexto barco (Atalaia): só `atx1` (teste) existe no inventário; quando houver `univaja-atalaia-barco-1`, rodar `python3 scripts/rastro_nodes_from_fleet.py --apply`. `cartao-2/3` (kind boat, sem nome da frota) ficaram de fora de propósito.
- [ ] `../univaja-lora`: **código do modeA commitado** (`b08dcf5`: `mtool.py`, `rastro_derive.py`, `install.sh`, testes; dev-gate codex+opus rodado em cópia descartável; PASS divergente da derivada é recusado; 117 + 130 testes). Falta: (a) docs/skills revisados e corretos (`docs/TUTORIAL-configurar-no-usb.md`, `docs/AGENTE-CONFIGURAR-FROTA.md`, `.skills/fleet-provisioning`, `.skills/meshtastic-setup`, README do modeA — incluem o passo «registrar no Rastro ANTES de `mqttenable`») mas **não commitados** porque estão misturados com o rename FLEET→EVU de outra sessão: commitar junto com ela; (b) NITs aceitos: `own_node_id` é chamado também para classes admin (`mtool.py` `_mqtt_args`/`cmd_unisolate`); linha USER do barco no `provision.env` faz o id sair como `***` na saída; (c) `UNIVAJA_MQTT_SECRET` em `~/.config/univaja/provision.env`: **feito e conferido** (igual a `RASTRO_NATIVE_SECRET` do broker). Conferir/gravar sem imprimir o valor: `python3 scripts/rastro_caprover.py univaja-secret [--execute]`.
- [ ] Renomear `FLEET`→`EVU` no `../univaja-lora` (≈263 ocorrências em planos/provisionamento; parte já em andamento por outra sessão) e no `AGENTS.md`/docs legados deste repo.
- [ ] **Push** (nenhum feito): `rastro` (commits `cdb3a30`, `0c29c8f`, tag local `v0.8.0`), `caprover-one-click-apps` (`aee41cc`, branch `rastro-0.5.1`), `univaja-lora` (`b08dcf5`). Depois do push do `rastro`, conferir o workflow `images` verde (a API e o viewer ainda não foram rodados no CI desde o 0.8.0).
- [ ] Certificado TLS confiável (Let's Encrypt): só necessário para o app iOS (Admin Móvel); os rádios não validam. Exige o devops: copiar `fullchain.pem`/`privkey.pem` do CapRover para um volume em `/mosquitto/secrets` do broker (dono 1883, 0400), reiniciar o broker, e agendar cópia+restart periódicos (renovação).
- [ ] Segurança/higiene: o dump `~/rastro-backups/rastro_pre_*.dump` contém coordenadas reais (mover/apagar; `chmod 600`); apagar `~/evu_psk.b64`; guardar `RASTRO_NATIVE_SECRET` (deriva todas as senhas dos nós) em cofre; trocar a chave EVU exige procedimento ainda NÃO documentado.
- [x] Decisão do dono R6 (retenção) RESOLVIDA 2026-10-08: histórico eterno — `purge_expired`/`RASTRO_*_RETENTION_DAYS` removidos; `prune_packet_seen` permanece hook (dedupe 7 dias). R9 (acesso ao servidor) segue aberta. Texto do chat é sensível: definir política antes do uso rotineiro.
- [ ] Entrega de alertas (push/e-mail) — ainda NÃO implementada; o motor e a exposição (`GET /api/alerts`, badges) estão no ar desde 0.8.5, com `gateway_mudo`+`bateria_critica` e ciclo ligado em produção (`RASTRO_ALERTS_ENABLED=1`, fixo: `!f2664e10`; `atalaia-pico/torre` entra quando existir).
- [ ] Chat: autenticação usa o token único da API (usuário «escritorio»); definir usuários reais se necessário. UI e API de chat estão no ar mas sem uso até o 1º rádio.
- [ ] Logs de contêiner não são legíveis pela API do CapRover: diagnóstico = ler logs no painel do CapRover ou olhar o banco (`raw_envelopes`, `gateway_status`).
- [ ] Riscos aceitos/conhecidos: replay antigo com horário inválido cai no fallback «agora» (sinalizado por `time_flag`); `rastro_ingest` também grava `virtual_gateways`/`boat_devices` (tabelas de configuração, sem coordenadas).
- Tarefa 6 permanece BLOQUEADA (não iniciada).

## Verificação de premissas e pendências (2026-10-01, plano da frota v7)
- [x] Premissas de firmware/docs verificadas contra o firmware 2.7.26.54e0d8d e as docs 2.7 (`univaja-lora/drafts/verificacao-premissas.md`): o cliente MQTT do nó usa TCP puro (sem WebSocket), não valida certificado TLS e imprime a senha no log; a fila é de 16 mensagens e só 1 entrada é reenviada após reconexão; assina `<raiz>/2/e/<canal>/+` e `<raiz>/2/e/PKI/+`; telemetria do dispositivo exige `device_telemetry_enabled`.
- [ ] **Retenção:** o dono informa que a retenção (nº de dias) é configurável no app web e que o log bruto no banco é eterno. No repositório só existe `scripts/rastro_retention.py` (dry-run por padrão; exige `RASTRO_RETENTION_DAYS` + `--executar`) e **nenhuma opção no app web** — confirmar onde está essa configuração. Definir o que a retenção apaga (posições, envelopes brutos, texto do chat, backups) e se o log bruto também terá limite (exclusão sob pedido/LGPD).
- [ ] Texto do chat: mesma política de retenção das posições, ou menor; nunca registrar o conteúdo em logs.
- [ ] Modelo de dados: texto, NodeInfo, id do pacote, gateway vs remetente, observado vs recebido, outbox do chat, gateways virtuais (6 barcos + cidade), vínculo dispositivo→barco com validade, **estado de energia/bateria por Nó Solar** para os alertas de energia.
- [ ] Alertas de energia: telemetria de bateria dos Nós Solares (900 s) com limiares por classe e alerta antes do apagão.

---

## Tarefas 10 a 20: Melhorias do Visualizador Web (Frontend SolidJS)

### Tarefa 10: Fixtures e Ambiente Local com Dados Reais do Servidor — ✅ Concluída em 2026-10-06
- [x] Proxy de desenvolvimento configurado em `web/vite.config.ts` encaminhando `/api` para o backend (suporte tanto a contêiner local `:8080` quanto a proxy HTTPS de produção com SSL flexível).
- [x] Configuração `web/.env.development` com `VITE_API_TOKEN` e `VITE_API_TARGET` (com auto-login e injeção transparente de credenciais Bearer em dev).
- [x] Atualização de `web/src/fixtures/nodesFixture.ts` para mimetizar dados reais:
  - Baterias conectadas a 5V/USB reportando 101% (ex.: `univaja-curuca-barco-1`, `univaja-itui-barco-1`).
  - Nós inativos com mais de 7 dias (ex.: `univaja-itui-barco-1` com 8 dias / visto em 2026-09-28 no banco real, `curuca-campo-1` com 9 dias, `jaquirana-campo-1` com 12 dias).
  - Presença de `age_s` no contrato GeoJSON de `/api/nodes/latest`.
- [x] Testes unitários atualizados em `web/tests/unit/mock-fixture.test.ts` (14 testes passando).

### Tarefa 11: Detecção de Nós de Barco pelo Nome ("barco") e Ícone de Embarcação — ✅ Concluída em 2026-10-06
- [x] Atualizar lógica em `web/src/lib/nodes.ts` para detectar nós como `kind: "boat"` quando `nome` contiver a palavra "barco" (case-insensitive e normalizado sem acento), mesmo que o backend não envie metadado de `kind`.
- [x] Exibir o ícone de barco (`boat-icon` com rotação por azimute) no MapLibre para todos os nós detectados como barco.
- [x] Exibir o ícone do barco na lista de nós (`NodeList.tsx`) e a tag de categoria "Barco".
- [x] Atualizar testes em `tests/unit/nodes.test.ts`.

### Tarefa 12: Ordenação da Lista por Último Visto e Filtro de Inativos (> 7 Dias) com Toggle — ✅ Concluída em 2026-10-06
- [x] Ordenar a lista de nós por último visto (`posTime` / `last_seen`) em ordem decrescente (mais recentes primeiro; nós sem fix ao final).
- [x] Esconder por padrão nós cujo fix mais recente tem mais de 7 dias de idade (`ageS > 7 * 86400`).
- [x] Adicionar botão/toggle na interface da lista ("Mostrar inativos (> 7 dias)" ou ícone com contagem) para revelar ou ocultar nós antigos sob demanda.

### Tarefa 13: Exibição do Modelo de Hardware e Imagem SVG na Lista de Nós (Apenas na Sidebar) — ✅ Concluída em 2026-10-06
- [x] Exibir o SVG do dispositivo **exclusivamente na sidebar** (card da lista e inspector), **nunca no mapa** (o mapa usa apenas os marcadores circulares e o ícone de barco, e o popup do pin usa badge textual).
- [x] Exibir o nome textual/badge do modelo de hardware na lista quando disponível (ou inferido via prefixo/tabela).
- [x] Garantir que o SVG correto seja renderizado no card de cada nó na lista: `boat.svg` para barcos, SVG do hardware correspondente (`heltec_v4.svg`, `tbeam.svg`, etc.) para outros rádios, com fallback legível para `unknown.svg`.

### Tarefa 14: Trilhas de Barcos: Coletivas quando Nenhum Selecionado, Exclusiva quando Selecionado — ✅ Concluída em 2026-10-06
- [x] Quando **nenhum nó estiver selecionado**: desenhar as trilhas de todos os barcos de forma contínua e suave (linha mais fina/sutil, sem pontos individuais de fix), evitando poluição visual.
- [x] Quando **um nó estiver selecionado**: **esconder o caminho de todos os outros nós**, exibindo exclusivamente a trilha do nó selecionado (com destaque em amarelo e marcadores de fix).

### Tarefa 15: Botão de Camadas com Satélite como Mapa Padrão — ✅ Concluída em 2026-10-06
- [x] Adicionar fonte de mapa satélite (ArcGIS World Imagery) e defini-la como basemap padrão do mapa.
- [x] Criar botão/menu de camadas nos controles do mapa (`MapControls.tsx`) permitindo alternar entre Satélite, OpenStreetMap e Basemap Local (PMTiles offline).
- [x] Salvar a preferência da camada no store / localStorage com proteção contra 404 em basemap local ausente.

### Tarefa 16: Navegação Responsiva Mobile em 2 Colunas com 2 Linhas e Ícones — ✅ Concluída em 2026-10-06
- [x] Em telas mobile (`< md`), controles de navegação dispostos em **duas colunas nas extremidades**:
  - Coluna esquerda: Linha 1 = Camadas (ícone `StackSimpleIcon`), Linha 2 = Enquadrar todos (ícone `ArrowsOutIcon`).
  - Coluna direita: Linha 1 = Nós da malha (ícone `ListIcon`), Linha 2 = Chat (ícone `ChatCircleTextIcon` com badge não lidas).
- [x] Botões estritamente com ícones (sem labels de texto em mobile), tamanho 40x40px acessível para toque.
- [x] Menu dropdown de camadas posicionado abaixo do botão com suporte a fechamento ao clicar fora (click-outside) e tecla `Escape`.
- [x] Semântica ARIA completa (`role="menu"`, `role="menuitemradio"`, `aria-checked`, `aria-expanded`, `aria-haspopup`).
- [x] Em telas desktop (`>= md`), mantém barra horizontal superior unificada com labels completos.

### Tarefa 17: Clique Fora no Mapa Desseleciona Nós e Fecha Popups — ✅ Concluído (caea365)
- [x] No `web/src/InitializeMap.tsx`, adicionar listener no canvas do mapa (`map.on("click", (e) => ...)`):
  - Verificar se o evento de clique atingiu algum pin (`nodes-circle`, `nodes-boat` ou outros elementos interativos).
  - Se clicou em área livre (água, floresta ou basemap sem nós sob o cursor):
    - Executar `LocalState.select(null)`.
    - Fechar e remover qualquer popup MapLibre ativo (`popup?.remove(); popup = undefined;`).
  - Ao desselecionar, restaurar automaticamente a exibição de trilhas coletivas sutis de barcos (`boat-tracks-line`) sem interferência.

### Tarefa 18: Revisão da Detecção de Barcos e Inferência de Nós Não-Embarcações — ✅ Concluído (0336fa2)
- [x] **Contexto & Diagnóstico:** Nós que não são embarcações, como `univaja-atalaia-movel-2` (e rádios de mão `movel`, rádios base `teto`, rastreadores `cartao`), não devem ser classificados como barco nem exibir ícone de embarcação.
- [x] **Inferência de Categoria (`kind`) no Frontend (`web/src/lib/nodes.ts`):**
  - Implementar regras de inferência hierárquicas a partir do nome do dispositivo quando `kind` não vier explicitamente do backend:
    - Nomes com `barco`: `kind: "boat"`
    - Nomes com `movel`: `kind: "handheld"` (nunca barco)
    - Nomes com `teto`, `fixo` ou `base`: `kind: "fixed_station"` (nunca barco)
    - Nomes com `cartao` ou `t1000`: `kind: "handheld"` (nunca barco)
  - Regra de exclusão estrita: se o nome contiver palavras-chave de rádio portátil (`movel`, `handheld`, `cartao`) ou estação fixa (`teto`, `base`), **rejeitar terminantemente `isBoatNode`**, mesmo se houver ambiguidade no nome.
- [x] **Inferência de Hardware (`hwModel`) e SVGs (`web/src/lib/nodes.ts`):**
  - Estender `inferHardwareFromName(nome)` para mapear as convenções da frota:
    - `movel` → `HELTEC_V4` (ou `TBEAM` para nós admin como `admin-movel`)
    - `teto` → `HELTEC_V4`
    - `cartao` → `TRACKER_T1000_E`
  - Garantir que `hardwareModelLabel` e `nodeSidebarSvgUrl` exibam o badge e SVG corretos (ex.: `heltec_v4.svg` ou `tracker-t1000-e.svg`), evitando fallback desnecessário para `unknown.svg` ou ícone de barco incorreto.
- [x] **Desambiguação de Sobreposição de Pins no Mapa (`InitializeMap.tsx`):**
  - Quando múltiplos nós estiverem nas mesmas coordenadas exatas (ex.: bancada de testes em Atalaia do Norte), garantir que cliques e z-index permitam selecionar nós individuais e que nós não-embarcações não fiquem mascarados pelo ícone de barco.

### Tarefa 19: Algoritmo de Barco Parado/Ancorado (Dwell & Anchor Detection) contra Trilhas Falsas — ✅ Concluído (e72a8ff, 6a58f9d)
- [x] **Contexto:** Nos rios amazônicos, barcos atracados ou ancorados sofrem dispersão de GPS (5–30 m por multipath sob a mata) somada ao raio de giro no fundeio (15–50 m de amarra na correnteza). Isso gera "novelos de linhas" (*hairball*) sobrepostas na trilha, infla artificialmente o odômetro e faz o rumo (`bearingDaTrilha`) girar 360° loucamente a cada fix.
- [x] **Especificação Matemática do Algoritmo ST-DAH (*Spatio-Temporal Dwell Accumulator with Hysteresis*):**
  - **Métrica de distância:** Projeção equirretangular local ($\Delta x, \Delta y, d = \sqrt{\Delta x^2 + \Delta y^2}$), $10\times$ mais rápida que Haversine e precisa para latitudes equatoriais ($\le 7^\circ\text{ S}$).
  - **Parâmetros operacionais calibrados para rios amazônicos:**
    - Raio de fundeio ($R_{\text{dwell}}$): **50 metros** (cobre amarra + espalhamento GPS).
    - Janela temporal mínima ($T_{\text{dwell\_min}}$): **15 minutos**.
    - Velocidade de corte ($v_{\text{stop\_thresh}}$): **$2.5\text{ km/h}$** (~$1.3\text{ nós}$).
    - Velocidade de retomada de navegação ($v_{\text{nav\_min}}$): **$4.0\text{ km/h}$** (~$2.2\text{ nós}$).
    - Histerese de saída ($K_{\text{breakout}}$): **2 fixes consecutivos** fora do raio $R_{\text{dwell}}$ (ou 1 fix com $d > 100\text{ m}$ e $v > v_{\text{nav\_min}}$), prevenindo que spikes isolados de erro quebrem a detecção de parada.
- [x] **Ações de Implementação:**
  - Criar `web/src/lib/dwell.ts` com a função pura `simplifyTrackDwells(trackPoints, options)`.
  - **Supressão de trilhas falsas:** Em períodos com estado `PARKED`, omitir todas as linhas internas do GeoJSON, ligando a trilha diretamente do ponto de chegada ao centróide $C_k$, e do centróide ao ponto de partida.
  - **Estabilização de rumo (`web/src/lib/bearing.ts`):** Quando o barco estiver no estado de parada, congelar o azimute no último rumo válido de aproximação ou definir como nulo, evitando giros espúrios do ícone SVG da embarcação.
  - **Representação visual no MapLibre (`InitializeMap.tsx`):**
    - Renderizar marcador tático de ancoragem (`dwell-point`) no centróide da parada com tooltip/popup contendo horário de chegada e tempo total parado (ex.: *"Ancorado há 4h 15m"*).
    - Adicionar badge de status nos cards da sidebar: `🟢 Navegando (X km/h)` vs `⚓ Ancorado / Parado (há Xh)`.

### Tarefa 20: Logo Animado como Loader e Favicon (`docs/rastro_logo.svg`) — ✅ Concluído (ab4c62c, 9386e66)
- [x] **Favicon do Visualizador:**
  - Copiar e adaptar `docs/rastro_logo.svg` para `web/public/favicon.svg` (viewBox quadrado 1:1, otimizado para renderização nítida em abas de navegadores e preservando cores nos temas escuro e claro com badge de fundo arredondado).
  - Atualizar `web/index.html` para incluir `<link rel="icon" type="image/svg+xml" href="/favicon.svg" />`.
- [x] **Componente de Loader Animado (`web/src/components/ui/LogoLoader.tsx`):**
  - Criar componente SolidJS reutilizável baseado no SVG oficial do Rastro.
  - Implementar animação CSS sutil e fluida com aceleração de GPU e suporte a `prefers-reduced-motion` (pulso de sinal no ponto/antena central `#eb742d` e ondas de radar suaves no contorno da malha).
  - Substituir o texto cru `<Text class="text-gray-400">Carregando…</Text>` em `web/src/App.tsx` (estado `verificando` de autenticação) e nos estados vazios/carregamento do visualizador (`NodeList.tsx`) por uma splash elegante com o logo animado.
  - Suportar prop de dimensão (`size: "sm" | "md" | "lg"` com aspect ratio preservado) e texto de status opcional (ex.: *"Conectando à malha Rastro..."*).


