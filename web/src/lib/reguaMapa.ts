import type {
  GeoJSONSource,
  LayerSpecification,
  Map as maplibregl,
} from "maplibre-gl";
import {
  type LngLat,
  type VerticeResolvido,
  distanciasSegmentos,
  rotuloDistancia,
  rotuloRumo,
  rumo,
} from "./regua.js";

export const FONTE_REGUA = "regua";
export const CAMADA_REGUA_LINHA = "regua-linha";
export const CAMADA_REGUA_VERTICES = "regua-vertices";
export const CAMADA_REGUA_ROTULOS = "regua-rotulos";

// Forma GeoJSON mínima usada pela régua (o pacote @types/geojson não está no projeto).
type PosicaoGeo = [number, number];

interface FeatureRegua {
  type: "Feature";
  geometry:
    | { type: "LineString"; coordinates: PosicaoGeo[] }
    | { type: "Point"; coordinates: PosicaoGeo };
  properties: Record<string, unknown>;
}

export interface FeatureCollectionRegua {
  type: "FeatureCollection";
  features: FeatureRegua[];
}

const FC_VAZIA: FeatureCollectionRegua = {
  type: "FeatureCollection",
  features: [],
};

const COR_LINHA = "#fbbf24";
const COR_ANCORADO = "#34d399";

// Filtros por tipo de geometria: a fonte única é separada em camadas aqui.
const FILTRO_LINHA: LayerSpecification["filter"] = [
  "==",
  ["geometry-type"],
  "LineString",
];
const FILTRO_VERTICES: LayerSpecification["filter"] = [
  "all",
  ["==", ["geometry-type"], "Point"],
  ["has", "indice"],
];
const FILTRO_ROTULOS: LayerSpecification["filter"] = [
  "all",
  ["==", ["geometry-type"], "Point"],
  ["has", "rotulo"],
];

// Ordem de inserção = ordem de empilhamento: rótulos ficam por cima de tudo.
const CAMADAS_REGUA: LayerSpecification[] = [
  {
    id: CAMADA_REGUA_LINHA,
    type: "line",
    source: FONTE_REGUA,
    filter: FILTRO_LINHA,
    paint: {
      "line-color": COR_LINHA,
      "line-width": 2.5,
      "line-dasharray": [2, 1.5],
    },
  },
  {
    id: CAMADA_REGUA_VERTICES,
    type: "circle",
    source: FONTE_REGUA,
    filter: FILTRO_VERTICES,
    paint: {
      "circle-radius": 6,
      "circle-color": "#0f172a",
      "circle-stroke-width": 2.5,
      "circle-stroke-color": [
        "case",
        ["==", ["get", "ancorado"], true],
        COR_ANCORADO,
        COR_LINHA,
      ],
    },
  },
  {
    id: CAMADA_REGUA_ROTULOS,
    type: "symbol",
    source: FONTE_REGUA,
    filter: FILTRO_ROTULOS,
    layout: {
      "text-field": ["get", "rotulo"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 11,
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": "#ffffff",
      "text-halo-color": "#090f0b",
      "text-halo-width": 2.5,
    },
  },
];

/** Adiciona fonte + camadas por cima de tudo, se ausentes (idempotente). */
export function garantirCamadasRegua(map: maplibregl): void {
  if (!map.getSource(FONTE_REGUA)) {
    map.addSource(FONTE_REGUA, {
      type: "geojson",
      data: FC_VAZIA as never,
    });
  }
  for (const camada of CAMADAS_REGUA) {
    if (!map.getLayer(camada.id)) {
      // Sem beforeId: a camada entra no topo da pilha.
      map.addLayer(camada);
    }
  }
}

/**
 * FeatureCollection da medição: LineString com todos os vértices (só com >= 2),
 * um Point por vértice e um Point no meio de cada segmento com o rótulo.
 */
export function geojsonRegua(
  pts: readonly VerticeResolvido[],
): FeatureCollectionRegua {
  if (pts.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }

  const features: FeatureRegua[] = [];
  const posicoes: LngLat[] = pts.map((p) => p.pos);

  if (pts.length >= 2) {
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: posicoes },
      properties: {},
    });
  }

  for (const ponto of pts) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: ponto.pos },
      properties: { indice: ponto.indice, ancorado: ponto.nodeNum !== null },
    });
  }

  const distancias = distanciasSegmentos(posicoes);
  for (let i = 0; i < distancias.length; i++) {
    const a = posicoes[i] as LngLat;
    const b = posicoes[i + 1] as LngLat;
    const meio: PosicaoGeo = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const distancia = distancias[i] as number;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: meio },
      properties: {
        rotulo: `${rotuloDistancia(distancia)} · ${rotuloRumo(rumo(a, b))}`,
      },
    });
  }

  return { type: "FeatureCollection", features };
}

/** setData na fonte (sem efeito se a fonte não existir). */
export function atualizarRegua(
  map: maplibregl,
  pts: readonly VerticeResolvido[],
): void {
  const fonte = map.getSource(FONTE_REGUA) as GeoJSONSource | undefined;
  if (!fonte) {
    return;
  }
  fonte.setData(geojsonRegua(pts) as never);
}
