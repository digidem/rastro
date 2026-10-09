# TASK — Régua: medir distâncias no mapa

Branch `feat/regua` (worktree `../rastro-regua`). Cada tarefa abaixo é implementada
por um subagente, revisada e commitada antes da próxima. Todo o trabalho fica em `web/`.

## Decisões do dono (2026-10-09)

- Botão da régua (ícone `RulerIcon` de `solid-phosphor/regular`) na coluna da direita
  do `MapControls`, **logo abaixo do botão de período (relógio)**.
- **Caminho de vários pontos.** Cada clique adiciona um vértice. O painel mostra o total
  e cada segmento. Duplo clique ou "Concluir" fecha o caminho; um clique depois de
  concluído começa uma medição nova. Dois pontos é só o caso mais simples.
- **Encaixe em nós.** Na régua, clicar sobre/perto de um pin adiciona um vértice
  ancorado no nó (`{ tipo: "no", nodeNum }`). Vértice ancorado **segue o nó ao vivo**:
  a coordenada vem sempre de `localState.nodes[nodeNum]`, então a distância se atualiza
  a cada rodada de polling.
- **Painel:** distância por segmento e total; rumo por segmento e em linha reta
  (primeiro→último); chegada estimada quando o PRIMEIRO vértice é um nó em movimento com
  velocidade conhecida; vértices arrastáveis (mouse e toque); "Copiar" copia coordenadas
  + distância como texto.
- **Entrada pelo nó:** botão "Medir daqui" no popup do pin e no `NodeInspector`. Liga a
  régua com o vértice 0 ancorado no nó.
- Com a régua ligada, cliques NÃO selecionam nó, não abrem popup e não desselecionam.
- `Escape` sai da régua. Sair apaga a medição.

## Convenções (ler antes de codar)

- Identificadores, comentários e textos de UI em **português** (veja o código vizinho).
- Imports com sufixo `.js`/`.jsx` (como nos arquivos existentes).
- Testes de libs puras em `web/tests/unit/*.test.ts`; testes de componente ao lado do
  componente (`*.test.tsx`, ver `MapControls.test.tsx`).
- Gates de cada tarefa, rodados em `web/`: `pnpm test` (base: 329 passando) e
  `pnpm biome check src tests`. NÃO rode `pnpm typecheck` (não termina neste ambiente).
- Não mexa em arquivos fora dos listados na tarefa, exceto imports.
- Não faça commit. O revisor commita.
- Dados de teste: só coordenadas fictícias (ex.: perto de lon -70.0, lat -5.0).

---

## T1 — Lib pura de geometria `web/src/lib/regua.ts`

Criar `web/src/lib/regua.ts` e `web/tests/unit/regua.test.ts`.

```ts
export type LngLat = [number, number];

/** Distância geodésica em metros (haversine, R = 6_371_008.8). */
export function haversineM(a: LngLat, b: LngLat): number;

/** Distância de cada segmento (tamanho n-1). */
export function distanciasSegmentos(pts: readonly LngLat[]): number[];

/** Soma dos segmentos; 0 com menos de 2 pontos. */
export function distanciaTotalM(pts: readonly LngLat[]): number;

/** "850 m" (< 1000 m, inteiro); "1,2 km" (< 100 km, 1 casa, vírgula);
 *  "134 km" (>= 100 km, inteiro). Separador decimal "," (pt-BR).
 *  Arredonde ANTES de escolher a faixa: 999,6 m vira "1,0 km", não "1000 m";
 *  99 960 m vira "100 km", não "100,0 km". */
export function rotuloDistancia(m: number): string;

/** Rosa de 8 pontos em PT + graus inteiros: "NE 47°", "L 90°", "SO 225°".
 *  Pontos: N, NE, L, SE, S, SO, O, NO (setores de 45° centrados em cada um).
 *  Graus arredondados; 359,6 vira "N 0°". null → "—". */
export function rotuloRumo(graus: number | null): string;

/** Rumo inicial de a para b (reusa calcularBearing). */
export function rumo(a: LngLat, b: LngLat): number | null;

/** Abaixo disso o "deslocamento" é ruído de multipath (AGENTS.md, lição 9). */
export const VELOCIDADE_MIN_ETA_KMH = 2;

/** Tempo em ms para `distanciaM` a `velocidadeKmh`. null se a velocidade for null,
 *  não finita ou menor que VELOCIDADE_MIN_ETA_KMH. */
export function etaMs(distanciaM: number, velocidadeKmh: number | null): number | null;

/** Texto para a área de transferência: uma linha por vértice
 *  "1. -5.00000, -70.00000 (Nome do nó)" (lat, lon, 5 casas; nome só em vértice de nó),
 *  depois "Total: 1,2 km". */
export function textoCompartilhar(
  pts: readonly { pos: LngLat; nome?: string | null }[],
): string;
```

