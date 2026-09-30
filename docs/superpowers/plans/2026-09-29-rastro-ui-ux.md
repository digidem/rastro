# Rastro UI/UX — plano de implementação

> [!NOTE]
> **Status:** Proposta arquitetural e plano de implementação histórico (baseline de 2026-09-29, pré-commit `5fdfe71`).
> A **Fase 1** (busca textual, filtros por categoria e condição, ordenação por frescor, painel flutuante retrátil, inspector com SVG por modelo de rádio, relógio reativo sem requisições adicionais, e desacoplamento do auto-enquadramento da câmera) foi integralmente implementada e coberta por testes no commit `5fdfe71`.
> A verificação unitária original citada na Seção 1 (`pnpm test` com 63 testes) reflete o baseline inicial daquela data; a suíte atual conta com 10 arquivos e 132 testes aprovados.

> Para o `cline`: executar uma tarefa por vez, com revisão humana dos contratos e das mudanças de sessão/mapa. Este documento é um plano; não autoriza publicar, implantar ou alterar dispositivos.

**Objetivo:** facilitar localizar, identificar e inspecionar a frota no mapa sem confundir posição antiga com presença atual.

**Arquitetura:** preservar `LocalState`, `DataProvider` e a instância única de MapLibre. Derivar lista, contagens e GeoJSON por funções puras e memos; componentes apresentam dados e emitem ações. Isolar solicitações de trilha/telemetria das atualizações de câmera e dos filtros.

**Stack:** SolidJS, MapLibre GL (instalado: 4.7.1), Tailwind 3, wrappers locais Ark UI/Park UI, Vitest e testes de navegador existentes. Sem novas dependências.

## Restrições globais

- Preservar os 63 testes existentes e seus contratos; acrescentar cobertura de comportamento sem trocar asserções exatas por asserções permissivas para esconder regressões.
- Preservar cookie HttpOnly, veto a credenciais em HTTP fora de loopback, geração de sessão, limpeza em logout/401 e comportamento de falha do logout.
- Não alterar basemap, protocolo PMTiles, glyphs locais, TLS, autenticação ou dependências junto com as melhorias visuais.
- Nenhuma fonte, sprite, ícone, serviço de busca, analytics ou CDN externa. Ícones de `solid-phosphor` já instalado ou SVG local.
- Não importar `fixtures/fleet.json` nem `nodesFixture.ts` no código de produção. Não publicar o registry integral, chaves, MACs, USBs ou notas operacionais.
- Não modificar os arquivos de trabalho já alterados por terceiros além das alterações explicitamente previstas na execução deste plano. Nenhum reset, remoção ou atualização do lockfile incidental.
- Textos em PT-BR; distinguir ausência de dados, erro, carregamento e resultado vazio.
- O limite de 12 h é uma regra visual inicial, não diagnóstico de falha de rádio ou de equipe.

## 1. Evidências e limites do checkout

| Evidência | Consequência |
|---|---|
| `web/src/store.ts`: `NodeInfo` tem apenas identificação básica, posição, bateria e `posTime` | Não há categoria, nome curto ou modelo utilizável pela UI hoje. |
| `web/src/providers/api.ts`: `nodeFromFeature` descarta `altitude_m`, `sats`, `time_source`, `received_at` | Inspector básico é possível com a API atual, ampliando o parser. |
| `web/src/fixtures/nodesFixture.ts`: a definição conhece `kind`, `shortName`, `hwModel`, mas `getLatestGeoJson` não os serializa | Ler diretamente a fixture no componente esconderia a incompatibilidade com produção. |
| `services/rastro_api/api/main.py`: `/track` e `/telemetry` já aceitam `from`, `to`, `limit`; `_window` usa 24 h; cap de 2.000 | Omitir parâmetros não significa “Todas”. |
| `services/rastro_api/api/queries.py`: últimas amostras limitadas, depois ordenadas ASC | Para a última telemetria, `limit=1` funciona dentro da janela solicitada. |
| `deploy/postgres/init/01-schema.sql`: view usa JOIN LATERAL com posição | Nós que nunca tiveram posição não chegam em `/latest` real. |
| Fixture “sem fix” tem coordenadas numéricas e `posTime=null` | Não desenhar essas coordenadas como posição confirmada. Não converter ausência em `[0,0]`. |
| `InitializeMap.tsx`: `style.load` e `load` compartilham inicialização protegida | Preservar pins e trilhas mesmo quando tiles falham; não esperar todos os tiles. |
| `InitializeMap.tsx`: clique abre popup, sem seleção; seleção concentra câmera e fetch em um efeito | Unificar seleção e separar efeitos com cuidado, mantendo os testes atuais. |
| Mock gera tempos relativos novamente em cada resposta e ignora query temporal | Os fixes parecem sempre recentes; o modo atual não prova envelhecimento nem janela. |
| Mock faz fetch OSM no processo Vite, com cache em memória e fallback transparente | Zero CDN no navegador não prova operação sem internet ou mapa disponível sem cache. |

