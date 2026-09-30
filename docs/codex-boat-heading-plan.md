> [!NOTE]
> **Status:** Especificação técnica e plano de implementação de rumo (implementado e consolidado nos commits `c2051e0` e `5fdfe71`).
> O cálculo geodésico de azimute (`bearingDaTrilha`), a propriedade `bearing` nas features GeoJSON e na store, e a camada MapLibre `nodes-boat` com rotação orientada ao mapa (`icon-rotate: ["get", "bearing"]`, `icon-rotation-alignment: "map"`) estão ativos no código em `web/src/`.

1. **Arquitetura**

O rumo deve ser calculado na entrada dos dados e armazenado por nó. `nodesGeoJson` apenas publica esse valor; o MapLibre aplica a rotação.

Esse valor representa a direção do deslocamento sobre o solo. Sem sensor de orientação, não determina para onde a proa aponta quando o barco está parado, à deriva ou navegando de ré.

O código atual apresenta duas fontes complementares:

| Fonte | Situação atual | Uso proposto |
|---|---|---|
| Polling de `latest()` | Atualiza todos os nós | Calcular rumo entre fixes sucessivos |
| `api.track()` | Busca apenas o nó selecionado | Inicializar rumo usando o último segmento válido |
| Fixture `getTrackGeoJson()` | Ordena pontos do mais antigo ao recente | Exercitar a mesma regra de trajetória |

A consequência é concreta: na primeira carga, barcos sem trilha consultada ficam em 0° até receberem um novo fix. Orientar todos imediatamente exige fornecer o último segmento no contrato de `latest()` ou carregar históricos adicionais. A recomendação inicial é aproveitar o polling existente, sem uma requisição de trilha por barco.

**Função matemática**

Criar `web/src/lib/bearing.ts`, sem dependências de store, DOM ou MapLibre:

```ts
type Coordenada = readonly [number, number];

const rad = (graus: number): number => (graus * Math.PI) / 180;

function coordenadaValida(lon: number, lat: number): boolean {
  return (
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    Math.abs(lon) <= 180 &&
    Math.abs(lat) <= 90
  );
}

/**
 * Azimute inicial geodésico sobre uma esfera, em [0, 360).
 * null significa direção indefinida ou coordenadas inválidas.
 */
export function calcularBearing(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number,
): number | null {
  if (
    !coordenadaValida(lon1, lat1) ||
    !coordenadaValida(lon2, lat2)
  ) {
    return null;
  }

  const phi1 = rad(lat1);
  const phi2 = rad(lat2);
  const deltaLambda = rad(lon2 - lon1);

  const y = Math.sin(deltaLambda) * Math.cos(phi2);
  const x =
    Math.cos(phi1) * Math.sin(phi2) -
    Math.sin(phi1) * Math.cos(phi2) * Math.cos(deltaLambda);

  // Coincidentes, antípodas ou outro caso numericamente indefinido.
  if (Math.hypot(x, y) < 1e-12) {
    return null;
  }

  const graus = (Math.atan2(y, x) * 180) / Math.PI;
  return ((graus % 360) + 360) % 360;
}

/**
 * Coordenadas em ordem cronológica: antiga → recente.
 * Procura o último segmento com direção definida.
 * Um ponto inválido interrompe a continuidade da trilha.
 */
export function bearingDaTrilha(
  coordenadas: readonly Coordenada[],
): number | null {
  for (let i = coordenadas.length - 1; i > 0; i--) {
    const anterior = coordenadas[i - 1];
    const atual = coordenadas[i];

    if (
      !coordenadaValida(...anterior) ||
      !coordenadaValida(...atual)
    ) {
      return null;
    }

    const bearing = calcularBearing(...anterior, ...atual);
    if (bearing !== null) {
      return bearing;
    }
  }

  return null;
}
```

Retornar `null` distingue ausência de direção de Norte verdadeiro. O fallback visual fica na integração.

**Integração proposta**