- Reusar `calcularBearing` de `./bearing.js` (não reimplementar).
- NÃO reusar `distanciaM` de `dwell.ts`: é aproximação equirretangular local; a régua
  pode cobrir centenas de km.

Testes (mínimo): haversine de 1° de latitude ≈ 111 195 m ± 1 m; distância zero;
caminho de 3 pontos soma os segmentos; `rotuloDistancia` em 0, 999.4, 999.6, 1000, 1234,
99_949, 99_960, 100_000, 134_400; `rotuloRumo` em 0, 22.4, 22.6, 90, 180, 225, 315, 359.6,
null; `etaMs` com null/0/1.9/2/10 km/h; formato de `textoCompartilhar`.

## T2 — Estado no store `web/src/store.ts`

Em `web/src/store.ts`:

```ts
/** Vértice da régua: ponto livre ou ancorado num nó (segue o nó ao vivo). */
export type PontoRegua =
  | { tipo: "livre"; lon: number; lat: number }
  | { tipo: "no"; nodeNum: number };

export interface EstadoRegua { ativa: boolean; concluida: boolean; pontos: PontoRegua[] }
```

Campo novo `regua: EstadoRegua` em `LocalState`, inicial
`{ ativa: false, concluida: false, pontos: [] }`. Não persiste.

Ações (exportadas pelo objeto `LocalState`):

- `ativarRegua(nodeNum?: number)` → ativa = true, concluida = false,
  pontos = nodeNum !== undefined ? [{tipo:"no",nodeNum}] : [].
- `desativarRegua()` → volta ao valor inicial.
- `adicionarPontoRegua(p: PontoRegua)` → sem efeito se não `ativa`. Se `concluida`,
  começa caminho novo `[p]` e concluida = false; senão, acrescenta. Ignora `p` igual ao
  último vértice (mesmo nó, ou ponto livre com lon/lat idênticos): o duplo clique emite
  2 cliques.
- `moverPontoRegua(i: number, lon: number, lat: number)` → vértice i vira
  `{tipo:"livre",lon,lat}` (arrastar um vértice de nó o desancora). Índice inválido: nada.
- `desfazerPontoRegua()` → remove o último vértice; concluida = false.
- `concluirRegua()` → concluida = true só se pontos.length >= 2.
- `limparRegua()` → pontos = [], concluida = false (continua ativa).
- `resetViewerState()` também chama `desativarRegua()`.

Acrescentar em `web/src/lib/regua.ts`:

```ts
export interface VerticeResolvido {
  pos: LngLat;
  nome: string | null;
  nodeNum: number | null;
  /** Índice no array `pontos` original (o arrasto precisa dele). */
  indice: number;
}

/** Resolve vértices em coordenadas. Vértice de nó lê `nodes`; nó ausente ou com
 *  lon/lat não finitos é DESCARTADO. */
export function resolverPontosRegua(
  pontos: readonly PontoRegua[],
  nodes: Readonly<Record<number, { lon: number; lat: number; nome: string }>>,
): VerticeResolvido[];
```

`PontoRegua` importado de `../store.js` como `import type`.

Testes em `web/tests/unit/regua-store.test.ts`: cada ação; o filtro de clique duplicado;
"clique depois de concluída começa caminho novo"; `resetViewerState` desliga a régua;
o resolvedor descarta nó ausente e acompanha um nó que muda de posição (`setNodes` 2x).
Limpar o store no `afterEach`.

## T3 — Desenho no mapa `web/src/lib/reguaMapa.ts`

Módulo dono da fonte e das camadas MapLibre da régua. Sem interações ainda.

