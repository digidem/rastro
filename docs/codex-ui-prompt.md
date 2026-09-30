Você é o arquiteto sênior consultado para definir o plano de evolução da UI/UX do visualizador web do projeto **Rastro** (`web/`).

### Contexto do Projeto
- O **Rastro** é uma plataforma de monitoramento tático e rastreamento offline-first sobre malha Meshtastic LoRa (PostgreSQL + FastAPI + SolidJS).
- O frontend (`web/`) foi originalmente derivado de `meshtastic/map` (upstream: SolidJS, MapLibre GL, TailwindCSS, Ark UI / Park UI), mas adaptado para operação tática, segura e 100% offline (basemap PMTiles local e proxy OSM com cache; fontes OFL locais; autenticação por token/cookie HttpOnly; zero CDNs externas).
- O caso de uso real é a vigilância territorial indígena no Vale do Javari (AM): barcos de patrulha nos rios (Itaquaí, Ituí, Curuçá, Jaquirana, Médio Javari), bases fixas e rastreadores portáteis em equipe de campo.
- Recentemente adicionamos fixtures completas da frota com 16 nós (9 oficiais + 6 bases de malha + 1 sem fix) e um plugin mock para Vite (`pnpm dev:mock`).

### Comparação com o upstream `meshtastic/map`
- **Upstream (`meshtastic/map`)**:
  - Circle pins com interpolação de cor por idade do fix (azul <1h -> pêssego -> vermelho >30d).
  - Camada de heatmap (`gateways-heat`) por densidade/idade.
  - Sidebar com busca de gateways (`MagnifyingGlassIcon` + Input).
  - Draft de slider duplo no topo para janela temporal (`Slider value={[33, 66]}`).
  - Focado em gateways estáticos da rede pública global, sem noção de barcos, telemetria detalhada ou trilhas de patrulha.
- **Rastro Atual (`web/`)**:
  - Sidebar simples com lista de nós (nome, bateria %, idade do fix em PT-BR: "agora", "há 12 min", "sem fix").
  - Seleção de nó desenha a trilha (`LineString` + pontos) e dá `flyTo`.
  - Popup básico ao clicar no círculo do nó (`<strong>nome</strong><br/>fix: dd/mm/aaaa`).
  - Sem busca, sem filtros por tipo, sem painel de telemetria expandido, sem controle de camada ou janela de trilha, sem botão de re-enquadramento.

### Sugestões de UI/UX Levantadas
1. **Busca e Filtros Rápidos na Barra Lateral**:
   - Campo de busca instantânea (por nome, nome curto ou hex `!xxxx`).
   - Chips/filtros por categoria: "Todos", "Barcos" (boats), "Bases" (fixed), "Portáteis" (handhelds) e "Sem fix / Offline".
   - Badge com contagem de nós visíveis/filtrados.
2. **Diferenciação Visual dos Nós no Mapa**:
   - Cores e estilos distintos por categoria (Barcos: azul/cyan `#38bdf8`, Bases fixas: âmbar `#f59e0b`, Portáteis: esmeralda `#10b981`).
   - Indicação visual de frescor do fix (anel com opacidade reduzida ou tom esmaecido se fix >12h ou sem fix).
3. **Painel de Detalhes do Nó (Card Inspector)**:
   - Ao selecionar um nó, exibir card detalhado no topo da sidebar ou drawer com:
     - Identificação (nome, short name, node_id, modelo do rádio se conhecido).
     - Medidor visual de bateria (ícone/barra colorida: verde/amarelo/vermelho).
     - Altitude (m), satélites em vista (`sats`).
     - Idade relativa e data/hora exata do fix.
     - Telemetria se disponível (tensão V, utilização de canal %, uptime).
     - Botões de ação: "Centralizar" e "Limpar trilha / Desselecionar".
4. **Controles de Trilha e Janela Temporal**:
   - Seletor de janela de tempo para trilha (ex.: "6h", "24h", "Todas").
   - Alternância para mostrar/ocultar pontos de fix ao longo da linha.
   - Indicador de total de pontos e extensão da rota.
5. **Controles Rápidos do Mapa e Responsividade**:
   - Botão flutuante "Enquadrar todos" (fitBounds) no canto superior do mapa.
   - Botão para colapsar/expandir a barra lateral em telas menores (tablets/laptops em campo).

### Sua Tarefa
1. Avalie as sugestões acima sob a ótica de UI/UX, arquitetura SolidJS reativa e facilidade de manutenção.
2. Indique o que deve ser priorizado agora (Fase 1 imediata) vs o que pode ficar para fases posteriores.
3. Elabore um **plano técnico passo-a-passo detalhado e modular** para implementação pela nossa equipe (que será delegado ao `cline` como junior), especificando:
   - Modificações de estado (`store.ts`)
   - Novos componentes SolidJS ou alterações em `MapWindow.tsx` e `InitializeMap.tsx`
   - Estilização TailwindCSS e componentes Ark/Park UI
   - Manutenção de integridade (zero quebra dos 63 testes existentes, 100% offline, compatibilidade com o modo mock e o backend real)