| Arquivo | Mudança |
|---|---|
| [store.ts](../web/src/store.ts) | Acrescentar `bearing?: number`; guardar o histórico mínimo separado dos campos recebidos da API |
| [DataProvider.tsx](../web/src/providers/DataProvider.tsx) | Atualizar rumo depois da validação da geração da sessão e antes de publicar os nós |
| [nodes.ts](../web/src/lib/nodes.ts) | Acrescentar `bearing` às propriedades de cada feature |
| [InitializeMap.tsx](../web/src/InitializeMap.tsx) | Aproveitar resposta válida de trilha para inicializar rumo |
| [nodesFixture.ts](../web/src/fixtures/nodesFixture.ts) | Reutilizar a função pura sobre a trajetória ordenada |

Regras para o histórico mínimo:

- Manter por `nodeNum` um fix de referência, o último timestamp aceito e o último rumo válido.
- Aceitar somente posições confirmadas com `posTime` estritamente crescente. Duplicatas e respostas antigas não substituem o histórico.
- Novo fix sem deslocamento mantém o rumo anterior; ausência de rumo conhecido produz fallback 0°.
- Para evitar oscilações por GPS, considerar inicialmente uma distância mínima configurável de 5 metros, calculada por Haversine. Acumular deslocamento em relação ao fix de referência até superar esse limite.
- Remover histórico de nós ausentes e limpar tudo em logout/401. A trajetória recebida também respeita a geração da sessão e `trackReq`.
- Uma resposta de trilha só substitui o rumo quando seu timestamp terminal é identificável e não é anterior ao fix que originou o rumo atual.

O adaptador atual preserva a ordem de `track.line`, mas não a ordena. Para produção, confirmar o contrato antiga → recente; quando necessário, ordenar `track.points` pelos timestamps válidos. Não deduzir cronologia pela posição geográfica.

Evitar guardar trilhas completas na store apenas para orientação. O polling permanece O(N), com memória O(N); a consulta de uma trilha exige uma única varredura reversa O(P). `nodesGeoJson` permanece puro e não realiza buscas de rede nem cálculos de trajetória.

A publicação pode usar:

```ts
bearing:
  typeof n.bearing === "number" && Number.isFinite(n.bearing)
    ? ((n.bearing % 360) + 360) % 360
    : 0,
```

Nas fixtures, calcular o rumo com o mesmo helper, sem escrever valores manualmente. O mock e o adaptador agora serializam e consomem a propriedade `bearing` diretamente (implementado no commit `c2051e0`).

**Camada MapLibre**

Em [InitializeMap.tsx](../web/src/InitializeMap.tsx):

```ts
{
  id: "nodes-boat",
  type: "symbol",
  source: "nodes",
  filter: ["==", ["get", "kind"], "boat"],
  layout: {
    "icon-image": "boat-icon",
    "icon-size": 0.5,
    "icon-anchor": "center",
    "icon-rotate": ["get", "bearing"],
    "icon-rotation-alignment": "map",
    "icon-pitch-alignment": "map",
    "icon-allow-overlap": true,
    "icon-ignore-placement": true,
  },
}
```