```ts
export const FONTE_REGUA = "regua";
export const CAMADA_REGUA_LINHA = "regua-linha";
export const CAMADA_REGUA_VERTICES = "regua-vertices";
export const CAMADA_REGUA_ROTULOS = "regua-rotulos";

/** Adiciona fonte + camadas por cima de tudo, se ausentes (idempotente). */
export function garantirCamadasRegua(map: maplibregl.Map): void;

/** FeatureCollection da medição: uma LineString com todos os vértices (só com >= 2),
 *  um Point por vértice (props: indice, ancorado: boolean) e um Point no meio de cada
 *  segmento (props: rotulo = `${rotuloDistancia(d)} · ${rotuloRumo(b)}`).
 *  FC vazia com 0 pontos. */
export function geojsonRegua(pts: readonly VerticeResolvido[]): FeatureCollection;

/** setData na fonte (sem efeito se a fonte não existir). */
export function atualizarRegua(map: maplibregl.Map, pts: readonly VerticeResolvido[]): void;
```

Estilo: linha âmbar `#fbbf24`, largura 2.5, `line-dasharray: [2, 1.5]`; vértices
`circle` raio 6, preenchimento `#0f172a`, contorno âmbar 2.5; vértice ancorado com
contorno esmeralda `#34d399`; rótulos `symbol` com `text-font: ["Noto Sans Regular"]`
(única fonte de glyphs servida), tamanho 11, texto branco, halo `#090f0b` 2.5,
`text-allow-overlap: true`. Use `filter` por tipo de geometria / propriedade para
separar as camadas. Meio do segmento = média simples dos 2 vértices.

Ligar em `web/src/InitializeMap.tsx`:
- Em `aoCarregar` (roda em `style.load` e `load`), chamar `garantirCamadasRegua(map)`
  e logo depois `atualizarRegua(...)` com o estado atual.
- Um `createEffect` que lê `LocalState.localState.regua.pontos` e os nós (para o vértice
  ancorado seguir ao vivo) e chama `atualizarRegua(map, resolverPontosRegua(...))`.
  Siga como os outros efeitos deste arquivo obtêm o mapa (`currentView()`) e protegem
  contra mapa ainda não carregado.

Testes `web/tests/unit/reguaMapa.test.ts`: contagens e props de `geojsonRegua` para 0, 1
e 3 pontos (3 vértices + 1 linha + 2 rótulos); `garantirCamadasRegua` idempotente com
um mapa falso mínimo (`getSource`, `addSource`, `getLayer`, `addLayer`).

## T4 — Interações no mapa (`web/src/lib/reguaMapa.ts` + `InitializeMap.tsx`)

```ts
export interface DepsInteracoesRegua {
  ativa: () => boolean;
  /** Camadas de pin a consultar no encaixe (filtradas por map.getLayer na hora). */
  camadasNos: string[];
  /** Reusar nodeNumDoPin de InitializeMap. */
  nodeNumDe: (props: Record<string, unknown>) => number | null;
  adicionar: (p: PontoRegua) => void;
  mover: (i: number, lon: number, lat: number) => void;
  concluir: () => void;
  sair: () => void;
}

/** Instala os handlers da régua. Devolve uma função que remove todos. */
export function instalarInteracoesRegua(map: maplibregl.Map, deps: DepsInteracoesRegua): () => void;
```

Comportamento:
- `click` com `ativa()`: consulta as camadas de nós numa caixa de ±10 px em torno de
  `e.point` (encaixe amigável ao toque). Achou nodeNum → `adicionar({tipo:"no",nodeNum})`;
  senão → `adicionar({tipo:"livre", lon, lat})`.
- `dblclick` com régua ativa: `e.preventDefault()` (sem zoom) e `concluir()`.
- Arrasto: `mousedown` / `touchstart` em `CAMADA_REGUA_VERTICES` com régua ativa → lê
  `indice` da feature, `e.preventDefault()`, `map.dragPan.disable()`; no `mousemove` /
  `touchmove` chama `mover(indice, lng, lat)`; no `mouseup` / `touchend` reabilita o
  `dragPan`. Arrasto que moveu NÃO pode também adicionar vértice (ignore o próximo
  `click` logo após um arrasto que moveu).
- Cursor: `crosshair` no canvas com régua ativa; `move` sobre um vértice. A função
  exportada `definirCursorRegua(map, ativa: boolean)` aplica `crosshair` / `""`.
- `keydown` `Escape` em `document` com régua ativa → `sair()`.

Em `InitializeMap.tsx`:
- Instalar uma vez em `aoCarregar` (proteja contra instalação dupla: `aoCarregar` roda
  em `style.load` e em `load`). Remover no `onCleanup` do componente.
