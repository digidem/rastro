# Plano de Implementação: Alertas de Campo e Simplificação da Retenção

## 1. Objetivo e Arquitetura

Este documento detalha o plano técnico para implementar duas frentes críticas no sistema Rastro:
1. **Simplificação da Retenção de Dados:** Abolir políticas de expurgo para posições, mensagens e envelopes brutos. O sistema passa a reter o histórico operacional por tempo indeterminado.
2. **Alertas de Campo Focados:** Reestruturar o motor de alertas para reduzir ruídos falsos positivos. Focaremos em dois alertas vitais: Gateways isolados da rede (sem uplink) e Nós Fixos/Solares com bateria crítica. A visibilidade dos alertas será levada ao operador tático no visualizador web.

## 2. Mudanças por Componente

### 2.1. Ingest e Banco de Dados (Retenção)
**Objetivo:** Remover expurgos sistêmicos mantendo apenas limpeza de cache de deduplicação.

*   **Arquivos Afetados:** `services/rastro_gateway/ingest/db.py`, `services/rastro_gateway/ingest/__main__.py`
*   **Ações:**
    *   **Remover código morto:** Deletar a função `purge_expired` e qualquer referência a exclusão de registros em `chat_messages`, `positions`, e `raw_envelopes`.
    *   **Limpeza de Variáveis de Ambiente:** Remover variáveis do tipo `RASTRO_*_RETENTION_DAYS` da base de código e da documentação.
    *   **Manutenção Essencial:** Preservar a função `prune_packet_seen()` em `db.py` e sua chamada periódica para evitar inchaço da tabela `packet_seen` (mantendo o expurgo de pacotes com mais de 7 dias, usados apenas para deduplicação MQTT).

### 2.2. Motor de Alertas (Gateway Native)
**Objetivo:** Eliminar alertas ruidosos e implementar alertas acionáveis.

*   **Arquivo Afetado:** `services/rastro_gateway/native/alerts.py`
*   **Ações:**
    *   **Remoção de Ruído:** Excluir os alertas e lógicas associadas a: `sem_fix`, `posicao_parada`, `movel_ausente`, e janelas de silêncio (`_janela_silencio`, `RASTRO_QUIET_START`, `RASTRO_QUIET_END`).
    *   **Manter:** Alerta `gateway_mudo` (gateway sem reportar dados ao broker via uplink por > `RASTRO_GATEWAY_SILENT_SECS`, padrão 3600s).
    *   **Novo Alerta:** Criar `bateria_critica`. Baseado na tabela (ou view) de estado de energia (ex: `node_power` ou atributos do nó no envio de status). Disparo: `battery_level < 20` ou `voltage < 3.55V`. Focado idealmente em nós marcados como fixos ou repetidores solares.
    *   **Ciclo de Execução:** Garantir que o loop de avaliação rode no `ingest/__main__.py` condicionado a `RASTRO_ALERTS_ENABLED=1`.

### 2.3. API REST (FastAPI)
**Objetivo:** Expor o estado atual dos alertas para o visualizador.

*   **Arquivo Afetado:** `services/rastro_api/main.py` (ou router de alertas respectivo).
*   **Ações:**
    *   **Nova Rota:** Adicionar `GET /api/alerts`.
    *   **Autenticação:** Proteger o endpoint com a estratégia de autenticação atual (ex: Bearer / Sessão).
    *   **Consulta:** Fazer JOIN da tabela `alert_state` (onde `cleared_at IS NULL`) com `node_info` ou tabela equivalente para retornar nomes amigáveis dos nós/barcos. O usuário `rastro_viewer` já tem permissão para SELECT.
    *   **Contrato de Dados (JSON):**
        ```json
        [
          {
            "alert_id": "uuid",
            "node_id": "!1234abcd",
            "node_name": "Repetidor Rio Alto",
            "alert_type": "bateria_critica",
            "severity": "high",
            "triggered_at": "2026-10-08T02:00:00Z",
            "details": {"battery_level": 15, "voltage": 3.4}
          },
          {
            "alert_id": "uuid",
            "node_id": "gateway_barco_1",
            "node_name": "Gateway Barco 1",
            "alert_type": "gateway_mudo",
            "severity": "critical",
            "triggered_at": "2026-10-08T01:30:00Z",
            "details": {"last_seen": "2026-10-08T00:30:00Z"}
          }
        ]
        ```

### 2.4. Visualizador Web (SolidJS)
**Objetivo:** Mostrar badges discretos e auto-atualizáveis no mapa e listas.

*   **Arquivos Afetados:** `web/src/api/alerts.ts`, `web/src/components/NodeList.tsx`, `web/src/components/NodeInspector.tsx`, `web/src/fixtures/alertsFixture.ts`.
*   **Ações:**
    *   **Polling:** Implementar busca na rota `/api/alerts` com intervalo de 15s a 30s. Atualizar o store global de alertas.
    *   **Componentes UI:**
        *   Adicionar indicador 📡 **Sem sinal (>1h)** em `NodeList` e `NodeInspector` quando houver um alerta `gateway_mudo` para o nó.
        *   Adicionar indicador 🪫 **Bateria crítica** quando houver o alerta correspondente.
    *   **Limpeza:** Os alertas resolvidos (não mais retornados pela API porque `cleared_at IS NOT NULL`) sumirão naturalmente da interface no próximo tick do polling.
    *   **Mock e Testes:** Criar `alertsFixture.ts` com dados simulados para rodar em modo `dev:test`. Ajustar os testes do Vitest para contemplar a exibição condicional dos badges.

## 3. Estratégia de Testes

*   **Testes Unitários (Gateway/Ingest):**
    *   Garantir que a remoção das regras de retenção não quebra a suíte principal (`cd services/rastro_gateway && .venv/bin/pytest -q`).
    *   Escrever testes para o motor de alertas `bateria_critica` injetando estados de voltagem simulados.
*   **Testes Unitários (Visualizador):**
    *   Testar renderização dos badges em `NodeList` e `NodeInspector` baseados no store de alertas (`cd web && pnpm test`).
*   **Simulação Completa (Deploy/Sim):**
    *   Rodar `deploy/sim/run.sh` e simular um gateway mudo interrompendo o envio de pacotes. Verificar o disparo no banco de dados e subsequente exposição na API.
    *   Simular o retorno do gateway e atestar o preenchimento de `cleared_at`.

## 4. Rollout Seguro em Produção (CapRover)

1.  **Backup:** Realizar backup preventivo do banco PostgreSQL do CapRover.
2.  **Merge & Build:** Fazer merge das alterações e aguardar a publicação das novas imagens Docker.
3.  **Deploy Ingest & API:**
    *   Atualizar o `rastro-api` no CapRover.
    *   Atualizar o `rastro-ingest` no CapRover. Ele aplicará as mudanças de alerta e paralisará os expurgos sem impacto negativo.
4.  **Deploy Web:**
    *   Atualizar o container do visualizador `rastro-web`. Usuários verão os novos badges após o recarregamento.
5.  **Verificação Pós-Deploy:**
    *   Confirmar ausência de erros nos logs do ingestão em relação ao loop de alertas.
    *   Verificar estabilidade do endpoint de alertas na API.