Verificação nesta consulta: `pnpm test` passou: 7 arquivos, 63 testes. `pnpm typecheck` foi iniciado, permaneceu sem resultado e foi interrompido; não há aprovação desse check. Build, lint e navegador não foram executados nesta consulta de arquitetura. Isso é baseline unitário, não comprovação visual ou de operação offline. O plano descreve comportamento futuro, não funcionalidades já implementadas.

## 2. Priorização e decisão de UX

| Sugestão | Fase | Decisão |
|---|---|---|
| Busca por nome/hex e contador | 1A | Maior benefício imediato, sem requisições novas. Nome curto entra quando fornecido pelo contrato. |
| Chips por categoria e cores | 1B | Entrega imediata condicionada ao pacote de metadados abaixo; desconhecidos permanecem identificáveis. |
| “Sem fix / Offline” | 1A | Separar tipo de condição. Usar “Sem posição” e “Fix antigo”; conexão com a API é global. |
| Frescor, seleção e legenda | 1A/1B | Cor representa categoria; anel/contorno e texto representam frescor. Vermelho reservado para bateria crítica. |
| Inspector básico | 1A | Nome, ID, bateria, altitude, satélites, tempo e ações. Informação sempre selecionável na lista. |
| Telemetria expandida | 2 | Endpoint existe, mas requer parser, estados assíncronos, timestamps e defesa contra respostas atrasadas. |
| Enquadrar e painel responsivo | 1A | Recupera orientação e espaço de mapa em campo. |
| Janela 6h/24h, alternar pontos | 2 | Presets claros; manter 24h atual na Fase 1. |
| Contagem e distância | 2 | Contagem é exata para a resposta; distância é estimativa entre fixes, nunca percurso fluvial confirmado. |
| “Todas”, slider duplo, playback, heatmap e seletor amplo de camadas | 3 | Dependem de retenção/paginação, semântica temporal ou necessidade operacional validada. |

Três caminhos foram considerados: expansão do componente atual com heurísticas de nome (rápida, porém classificação frágil); componentes pequenos com contrato explícito (recomendado); redesign completo e múltiplas trilhas (maior superfície de regressão). A escolha é a segunda.

Fase 1A funciona com o backend atual. Fase 1B completa categorias/nome curto/modelo sem adivinhação. Se o contrato de metadados não estiver disponível, a UI continua operacional com “Tipo não informado”; o pacote de categorias não é considerado concluído apenas porque funciona no mock.

## 3. Semântica operacional

1. Categoria exclusiva: `boat`, `fixed_station`, `handheld`, `unknown`. “Todos” inclui desconhecidos; oferecer “Não informados” quando existirem, para evitar nós invisíveis sem explicação.
2. Condição independente: `all`, `no-position`, `stale`. Busca + categoria + condição combinam por AND. Nenhum desses filtros indica rádio offline.
3. Posição confirmada: coordenadas finitas dentro dos intervalos geográficos e timestamp ISO parseável. Timestamp ausente/inválido implica “Sem posição confirmada” no inspector e exclusão dos pins/enquadramento. Timestamp futuro acima de 5 min recebe “Horário do fix inconsistente”, sem badge de frescor; tolerância menor é clamp para idade zero.
4. `stale`: idade estritamente maior que 12 h. Não reduzir opacidade a ponto de esconder o nó; usar anel tracejado/contorno diferente, legenda e texto “Fix antigo”. Categoria desconhecida usa cinza `#94a3b8`.
5. Bateria: preservar valor recebido, mas valores fora de 0–100 não viram barra válida. `0` é válido; `null`/ausente é “Bateria não informada”. Valor `101`, se recebido, não significa 101%: mostrar “Valor fora da escala” até definir a semântica de alimentação externa. Faixas iniciais: <20 vermelho, 20–49 âmbar, >=50 verde, sempre com número/texto.
6. Data exata: `pt-BR`, zona explícita `America/Manaus` para operação no Javari, independente da zona do computador; rótulo “Hora local do Javari”. `timeSource=gateway` recebe “Horário de recebimento usado como referência”; não chamar automaticamente de instante medido pelo GPS.
7. Busca normaliza espaços, caixa e acentos; compara substrings de `nome`, `shortName`, `nodeId` e hex canônico de `nodeNum`, com/sem `!`. Não usar regex construída com entrada do usuário.
8. A lista filtrada determina os pins. Ao mudar filtro e esconder a seleção, desselecionar e limpar popup/trilha, sem mover câmera. O total de nós vem do store completo, não do GeoJSON.
9. Badge: “X de Y nós”; quando houver nós sem posição, legenda adicional “Z com posição no mapa”. “Enquadrar todos” permanece literal: enquadra todos os nós com posição, limpa os filtros e preserva a seleção. Sem posição válida, botão desabilitado com texto explicativo.
10. Inspector usa “Desselecionar”, que também limpa a trilha. “Limpar trilha” isolado não entra na Fase 1: manter seleção com trilha invisível exigiria estado separado e outra ação para restaurá-la.
11. Lista sem dados após sucesso: “Nenhum nó com posição recebido”. Lista sem match: “Nenhum resultado” + “Limpar filtros”. Antes da primeira resposta: “Aguardando dados…”. Erro de API tem mensagem própria e preserva dados anteriores da sessão quando for falha de rede.