`icon-rotate` aplica graus no sentido horário; o alinhamento `"map"` acompanha a rotação do mapa. Não subtrair manualmente `map.getBearing()`. A especificação oficial confirma esses comportamentos. [MapLibre Style Spec](https://maplibre.org/maplibre-style-spec/layers/#icon-rotate)

O registro atual rasteriza o SVG em 64×64. Com `icon-size: 0.5`, a imagem ocupa 32×32 pixels; valores de `0.375` a `0.75` cobrem 24–48 pixels. Manter a imagem colorida com `sdf: false` — o padrão — e o registro existente após recarga do estilo.

2. **SVG**

Código completo para `web/public/devices/boat.svg`. A proa está em cima, o motor embaixo e o eixo longitudinal em `x=32`, sem rotação interna. O desenho é uma interpretação tática da referência; detalhes como o motor são simplificados.

O contorno claro e escuro fornece contraste em mapas distintos. A cabine ocupa a frente do teto e a lona azul ocupa o centro/ré.

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     width="64" height="64" viewBox="0 0 64 64"
     fill="none">
  <title>Barco regional de patrulha, proa ao Norte</title>

  <g stroke-linecap="round" stroke-linejoin="round">
    <!-- Halo claro: legibilidade sobre água e mapas escuros -->
    <path
      d="M32 6
         C23 11 18 21 18 34
         L19 51
         Q32 57 45 51
         L46 34
         C46 21 41 11 32 6Z"
      fill="#F8FAFC" stroke="#F8FAFC" stroke-width="5"/>

    <!-- Casco regional alongado, com borda resistente -->
    <path
      d="M32 6
         C23 11 18 21 18 34
         L19 51
         Q32 57 45 51
         L46 34
         C46 21 41 11 32 6Z"
      fill="#E8DCC0" stroke="#0F172A" stroke-width="2.5"/>

    <!-- Convés e borda interna -->
    <path
      d="M32 11
         C25 16 22 24 22 34
         L23 48
         Q32 52 41 48
         L42 34
         C42 24 39 16 32 11Z"
      fill="#A88960" stroke="#FFF7E6" stroke-width="1.5"/>

    <!-- Área livre da proa -->
    <path d="M32 12L25 21H39Z"
          fill="#F1E7CF" stroke="#0F172A" stroke-width="1.2"/>

    <!-- Teto do convés superior -->
    <rect x="23" y="22" width="18" height="25" rx="2"
          fill="#FFF4D8" stroke="#0F172A" stroke-width="2"/>

    <!-- Cabine de comando, à frente -->
    <rect x="26" y="23" width="12" height="8" rx="1.5"
          fill="#E2E8F0" stroke="#0F172A" stroke-width="1.5"/>
    <path d="M28 25H36V28H28Z" fill="#164E63"/>
    <path d="M32 25V28" stroke="#E2E8F0" stroke-width="1"/>

    <!-- Lona azul sobre equipamentos no centro/ré -->
    <path d="M26 33L32 31L38 34L39 43L33 46L25 43Z"
          fill="#2563EB" stroke="#0F172A" stroke-width="1.5"/>
    <path d="M26 34L32 33L37 35L38 42L33 44L27 42Z"
          fill="#3B82F6"/>
    <path d="M32 33L30 39L33 44M30 39L37 40"
          stroke="#93C5FD" stroke-width="1.3"/>

    <!-- Bordas laterais do teto -->
    <path d="M24 24V45M40 24V45"
          stroke="#FFFFFF" stroke-width="1.5"/>

    <!-- Escada lateral de acesso ao teto -->
    <path d="M20 34V46M23 34V46M20 36H23M20 40H23M20 44H23"
          stroke="#FFF7E6" stroke-width="1.2"/>

    <!-- Travessa e plataforma de popa -->
    <path d="M23 49H41" stroke="#0F172A" stroke-width="2"/>

    <!-- Motor e conjunto de direção na popa -->
    <rect x="28" y="51" width="8" height="8" rx="2"
          fill="#334155" stroke="#F8FAFC" stroke-width="3"/>
    <rect x="28" y="51" width="8" height="8" rx="2"
          fill="#334155" stroke="#0F172A" stroke-width="1.5"/>
    <path d="M32 59V62M29 61H35"
          stroke="#0F172A" stroke-width="2"/>

    <!-- Pneu/fender preso à ponta da proa -->
    <ellipse cx="32" cy="8" rx="3.5" ry="4.5"
             fill="#0F172A" stroke="#F8FAFC" stroke-width="1.5"/>
    <ellipse cx="32" cy="8" rx="1.3" ry="2.1"
             fill="#A88960"/>
  </g>
</svg>
```

3. **Testes**

Exemplo completo de `web/tests/unit/bearing.test.ts`, usando Vitest e a convenção de imports atual:

```ts
import { describe, expect, it } from "vitest";
import {
  bearingDaTrilha,
  calcularBearing,
} from "../../src/lib/bearing.js";

describe("calcularBearing", () => {
  it.each([
    ["Norte", 0, 0, 0, 1, 0],
    ["Leste", 0, 0, 1, 0, 90],
    ["Sul", 0, 0, 0, -1, 180],
    ["Oeste", 0, 0, -1, 0, 270],
  ])("%s", (_nome, lon1, lat1, lon2, lat2, esperado) => {
    expect(calcularBearing(lon1, lat1, lon2, lat2))
      .toBeCloseTo(esperado, 8);
  });

  it("calcula direção no hemisfério sul", () => {
    expect(calcularBearing(-70, -5, -70, -4))
      .toBeCloseTo(0, 8);
    expect(calcularBearing(-70, -4, -70, -5))
      .toBeCloseTo(180, 8);
  });

  it("retorna null para coordenadas idênticas", () => {
    expect(calcularBearing(-70, -5, -70, -5)).toBeNull();
  });

  it("retorna null para pontos antípodas", () => {
    expect(calcularBearing(0, 0, 180, 0)).toBeNull();
  });

  it.each([
    [NaN, 0, 1, 0],
    [0, Infinity, 1, 0],
    [181, 0, 1, 0],
    [0, 91, 1, 0],
    [0, 0, -181, 0],
    [0, 0, 1, -91],
  ])("rejeita coordenadas inválidas", (a, b, c, d) => {
    expect(calcularBearing(a, b, c, d)).toBeNull();
  });

  it("cruza o antimeridiano pelo caminho curto", () => {
    expect(calcularBearing(179, 0, -179, 0))
      .toBeCloseTo(90, 8);
    expect(calcularBearing(-179, 0, 179, 0))
      .toBeCloseTo(270, 8);
  });

  it("normaliza uma direção noroeste em [0, 360)", () => {
    const bearing = calcularBearing(0, 0, -1, 1);
    expect(bearing).not.toBeNull();
    expect(bearing!).toBeGreaterThan(270);
    expect(bearing!).toBeLessThan(360);
  });
});

describe("bearingDaTrilha", () => {
  it("não inventa direção sem dois pontos", () => {
    expect(bearingDaTrilha([])).toBeNull();
    expect(bearingDaTrilha([[0, 0]])).toBeNull();
  });

  it("usa o último segmento, inclusive após uma curva", () => {
    expect(bearingDaTrilha([[0, 0], [1, 0], [1, 1]]))
      .toBeCloseTo(0, 8);
  });

  it("ignora repetições finais e preserva o último segmento", () => {
    expect(
      bearingDaTrilha([[0, 0], [1, 0], [1, 0], [1, 0]]),
    ).toBeCloseTo(90, 8);
  });

  it("não inventa direção em uma trilha estacionária", () => {
    expect(bearingDaTrilha([[1, 1], [1, 1]])).toBeNull();
  });

  it("não atravessa uma interrupção por coordenada inválida", () => {
    expect(
      bearingDaTrilha([[0, 0], [NaN, 0], [1, 0]]),
    ).toBeNull();
  });
});
```

Os testes de integração também precisam verificar:

- `nodesGeoJson` publica `bearing: 0` sem histórico e preserva posição, filtros e demais propriedades.
- Polling mantém rumo durante paradas, ignora timestamps antigos e não mistura nós.
- Atualização apenas de bateria não recalcula rumo.
- Trilhas antigas ou respostas de seleção/sessão anterior não sobrescrevem o rumo.
- Logout/401 limpa o histórico; atualizações de rumo não criam ciclos de requisições nem deslocam a câmera.
- A camada contém a expressão de rotação e os alinhamentos previstos.

A validação visual cobre 24, 32 e 48 pixels, fundos claros/escuros e câmera girada, usando o browser-harness indicado pelo projeto. Norte, Leste, Sul e Oeste precisam continuar corretos com o mapa rotacionado.

Nenhum arquivo foi alterado e nenhum teste foi executado nesta consulta. Os 105 testes mencionados permanecem uma referência fornecida; a aprovação da implementação depende de `pnpm test`, `pnpm typecheck`, `pnpm build` e da verificação visual.

🌱 graft economizou aproximadamente 21 mil tokens nesta consulta, em 1 chamada.