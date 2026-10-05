# TODO — Rastro

## Visão Geral (Status)

| # | Tarefa | Status | Bloqueio | Prioridade |
|---|--------|--------|----------|------------|
| 1 | Limpeza segura do banco legado `mqtt` | ✅ Concluída (2026-10-01) | `DROP DATABASE mqtt` executado; `rastro` intacto (7 nós / 143 posições). Script agora é inspetor read-only (DROP removido); 3 shadow DBs `prisma_migrate_shadow_db_*` dropados (2026-10-01, vazios). Papel `mqtt` REMOVIDO (2026-10-01, a pedido do dono; origem: protótipo `meshtastic-map` criado por ele, sem uso): REVOKE ALL das 8 tabelas de `warehouse.public` + ACL padrão do `cmiadmin` (só entradas do `mqtt`; demais ACLs idênticas, verificado por diff) e `DROP ROLE mqtt` | 🔴 Alta |
| 2 | Conexão admin: `/superset_metastore` → `/postgres` | ✅ Concluída (2026-10-01) | `RASTRO_PG_ADMIN_URL` do app `rastro-setup` e `DB=` do `.env` agora apontam para `/postgres`; `superset_metastore` sem tabelas do Rastro. Considerar apagar `rastro-setup` (docs/OPERACAO-caprover.md §3) | 🔴 Alta |
| 3 | Versionar diagramas `rastro_flow*.png` | ⏳ Aguardando | Requer aprovação do usuário para `git add` | 🟡 Baixa |
| 4 | Ingest nativo Meshtastic (ServiceEnvelope) dos nós Nó Solar do Barco | 🟡 Imagens 0.7.0 implantadas (ingest/api/broker/web) + migração 02 aplicada em produção (2026-10-05); ingest nativo DESLIGADO (RASTRO_NATIVE_ENABLED não definido) | Pendências do dono: checklist em «Tarefa 4 — status» (porta 8883/firewall Azure, certificado real, contas reais, PSK EVU, migração 02 em produção) | 🔴 Alta |
| 5 | Chat no mapa: escritório ⇄ tripulação via MQTT (EVU downlink) | 🟡 Imagens 0.7.0 implantadas; chat sem uso até criar o app `rastro-chat` (imagem rastro-ingest:0.7.0, comando `python -m rastro_gateway.chat`) e o broker com contas; testes de campo pendentes | Mesmas pendências da tarefa 4 + conta `outbox` + testes de campo com humanos | 🔴 Alta |
| 6 | Ações remotas por nó no mapa: **reiniciar** e **desligar** | 📝 Planejado | Botão por nó no visualizador (requer autenticação admin). Regras: nós **solares/fixed = somente reiniciar** (desligar é irreversível por rádio — ninguém no local para apertar o botão); desligar oferecido só em nós com tripulação por perto (morto até religarem fisicamente); **nunca** factory-reset remoto; transporte = AdminMessage PKC pela malha (depende do caminho de comando da tarefa 4/5 — hoje inexistente; guardrails no repo `univaja-lora`, skill `remote-management` §3.3) | 🔴 Alta |
| 7 | Apagar o app `rastro-setup` no CapRover | ✅ Concluída (2026-10-05, a pedido do dono; volumes preservados) | Mantém a senha de admin do PostgreSQL em texto no ambiente (docs/OPERACAO-caprover.md §3). Só recriar se precisar reprovisionar | 🟠 Média |
| 8 | Commitar arquivos pendentes (`AGENTS.md`, `TODO.md`, `scripts/rastro_cleanup_legacy_db.py`, `.gitignore`, `.agents/`) | ⏳ Aguardando | Requer aprovação do usuário para commit | 🟡 Baixa |
| 9 | Provisionamento USB (`tsk` thread `provisao-usb`, #8–#11): commitar script/doc/skill; verificar ingestão de `atalaia-mobile-1` ponta a ponta; escolher nome curto; exercitar ramos não testados do script | ⏳ Aberto | Ver `tsk list --all` | 🟠 Média |

> **Arquivos ainda não commitados** (verificados via `git status`): `AGENTS.md`, `TODO.md`, `scripts/rastro_cleanup_legacy_db.py`, `rastro_flow.png`, `rastro_flow_readme.png`.

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

## Tarefa 3: Versionamento dos Diagramas de Arquitetura — ⏳ Pendente (aguardando aprovação)

### Contexto
Durante a sessão, foram gerados e ajustados com precisão dois diagramas da plataforma Rastro:
- `rastro_flow.png`: Diagrama de fluxo arquitetural completo (Gateway Serial $\rightarrow$ Mosquitto WSS $\rightarrow$ Ingest PostgreSQL $\rightarrow$ FastAPI $\rightarrow$ SolidJS Viewer).
- `rastro_flow_readme.png`: Versão dimensionada para exibição direta no `README.md`.

Ambos os arquivos estão prontos e preservados na raiz do repositório, mas intencionalmente **não comitados** a pedido do usuário.

### Instruções para o Próximo Agente
- [ ] Perguntar ao usuário se deseja versionar os diagramas agora (`git add rastro_flow.png rastro_flow_readme.png`).
- [ ] Caso aprovado, referenciar as imagens na documentação principal (`README.md` ou `docs/ARQUITETURA.md`) e efetuar o commit.

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

> **Versão atual publicada: 0.8.0** (ingest, chat, api, broker, web, pgtools-pg17; template one-click com app `-chat`, contas dos nós derivadas de segredo, seed de gateways virtuais; ingest/chat desligados por padrão). Em produção até 2026-10-05 estavam as 0.7.0.
> **Implantado em 2026-10-05:** imagens `communityfirst/rastro-{ingest,api,broker,web}:0.7.0` (+ `rastro-pgtools:0.7.0-pg17`) no CapRover; migração `02-native.sql` aplicada em produção (7/143/199 intactos). Correção pós-migração: o papel `rastro_ingest` NÃO lê coordenadas — o ciclo de alertas usa a função `node_activity_summary()` (SECURITY DEFINER); a migração revoga grants antigos. O repo `../univaja-lora` ainda usa o nome de canal `FLEET` (263 ocorrências em planos/provisionamento): alinhar para `EVU` antes do campo.

**Falta / checklist do dono (NÃO feito):**
- [x] Migração 02 aplicada no PostgreSQL de produção em 2026-10-05 (backup `pg_dump -Fc` feito antes e restaurado em ensaio local; contagens 7/143/199 intactas; 10 tabelas novas; só o banco `rastro` foi tocado). Backup local: `/tmp/claude-1000/prodbak/` (contém dados reais — mover/apagar conforme política).
- [ ] Mosquitto de produção: abrir 8883 no firewall do Azure + mapeamento de porta no CapRover; certificado TLS real (CA reconhecida pelo app iOS), montagem + recarga após renovação + checagem externa de validade.
- [ ] Criar `accounts.json` real (fora do repo; senhas ≥ 24 caracteres; 6 nós + gateways virtuais + `ingest`/`outbox`), `RASTRO_ACCOUNTS_FILE`, e testar cada conta com MQTT v5 no broker real; handshake do Heltec V4 pelo mapeamento de porta.
- [ ] Chave EVU (`RASTRO_EVU_PSK_B64`) só nos serviços `ingest` e `outbox`; procedimento de troca de chave ainda NÃO documentado.
- [ ] `RASTRO_NATIVE_ENABLED=1` no ingest e subir o serviço `chat` (outbox) com a conta `outbox`; cadastrar `virtual_gateways` (6 barcos + cidade) e `boat_devices`.
- [ ] Testes de campo (tarefa 5): escritório→tripulação e tripulação→escritório com humano, Starlink desligada/religada, 6 barcos ao mesmo tempo; descobrir como os apps exibem o remetente virtual «Rastro» (NodeInfo publicado, não verificado em app).
- [ ] Entrega dos alertas (push/e-mail) e alertas de energia (limiares de bateria; `node_power` já é gravado).
- [ ] Decisões abertas R6/R9: retenção e acesso ao servidor — só existem hooks (`RASTRO_RAW_RETENTION_DAYS`, `RASTRO_CHAT_RETENTION_DAYS`, `purge_expired`, `prune_packet_seen`; nada agendado).
- [ ] Autenticação do chat usa o mesmo token/sessão do visualizador (usuário único `escritorio`); definir usuários reais se necessário.
- [ ] Risco aceito (revisão Codex): replay muito antigo com horário de dispositivo inválido cai no fallback «agora» (sinalizado por `time_flag`); só afeta pacotes reenviados depois da janela de dedupe (7 dias). Revisar se aparecer na prática.
- Tarefa 6 permanece BLOQUEADA (não iniciada; nenhum gancho de comando além do outbox de texto).

## Verificação de premissas e pendências (2026-10-01, plano da frota v7)
- [x] Premissas de firmware/docs verificadas contra o firmware 2.7.26.54e0d8d e as docs 2.7 (`univaja-lora/drafts/verificacao-premissas.md`): o cliente MQTT do nó usa TCP puro (sem WebSocket), não valida certificado TLS e imprime a senha no log; a fila é de 16 mensagens e só 1 entrada é reenviada após reconexão; assina `<raiz>/2/e/<canal>/+` e `<raiz>/2/e/PKI/+`; telemetria do dispositivo exige `device_telemetry_enabled`.
- [ ] **Retenção:** o dono informa que a retenção (nº de dias) é configurável no app web e que o log bruto no banco é eterno. No repositório só existe `scripts/rastro_retention.py` (dry-run por padrão; exige `RASTRO_RETENTION_DAYS` + `--executar`) e **nenhuma opção no app web** — confirmar onde está essa configuração. Definir o que a retenção apaga (posições, envelopes brutos, texto do chat, backups) e se o log bruto também terá limite (exclusão sob pedido/LGPD).
- [ ] Texto do chat: mesma política de retenção das posições, ou menor; nunca registrar o conteúdo em logs.
- [ ] Modelo de dados: texto, NodeInfo, id do pacote, gateway vs remetente, observado vs recebido, outbox do chat, gateways virtuais (6 barcos + cidade), vínculo dispositivo→barco com validade, **estado de energia/bateria por Nó Solar** para os alertas de energia.
- [ ] Alertas de energia: telemetria de bateria dos Nós Solares (900 s) com limiares por classe e alerta antes do apagão.