## 4. Mapa de arquivos e responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `web/src/store.ts` | Dados da sessão, seleção, filtros, estado de carregamento de latest e ações de reset. |
| `web/src/lib/nodes.ts` (novo) | Busca, condição, timestamp, coordenadas, categoria e conversão de pins; funções puras. |
| `web/src/hooks/useViewerNodes.ts` (novo) | Memos compartilháveis para nós, selecionado, filtrados e contagens; relógio reativo. |
| `web/src/components/viewer/NodeFilters.tsx` (novo) | Input, chips e condição; recebe valores e callbacks, não faz fetch. |
| `web/src/components/viewer/NodeList.tsx` (novo) | Lista, estados vazios e seleção. |
| `web/src/components/viewer/NodeInspector.tsx` (novo) | Identificação, bateria, posição e ações; sem leitura de fixture. |
| `web/src/components/viewer/MapControls.tsx` (novo) | Enquadrar e abrir/fechar painel. |
| `web/src/MapWindow.tsx` | Composição, layout e ações existentes de sair/reconectar. |
| `web/src/InitializeMap.tsx` | Dono do mapa, layers, listeners, câmera e lifecycle da trilha atual. |
| `web/src/providers/MapProvider.tsx` | Contrato de comandos; nenhuma instância MapLibre no store. |
| `web/src/providers/api.ts` | Normalização de campos novos; na Fase 2, parâmetros de trilha e método de telemetria. |
| `web/src/providers/DataProvider.tsx` | Polling existente, estados de latest e reset centralizado de sessão. |
| `web/src/fixtures/nodesFixture.ts`, `web/vite-plugin-mock-api.ts` | Contrato mock fiel ao real e cenários estáveis. |
| `web/tests/unit/*.test.ts`, `web/e2e/viewer.spec.ts` | Regressões reativas, contrato HTTP e fluxos do navegador. |

Sem componente separado para cada ícone/campo; extrair `BatteryIndicator.tsx` apenas se houver uso compartilhado real entre lista e inspector. Não reformatar toda a pasta de wrappers upstream.

## 5. Estado e interfaces da Fase 1

Adicionar propriedades opcionais para compatibilidade com os objetos antigos e as asserções exatas dos testes:

```ts
export type NodeKind = "boat" | "fixed_station" | "handheld" | "unknown";
export type KindFilter = "all" | NodeKind;
export type ConditionFilter = "all" | "no-position" | "stale";

// Extensão de NodeInfo; manter os campos atuais.
shortName?: string | null;
kind?: NodeKind;
hwModel?: string | null;
altitudeM?: number | null;
sats?: number | null;
timeSource?: string | null;
receivedAt?: string | null;

// Extensão do estado; manter auth, nodes, selected, online e pollingGeracao.
query: string;                  // inicial ""
kindFilter: KindFilter;         // inicial "all"
conditionFilter: ConditionFilter; // inicial "all"
latestStatus: "idle" | "loading" | "ready" | "error"; // inicial "idle"
```

Ações exportadas em `LocalState`: `setQuery`, `setKindFilter`, `setConditionFilter`, `setLatestStatus`, `resetFilters`, `resetViewerState`. `resetViewerState` limpa seleção/filtros/status; a geração continua avançada pelo `clearSessionData` existente. Não criar setter público genérico.

`setNodes` continua com `reconcile`, dentro de `batch` com a limpeza da seleção caso ela desapareça. Preservar a exclusão de nós ausentes. Nunca substituir nodes por uma lista filtrada. A seleção usa `nodeNum`, não índice da lista.

Estado do painel é signal local de `MapWindow` (`sidebarOpen`); a API não precisa conhecê-lo. Ao mudar sessão, resetar esse signal. O relógio é signal compartilhado entre memos da lista/mapa, atualizado a cada 60 s, com `onCleanup(clearInterval)`; não armazenar `ageMinutes` nos nós.

Interfaces das funções puras:

```ts
hasConfirmedPosition(node: NodeInfo): boolean;
matchesQuery(node: NodeInfo, query: string): boolean;
matchesFilters(node: NodeInfo, filters: {
  query: string; kindFilter: KindFilter; conditionFilter: ConditionFilter;
}, nowMs: number): boolean;
fixAgeLabel(iso: string | null, nowMs: number): string;
nodeKind(node: NodeInfo): NodeKind;
nodesGeoJson(nodes: NodeInfo[], nowMs: number): GeoJSON.FeatureCollection;
```

`useViewerNodes()` produz accessors `allNodes`, `filteredNodes`, `selectedNode`, `mapNodes`, `nowMs`. Exemplo de derivação, não um segundo store:

```ts
const allNodes = createMemo(() => Object.values(localState.nodes));
const filteredNodes = createMemo(() => allNodes().filter((n) =>
  matchesFilters(n, {
    query: localState.query,
    kindFilter: localState.kindFilter,
    conditionFilter: localState.conditionFilter,
  }, nowMs()),
));
const selectedNode = createMemo(() => {
  const id = localState.selected;
  return id === null ? undefined : localState.nodes[id];
});
```

