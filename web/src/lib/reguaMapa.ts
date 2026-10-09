import type {
  GeoJSONSource,
  LayerSpecification,
  MapLayerMouseEvent,
  MapLayerTouchEvent,
  MapMouseEvent,
  MapTouchEvent,
  Map as maplibregl,
} from "maplibre-gl";
import type { PontoRegua } from "../store.js";
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

/** Meia-largura (px) da caixa de encaixe em torno do clique: amigável ao toque. */
const RAIO_ENCAIXE_PX = 10;

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

/** Cursor de crosshair com a régua ligada; "" para o padrão. */
export function definirCursorRegua(map: maplibregl, ativa: boolean): void {
  if (typeof map.getCanvas === "function") {
    map.getCanvas().style.cursor = ativa ? "crosshair" : "";
  }
}

/** Instala os handlers da régua. Devolve uma função que remove todos. */
export function instalarInteracoesRegua(
  map: maplibregl,
  deps: DepsInteracoesRegua,
): () => void {
  // Índice do vértice em arraste; null = nenhum arraste em curso.
  let arrastando: number | null = null;
  // O arraste moveu o vértice: não deve virar um vértice novo no clique seguinte.
  let arrastou = false;
  let ignorarProximoClique = false;

  const nodeNumSob = (x: number, y: number): number | null => {
    const camadas = deps.camadasNos.filter((id) => map.getLayer(id));
    if (camadas.length === 0) {
      return null;
    }
    const feicoes = map.queryRenderedFeatures(
      [
        [x - RAIO_ENCAIXE_PX, y - RAIO_ENCAIXE_PX],
        [x + RAIO_ENCAIXE_PX, y + RAIO_ENCAIXE_PX],
      ],
      { layers: camadas },
    );
    for (const f of feicoes) {
      const nodeNum = deps.nodeNumDe(
        (f.properties ?? {}) as Record<string, unknown>,
      );
      if (nodeNum !== null) {
        return nodeNum;
      }
    }
    return null;
  };

  const aoClicar = (e: MapMouseEvent) => {
    if (!deps.ativa()) {
      return;
    }
    if (ignorarProximoClique) {
      ignorarProximoClique = false;
      return;
    }
    const nodeNum = nodeNumSob(e.point.x, e.point.y);
    if (nodeNum !== null) {
      deps.adicionar({ tipo: "no", nodeNum });
      return;
    }
    deps.adicionar({ tipo: "livre", lon: e.lngLat.lng, lat: e.lngLat.lat });
  };

  // Duplo clique fecha o caminho e não dá zoom. Os dois cliques anteriores
  // repetem o mesmo ponto, que o store já descarta.
  const aoDuploClique = (e: MapMouseEvent) => {
    if (!deps.ativa()) {
      return;
    }
    e.preventDefault();
    deps.concluir();
  };

  const aoPressionarVertice = (e: MapLayerMouseEvent | MapLayerTouchEvent) => {
    if (!deps.ativa()) {
      return;
    }
    const indice = e.features?.[0]?.properties?.indice;
    if (typeof indice !== "number") {
      return;
    }
    e.preventDefault();
    arrastando = indice;
    arrastou = false;
    map.dragPan.disable();
  };

  const aoMover = (e: MapMouseEvent | MapTouchEvent) => {
    if (arrastando === null || !deps.ativa()) {
      return;
    }
    arrastou = true;
    deps.mover(arrastando, e.lngLat.lng, e.lngLat.lat);
  };

  // Sem checar ativa(): precisa devolver o dragPan mesmo se a régua for desligada no meio.
  const aoSoltar = () => {
    if (arrastando === null) {
      return;
    }
    arrastando = null;
    map.dragPan.enable();
    if (arrastou) {
      ignorarProximoClique = true;
    }
    arrastou = false;
  };

  // Um gesto novo cancela o "ignorar clique" pendente de um arraste sem clique
  // (o MapLibre não emite click depois de arrastar além da tolerância).
  const aoIniciarGesto = () => {
    ignorarProximoClique = false;
  };

  const aoEntrarVertice = () => {
    if (deps.ativa() && typeof map.getCanvas === "function") {
      map.getCanvas().style.cursor = "move";
    }
  };

  const aoSairVertice = () => {
    if (deps.ativa()) {
      definirCursorRegua(map, true);
    }
  };

  const aoTeclar = (e: KeyboardEvent) => {
    if (e.key === "Escape" && deps.ativa()) {
      deps.sair();
    }
  };

  map.on("click", aoClicar);
  map.on("dblclick", aoDuploClique);
  map.on("mousedown", CAMADA_REGUA_VERTICES, aoPressionarVertice);
  map.on("touchstart", CAMADA_REGUA_VERTICES, aoPressionarVertice);
  map.on("mousedown", aoIniciarGesto);
  map.on("touchstart", aoIniciarGesto);
  map.on("mousemove", aoMover);
  map.on("touchmove", aoMover);
  map.on("mouseup", aoSoltar);
  map.on("touchend", aoSoltar);
  map.on("mouseenter", CAMADA_REGUA_VERTICES, aoEntrarVertice);
  map.on("mouseleave", CAMADA_REGUA_VERTICES, aoSairVertice);
  document.addEventListener("keydown", aoTeclar);

  return () => {
    map.off("click", aoClicar);
    map.off("dblclick", aoDuploClique);
    map.off("mousedown", CAMADA_REGUA_VERTICES, aoPressionarVertice);
    map.off("touchstart", CAMADA_REGUA_VERTICES, aoPressionarVertice);
    map.off("mousedown", aoIniciarGesto);
    map.off("touchstart", aoIniciarGesto);
    map.off("mousemove", aoMover);
    map.off("touchmove", aoMover);
    map.off("mouseup", aoSoltar);
    map.off("touchend", aoSoltar);
    map.off("mouseenter", CAMADA_REGUA_VERTICES, aoEntrarVertice);
    map.off("mouseleave", CAMADA_REGUA_VERTICES, aoSairVertice);
    document.removeEventListener("keydown", aoTeclar);
    if (arrastando !== null) {
      map.dragPan.enable();
      arrastando = null;
    }
  };
}