- Os três `map.on("click", ...)` existentes (pin do nó, pin de parada, área livre)
  retornam cedo quando `LocalState.localState.regua.ativa` for true. O handler de
  `mouseleave` de `nodes-circle` que zera o cursor deve restaurar `crosshair` se a régua
  estiver ativa.
- Efeito: `definirCursorRegua(map, regua.ativa)`.

Testes `web/tests/unit/reguaMapa-interacoes.test.ts` com mapa falso mínimo (guarda
handlers de `on`/`off`, stub de `queryRenderedFeatures`, `dragPan`, `getCanvas`):
clique adiciona ponto livre; clique perto de nó adiciona vértice de nó; clique com régua
inativa não faz nada; dblclick chama `concluir` + `preventDefault`; arrasto chama `mover`
e o clique seguinte é ignorado; Escape chama `sair`; a função de limpeza remove os handlers.

## T5 — Botão da barra + painel de leitura

`web/src/components/viewer/MapControls.tsx`: depois do bloco do período, botão da régua
(mesmo `btnIcone`). `aria-label="Régua: medir distâncias"`, `aria-pressed` =
`regua.ativa`, `title="Medir distâncias"`. Ligado: ícone âmbar + `ring-2 ring-amber-400`.
Clique alterna `ativarRegua()` / `desativarRegua()`.

Novo `web/src/components/viewer/ReguaPainel.tsx`, renderizado em `web/src/MapWindow.tsx`
dentro do overlay, embaixo ao centro (`absolute bottom-3 left-1/2 -translate-x-1/2`,
`pointer-events-auto`, `max-w-[calc(100vw-1rem)]`), só com `regua.ativa`. Mesmo estilo
escuro dos menus (`bg-slate-950/95 border border-slate-700/80 rounded-lg`). Conteúdo
(derivado reativamente de `resolverPontosRegua`):
- 0 pontos: "Toque no mapa ou num nó para começar".
- 1 ponto: "Toque no próximo ponto".
- >= 2: total em destaque (`rotuloDistancia`); com > 2 pontos, "Em linha reta: X · rumo"
  (primeiro→último); lista compacta de segmentos "1→2 · 850 m · NE 47°".
- Linha de chegada só quando o vértice 0 é de nó, `localState.movimento[nodeNum]` tem
  `parado !== true` e `etaMs(total, velocidadeKmh)` não é null:
  "Chegada estimada: ~1h 20min a 12 km/h" (use `duracaoLabel` de `lib/dwell.js`).
- Botões pequenos (ícone + texto, com `aria-label`): "Desfazer" (desabilitado com 0
  pontos), "Concluir" (só se não concluída e >= 2 pontos), "Copiar" (>= 2 pontos;
  `navigator.clipboard.writeText(textoCompartilhar(...))`, mostra "Copiado" por 2 s; em
  falha ou sem clipboard mostra "Não foi possível copiar"), "Fechar" (`desativarRegua`).
- Dica sob os botões enquanto não concluída: "Duplo clique para concluir · Esc para sair".

Testes: em `MapControls.test.tsx`, novo `describe` do botão (aria-pressed alterna, store
muda); `ReguaPainel.test.tsx`: dicas com 0/1 ponto, total com 3 pontos livres, chegada só
para nó em movimento com velocidade, "Copiar" chama o clipboard com o texto esperado
(mock de `navigator.clipboard`), "Fechar" desliga. Limpar o store no `afterEach`.

## T6 — Entradas "Medir daqui"

- `web/src/components/viewer/NodeInspector.tsx`: botão ao lado de "Centralizar no mapa",
  mesmo estilo, `RulerIcon`, texto "Medir daqui", chama `ativarRegua(node().nodeNum)`.
  Desabilitado quando o nó não tem posição confirmada (siga como o "Centralizar" decide).
- Popup em `InitializeMap.tsx` (`gerarHtmlPopup`): rodapé com
  `<button type="button" data-acao="medir-daqui" ...>Medir daqui</button>` (só quando o
  pin tem nodeNum). Em `abrirPopupDoPin`, depois do `addTo(map)`, ouvir o clique em
  `popup.getElement().querySelector('[data-acao="medir-daqui"]')` que chama
  `LocalState.ativarRegua(nodeNum)` e fecha o popup.
- Testes: botão do inspector liga a régua com o vértice 0 ancorado.

## T7 — Só o revisor (não é para subagente)

Conferência no navegador com `pnpm dev:test`, e2e de fumaça se viável, nota no
AGENTS.md, revisão final.