Criar o relógio uma vez por viewer, com owner Solid e cleanup, e fornecê-lo aos consumidores; não criar um intervalo por linha. Ler `props.node` dentro de JSX/memos; não desestruturar propriedades reativas em snapshots. Memos só derivam; ações/effects fazem mutações.

## 6. Tarefas da Fase 1 — ordem de execução

### Tarefa 1 — dados já disponíveis e regras puras

**Arquivos:** modificar `store.ts` e `providers/api.ts`; criar `lib/nodes.ts`; criar `tests/unit/nodes.test.ts`; estender `tests/unit/api.test.ts`.

- [ ] Escrever testes de regra com relógio fixo, cobrindo os exemplos abaixo.
- [ ] Rodar `pnpm exec vitest run tests/unit/nodes.test.ts tests/unit/api.test.ts` e observar a falha da funcionalidade ausente.
- [ ] Adicionar tipos opcionais e leitura dos campos somente quando a propriedade correspondente existir no payload. Exemplo: `...(Object.hasOwn(p, "altitude_m") ? { altitudeM: num(p.altitude_m) } : {})`. Aplicar o mesmo padrão aos demais campos novos, para manter o shape de respostas antigas.
- [ ] Implementar funções puras e rótulos; `num` continua rejeitando NaN/Infinity; timestamps inválidos não exibem “NaN”. `nodeKind` faz fallback explícito para `unknown`.
- [ ] Verificar os novos testes e os 63 antigos; revisar somente o diff desta tarefa.

Casos mínimos executáveis em `nodes.test.ts`, usando o `NodeInfo` básico do teste de API como `base`:

```ts
expect(matchesQuery({ ...base, nome: "Base Curuçá" }, "curuca")).toBe(true);
expect(matchesQuery({ ...base, shortName: "IQ1M" }, "iq1m")).toBe(true);
expect(matchesQuery({ ...base, nodeId: "!abcd1234" }, "ABCD")).toBe(true);
expect(hasConfirmedPosition({ ...base, posTime: null })).toBe(false);
expect(hasConfirmedPosition({ ...base, lat: 91 })).toBe(false);
expect(fixAgeLabel("data-invalida", Date.parse("2026-09-29T12:00:00Z")))
  .toBe("sem fix");
```

Acrescentar casos de 0% bateria, null, 101, altitude 0, sats 0, fix exatamente 12h e 12h+1ms, futuro >5min e hex sem prefixo. Não clamar integração MapLibre a partir desses testes puros.

### Tarefa 2 — contrato de metadados, pacote 1B

**Dono:** mantenedor de backend aprova o contrato; `cline` implementa o consumidor web após revisão. **Arquivos web:** `providers/api.ts`, `fixtures/nodesFixture.ts`, testes de API/mock. **Pacote backend separado:** `services/rastro_api/api/fleet_metadata.py` (novo), `api/main.py`, testes do loader/GeoJSON e documentação da configuração.

- [ ] Fixar um único contrato: propriedades opcionais `short_name`, `kind`, `hw_model` em `/latest`, sem renomear propriedades existentes. `kind` aceita `boat`, `fixed_station`, `handheld`; valores desconhecidos viram `unknown` no consumidor.
- [ ] No backend, carregar uma vez ao criar o app um arquivo opcional de metadados de implantação, configurado por `RASTRO_API_FLEET_METADATA_FILE`. Produzir somente `{node_num: {short_name, kind, hw_model}}`, lendo `identity.short_name`, `deployment.kind`, `hardware.hw_model`; validar node_num como inteiro não booleano dentro de uint32. Arquivo ausente/não configurado mantém o contrato antigo; configurado inválido gera aviso sem imprimir conteúdo do registry.
- [ ] Enriquecer somente features que já vieram da consulta `/latest`, por node_num. Nenhuma posição é criada pelo cadastro; nenhum SQL, tabela, bridge ou ingestão muda nesta tarefa. Não importar o pacote do gateway no serviço API.
- [ ] No mock, serializar os mesmos campos a partir de `FLEET_NODES` em `getLatestGeoJson` e no helper `getMockNodeInfoList`. Campo presente normalizado e campo ausente compatível têm testes separados.
- [ ] Testar payload antigo, campos nulos, categoria inválida, rádio desconhecido e nó sem cadastro. Não inferir tipo por nome, hardware ou papel Meshtastic: TRACKER não equivale a barco.
- [ ] Verificar o consumer com backend sem arquivo (degradação explícita) e com arquivo minimizado de implantação. A API continua autenticada; não servir um registry integral como asset público.

Aceite: busca por short name e cores/filtros concordam entre mock e real. Categorias dos 16 exemplos não podem ser copiadas como cadastro real: as seis bases simuladas e o cenário sem fix continuam exemplos de desenvolvimento.

### Tarefa 3 — filtros e estado da sessão

**Arquivos:** `store.ts`, `providers/DataProvider.tsx`, `hooks/useViewerNodes.ts`, novos `tests/unit/viewer-state.test.ts`, testes existentes de provider.

- [ ] Implementar os defaults/ações da seção 5; setters de filtro não fazem fetch.
- [ ] Acrescentar estados de latest ao polling: loading apenas na primeira carga; ready após sucesso; error após erro; geração verificada antes de qualquer escrita de sucesso ou erro. Dados anteriores permanecem visíveis em falha de rede.
- [ ] Chamar reset do viewer em `clearSessionData`, em `batch` com geração/nós/conexão. Testar que uma resposta antiga não restaura status nem dados depois do reset.
- [ ] Criar memos e relógio compartilhado; remover seleção órfã quando o nó sair do reconcile e seleção escondida quando filtros mudarem. Relógio e polling nunca centralizam a câmera.
- [ ] Testar filtros AND, contagens, seleção removida, atualizações de um nó via reconcile, reset de logout/401 e ausência de timers após unmount.

Aceite: o store completo mantém todos os nós, mesmo quando nenhum corresponde aos filtros; a troca de sessão zera as escolhas e dados do viewer.

### Tarefa 4 — sidebar de busca/lista

**Arquivos:** novos `NodeFilters.tsx`, `NodeList.tsx`; modificar `MapWindow.tsx`; novo `tests/unit/node-sidebar.test.ts`.

- [ ] Reutilizar `Input`, `Badge` e `Button` locais. Campo com label “Buscar nós”, ícone decorativo e botão “Limpar busca”. Chips são buttons `aria-pressed`, `type=button`, em grupo rotulado “Tipo de nó”; condição em select nativo rotulado, sem inventar componentes compostos.
- [ ] Interface `NodeFilters`: `query`, `kindFilter`, `conditionFilter`, `onQueryChange(string)`, `onKindChange(KindFilter)`, `onConditionChange(ConditionFilter)`, `onReset()`. `NodeList`: `nodes: NodeInfo[]`, `selected: number|null`, `nowMs: number`, `onSelect(number|null)` e `latestStatus`.
- [ ] Manter cabeçalho e filtros fora da área rolável da lista. Ordenar por nome com comparação pt-BR e desempate nodeNum; não ordenar por bateria ou frescor a cada tick.
- [ ] Implementar estados vazios da seção 3 e badge “X de Y nós”. Cada row mostra nome, tipo/texto quando conhecido, bateria e idade; estado de seleção tem contorno além da cor.
- [ ] Preservar sair, aviso de falha do logout e reconectar. Não mover essas operações para componentes que recebem props de apresentação.
- [ ] Testar entrada via teclado, busca sem acentos/hex, zerar filtros, seleção repetida desselecionando, lista vazia de sucesso e erro distinguíveis.

Como o include de Vitest atual é `tests/**/*.test.ts`, usar `createComponent` nos testes de componentes, seguindo os testes atuais, ou alterar explicitamente o include para aceitar `.test.tsx`. Não adicionar testes TSX que o runner não descobre.

### Tarefa 5 — inspector básico

**Arquivos:** novo `NodeInspector.tsx`; `MapWindow.tsx`; novo `tests/unit/node-inspector.test.ts`.

- [ ] Interface: `node: NodeInfo`, `nowMs: number`, `onCenter()`, `onDeselect()`. Receber o nó atual via memo do store, não cópia feita no clique.
- [ ] Reutilizar Card, Text e Button. Identificação em `dl`; ID e nome longo com `break-words`/`overflow-wrap:anywhere`; modelo/short name ausentes indicam “Não informado”.
- [ ] Bateria usa `<meter min="0" max="100">` com label e percentual ou wrapper Progress existente com faixa/label verificados. Valor inválido/ausente exibe texto, sem barra enganosa. Não mudar o wrapper inteiro para colorir um card.
- [ ] Mostrar altitude em metros, satélites em vista, idade e data exata na zona definida. Não mostrar tensão/canal/uptime na Fase 1. São amostras diferentes com outro timestamp.
- [ ] Centralizar desabilitado sem posição confirmada; desselecionar sempre disponível. Card no topo da região de conteúdo da sidebar; cabeçalho/filtros e rodapé permanecem acessíveis mesmo em pouca altura, sem impedir rolagem do conteúdo.
- [ ] Testar bateria 0/null/101, altitude/sats 0, texto hostil renderizado como texto e atualização do nó selecionado após polling.

### Tarefa 6 — pins, seleção e efeitos MapLibre

**Arquivos:** `InitializeMap.tsx`, `lib/nodes.ts`, `tests/unit/initialize-map.test.ts`.

- [ ] Serializar `nodeNum`, categoria, estado do fix e nome em cada feature, com `id=nodeNum`. Filtrar posições não confirmadas e coordenadas inválidas antes de `setData`, usando os mesmos memos/regras da lista.
- [ ] Manter `nodes-circle` e `nodes-label` e as sources atuais. `circle-color` usa expressão `match` em categoria: barco `#38bdf8`, base `#f59e0b`, portátil `#10b981`, default `#94a3b8`. Acrescentar layer de halo/seleção com dados locais; geometria simbólica por categoria fica para Fase 3. Nunca representar sem fix em uma posição inventada.
- [ ] Clicar no pin continua abrindo popup e, quando houver nodeNum válido presente no store, também seleciona. Preservar o fallback de popup para features sem nodeNum dos testes atuais. Texto deve continuar escapado; substituir/remover o popup anterior antes de abrir outro.
- [ ] Dividir o efeito de seleção em: câmera na mudança explícita de ID; pedido de trilha ligado ao ID/identificador e ciclo de existência do nó; desenho quando a resposta aceita chega. Atualização de posição, cor, bateria, relógio ou filtro não dispara flyTo/fetch. O efeito detecta remoção do nó sem subscrever desnecessariamente todos os seus campos.
- [ ] Na Fase 1, manter `trackStatus: "idle" | "loading" | "ready" | "empty" | "error"` como signal do dono do mapa e expor accessor no MapContext para o inspector. Definir idle ao limpar, loading antes do pedido, empty quando não houver linha nem pontos, ready após resposta aceita, error após erro aceito. O pacote da Fase 2 transfere esse estado para `useSelectedTrack`, sem duplicá-lo.
- [ ] Troca de nó/desseleção/remoção/geração/unmount incrementa `trackReq` e limpa trilha imediatamente. Capturar também geração e instância; aceitar resposta apenas se todas ainda corresponderem. Incluir o mesmo veto antes de mutações no catch. `onCleanup` invalida resposta antes de remover mapa.
- [ ] Erro de trilha não pode deixar a trilha de A visível quando B foi selecionado. 401 da geração atual deve chamar o fluxo de encerramento do provider; erro de rede mostra aviso de trilha indisponível, sem chamar logout.
- [ ] Preservar inicialização idempotente em style.load/load e fallback OSM/PMTiles. Event handlers são registrados uma vez; cleanup não deixa listeners/timers/popup ativos.
- [ ] Estender testes com clique→seleção, troca A→B fora de ordem, desseleção, nó removido, geração e unmount. Contar `track`/`flyTo` após mudança apenas de bateria/posição/relógio: não devem aumentar.

Aceite: o inspector, o pin selecionado e a trilha sempre pertencem ao mesmo nó. Manter os testes atuais de popup, sessão e basemap; novos métodos no FakeMap devem representar métodos reais, não falsificar seu resultado para obter verde.

### Tarefa 7 — câmera e responsividade

**Arquivos:** `providers/MapProvider.tsx`, `InitializeMap.tsx`, `MapWindow.tsx`, novo `MapControls.tsx`, testes de mapa/sidebar.

- [ ] Acrescentar comandos no contexto: `centerNode(nodeNum: number): void`, `fitAllNodes(): void`, `resizeMap(): void`, `setViewportPadding(padding: {top:number;right:number;bottom:number;left:number}): void`. São seguros antes de mapa pronto e após cleanup. Não expor o Map diretamente aos cards.
- [ ] Centralizar usa posição atual do store, zoom mínimo 12, animação reduzida a zero com `prefers-reduced-motion`. Clique explícito no botão funciona mesmo com o mesmo selected, sem novo pedido de trilha.
- [ ] Enquadrar limpa filtros, usa todos os nós com posição, `maxZoom=12` e não reinicia permanentemente o enquadramento automático: polling posterior não deve desfazer o enquadramento manual. Preservar a regra atual de primeira carga/entrada de nós antes de interação.
- [ ] Separar container MapLibre (`absolute inset-0`) do overlay UI; pai `relative h-full min-h-0`. Evitar filhos de controle dentro do container gerenciado pela biblioteca.
- [ ] Sidebar: `w-[min(22rem,calc(100vw-1rem))]`, à direita com margem de 8px; área interna `min-h-0`, lista rolável. Botão de recolher disponível em todos os tamanhos; em telas <768px começa recolhida, em telas maiores começa aberta. Colapsar não desmonta o mapa nem perde seleção.
- [ ] Botão “Nós da malha” usa aria-controls/aria-expanded e devolve foco ao botão quando fecha; esconder conteúdo remove controles do tab order. Na Fase 1 usar painel não modal, sem backdrop que bloqueia mapa. Se usar Drawer modal na Fase 3, validar trap e restituição de foco.
- [ ] Medir overlay com ResizeObserver e atualizar padding; padding básico 24px, somar largura efetivamente ocupada à direita quando aberto, limitado para manter área útil em tela estreita. Câmera automática e comandos explícitos usam o mesmo cálculo. Mudança de padding não dispara flyTo. Usar API compatível com 4.7.1; não copiar `retainPadding` de exemplos atuais sem verificar os tipos instalados. Evitar acumular padding entre comandos.
- [ ] Chamar resize após mudança real de dimensões do container, usando requestAnimationFrame; se apenas o overlay muda, não recriar mapa. Observer e frame pendente são cancelados no cleanup.
- [ ] Testar nenhum nó/um nó/múltiplos, sidebar aberta/fechada, botão repetido, resize e gesto anterior. Conferir visualmente o pin no espaço descoberto pela sidebar.

Estilo comum: fundo opaco `bg-slate-950`, texto `text-slate-100`, secundário `text-slate-300`, bordas visíveis, foco com `focus-visible:ring-2 focus-visible:ring-sky-400`. Alvos de toque de pelo menos 44px; chips com wrap, não rolagem horizontal escondida. Usar classes literais ou mapa fixo de classes; não construir `bg-${cor}` dinamicamente. Contraste deve ser verificado sobre tiles claros e escuros; hex de marcador não é cor de texto universal.

### Tarefa 8 — gate de entrega da Fase 1

**Arquivos:** testes novos/existentes, cenários mock e documentação de desenvolvimento apenas no trecho pertinente.

- [ ] Rodar, de `web/`: `pnpm test`, `pnpm typecheck`, `pnpm build`, `pnpm lint`. Primeiro registrar baseline de lint; não reformatar wrappers upstream para corrigir dívida fora do escopo. A entrega exige zero regressões e nenhum erro novo nos arquivos tocados.
- [ ] Tornar o cenário mock estável por inicialização do processo: todas as rotas usam o mesmo instante de origem dos fixes, sem renovar sua idade a cada fetch. Aceitar relógio injetado nas funções existentes para os testes. Acrescentar cenário separado com fix >12h e timestamp futuro; manter 16 nós do cenário padrão.
- [ ] No navegador, iniciar `pnpm dev:mock`, validar 16 itens com filtros limpos e 15 posições confirmadas no cenário padrão, busca por short name/hex, tipos, sem posição, inspector, seleção via pin/lista e enquadramento. Categoria é independente do grupo “9 oficiais + 6 bases + 1 cenário”; não usar esses números como contagem de tipos.
- [ ] Usar browser-harness para interação/inspeção visual conforme instrução local. As suítes E2E existentes continuam com `pnpm e2e` no ambiente documentado por elas; seu `baseURL` padrão é 8081, distinto do Vite 5173. Não declarar que o mock autenticado prova login/logout reais.
- [ ] Validar pelo menos 390×844, 768×1024 e 1366×768, zoom do navegador 200%, teclado e nomes longos. Não criar screenshots ouro antes de confirmar as dimensões/alvos de toque.
- [ ] Offline significa API local acessível e internet indisponível. Pré-provisionar PMTiles/glyphs locais ou cache OSM suficiente, bloquear egress de internet do navegador e do serviço, e repetir interação. Apenas simular “browser offline” desconecta também a API e testa outro cenário.
- [ ] No teste mock estritamente offline, interceptar tiles no processo Vite ou acrescentar opção dev explícita que impede `fetch` externo. Fallback transparente comprova tolerância, não presença de basemap útil.
- [ ] Com backend real: verificar API sem metadados, API enriquecida, 401 com trilha pendente, logout falhando e rede temporariamente perdida. Nenhuma resposta da sessão antiga restaura pins/inspector/trilha.
- [ ] Inspecionar bundle/rede para confirmar ausência de fixtures completas, token de produção, URLs CDN e novas requisições externas. Não salvar tokens ou coordenadas em logs de validação.

Aceite de Fase 1: 63 testes preservados + novos testes passando; typecheck/build sem erros novos; filtros não requisitam API; relógio/polling não movem câmera; categorias provadas no contrato real; inspector e pin concordam; teclado/toque e operação offline validados. Checks impedidos por ambiente são reportados como não verificados.

## 7. Fase 2 — três módulos depois do gate anterior

### Tarefa 9 — janela de trilha e pontos

**Arquivos:** `store.ts`, `providers/api.ts`, `InitializeMap.tsx`, novo `components/viewer/TrackControls.tsx`, mock/plugin, testes de HTTP/mapa/mock.

- [ ] Adicionar `trackWindow: "6h" | "24h"` (default 24h), `showTrackPoints: boolean` (default true), reset na sessão. `ApiClient.track(node, options?: {from?:string;to?:string;limit?:number})` permanece compatível; chamada sem options mantém URL antiga. Query via URLSearchParams, datas UTC e node via encodeURIComponent.
- [ ] Cada seleção/troca de preset captura um único `to=now`; `from=to-6h/24h`, `limit=2000`. Mudança do preset atualiza trilha, não centraliza. Não refazer pedido a cada tick de idade.
- [ ] Mock parseia e aplica `from`/`to` inclusivos e limit, cap de 2000, últimas amostras ordenadas ASC. Recriar LineString dos pontos aceitos; zero/um ponto não gera linha válida. Query inválida tem 400 conforme API, não coleção completa silenciosa.
- [ ] Usar `SegmentGroup` local para 6h/24h, `Switch` existente para pontos com label. Alternar pontos chama setLayoutProperty na layer, sem fetch ou refazer linha.
- [ ] Tratar corridas por seleção + janela + geração + instância; atualizar chave de request para cada troca. Exibir carregando/vazia/erro no controle da trilha.
- [ ] Acrescentar teste com amostras a 2h/8h/26h: 6h retorna 1 ponto, 24h retorna 2; inverter ordem de resposta 6h/24h; toggling de pontos não aumenta calls de track.

### Tarefa 10 — resumo da trilha

**Arquivos:** novo `lib/track.ts`, `TrackControls.tsx`, estado de trilha/memos e testes.

- [ ] Contagem derivada de points válidos retornados. Distância estimada em km por Haversine entre pontos consecutivos em ordem temporal; raio terrestre 6.371.000m. Não usar distância entre extremos nem adicionar biblioteca.
- [ ] Zero pontos: “Sem fixes na janela”; um ponto: contagem 1, distância “Não estimável”; coordenadas repetidas: distância zero. Não ligar pontos descartados como inválidos sem indicar interrupção; lacunas grandes devem receber aviso, sem limiar operacional inventado.
- [ ] Mostrar “Distância estimada entre fixes”, uma casa decimal. Se 2.000 pontos forem retornados, mostrar “Possível limite de pontos atingido”; sem total fornecido pelo servidor não afirmar truncamento com certeza.
- [ ] Criar estado da trilha uma única vez, compartilhado entre resumo e desenho: `{nodeNum, requestKey, status, line, points}` em memória da sessão. Mover a aceitação da resposta para um hook dedicado `useSelectedTrack.ts`; nem inspector nem mapa faz um segundo pedido. Hook limpa em seleção/janela/sessão/unmount e expõe accessors ao desenho/resumo.
- [ ] Testar pontos repetidos, um/zero, ordem temporal, coordenada inválida, cap e ausência de duplicação de solicitações. Preservar a linha retornada pela API no caminho compatível; resumo usa points e informa se não existirem points suficientes.

### Tarefa 11 — telemetria sob demanda

**Arquivos:** `providers/api.ts`, novo `hooks/useNodeTelemetry.ts`, novo `components/viewer/NodeTelemetry.tsx`, `NodeInspector.tsx`, testes API/hook/sessão.

- [ ] Adicionar método `telemetry(node, options?: {from?:string;to?:string;limit?:number})` usando o mesmo request autenticado. Retornar amostras normalizadas com `batteryLevel`, `voltage`, `channelUtil`, `airUtilTx`, `uptimeS`, `telemTime`; rejeitar números não finitos, preservar zeros, null em dados ausentes.
- [ ] Ao expandir “Telemetria”, pedir para o nó selecionado `limit=1`, com janela explícita de 24h. Mostrar “Sem telemetria nas últimas 24h” se vazio; não afirmar ausência histórica. Refresh explícito enquanto expandido, sem timer adicional na entrega inicial.
- [ ] Estados `idle/loading/ready/empty/error`, timestamp independente do fix e etiqueta de idade. Não substituir silenciosamente bateria do latest por leitura histórica de outra janela.
- [ ] Seleção/geração/unmount invalida resposta; 401 atual chama clearSessionData + login; erro de rede mantém sessão e mostra erro recuperável. Não usar cache persistente ou localStorage; credenciais nunca entram no hook.
- [ ] Testar zero volts/canal, uptime zero, amostra antiga, payload vazio, API velha sem rota, 401, troca rápida A→B, collapse/unmount e logout durante fetch. API velha sem rota mostra indisponibilidade de telemetria sem inutilizar inspector básico.

## 8. Fase 3 — contratos necessários antes de implementar

1. **Inventário sem posição real:** endpoint autenticado de inventário separado ou ampliação explícita de `/latest` com geometry null. A decisão exige separar cadastro, último contato e posição; parser atual rejeita geometry null. Não mudar JOIN para criar coordenadas falsas. Último contato (`last_seen`) tem semântica diferente do fix e precisa aparecer como tal.
2. **Histórico amplo:** “Todas” só depois de definir horizonte/retention, paginação e indicação de cobertura/truncamento. Sem isso, usar janelas limitadas com rótulos honestos. Não remover a proteção de 2.000 pontos para atender um botão.
3. **Formas por tipo, multilinha, playback e slider:** validar com operadores se ajudam decisões no rio, definir legenda e performance. Heatmap de fixes de barcos representa frequência de amostragem e passagem, não cobertura de rádio nem presença atual; não portar heatmap upstream automaticamente.
4. **Camadas e drawer modal:** apenas depois de necessidade real; basemap local indisponível deve aparecer como estado, nunca introduzir fallback de CDN. Drawer usa o wrapper Ark existente, foco/escape/restauração testados na versão instalada.

## 9. Handoff e revisão

Ordem recomendada: dados/regras → contrato de metadados → estado → busca/lista → inspector → integração de pins → câmera/layout → gate. Fase 1A pode prosseguir enquanto o mantenedor fecha o contrato 1B. Fase 2 começa com o gate concluído.

Cada tarefa termina com entrega pequena e revisável: arquivos tocados, comportamento antes/depois, testes realmente executados e limitações. Mudanças de contrato, sessão e requests fora de ordem recebem revisão sênior antes do próximo módulo. Não abrir um único pedido “redesenhar o mapa”; passar ao `cline` esta tarefa numerada e as restrições globais.

## Referências de arquitetura

- [Solid — memos](https://docs.solidjs.com/concepts/derived-values/memos): derivações cacheadas; usar getters em consumidores.
- [Solid — props](https://docs.solidjs.com/concepts/components/props): desestruturar valores de props pode quebrar reatividade.
- [Solid — onCleanup](https://docs.solidjs.com/reference/lifecycle/on-cleanup): dispose de timers/listeners/escopos.
- [MapLibre — padding](https://maplibre.org/maplibre-gl-js/docs/API/type-aliases/PaddingOptions/): enquadramento considerando overlays. Para APIs exatas, os tipos locais da versão 4.7.1 prevalecem sobre exemplos da documentação atual.
