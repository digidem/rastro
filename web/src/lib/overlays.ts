import type { LayerSpecification, Map as MapLibre } from "maplibre-gl";

export interface OverlayLayer {
  name: string;
  data: GeoJSON.FeatureCollection;
}

const CORES = ["#f472b6", "#a3e635", "#fb923c", "#c084fc", "#22d3ee"];
const ROTULO = [
  "coalesce",
  ["get", "ALDEIA"],
  ["get", "NOME"],
  ["get", "nome"],
  ["get", "name"],
  ["get", "terrai_nom"],
  "",
];

/** Camadas extras servidas pela API (GET /api/overlays); falha vira lista vazia. */
export async function fetchOverlays(base = ""): Promise<OverlayLayer[]> {
  try {
    const res = await fetch(`${base}/api/overlays`, {
      credentials: "same-origin",
    });
    if (!res.ok) {
      return [];
    }
    const corpo = (await res.json()) as { layers?: OverlayLayer[] };
    return Array.isArray(corpo.layers) ? corpo.layers : [];
  } catch {
    return [];
  }
}

/** Adiciona cada camada ao mapa, abaixo dos pins/trilhas (`antesDe`). */
export function adicionarOverlays(
  map: MapLibre,
  camadas: OverlayLayer[],
  antesDe?: string,
) {
  const ancora = antesDe && map.getLayer(antesDe) ? antesDe : undefined;
  camadas.forEach((camada, i) => {
    const fonte = `overlay-${i}`;
    if (map.getSource(fonte) !== undefined) {
      return;
    }
    const cor = CORES[i % CORES.length] as string;
    map.addSource(fonte, { type: "geojson", data: camada.data });
    const specs: LayerSpecification[] = [
      {
        id: `${fonte}-fill`,
        type: "fill",
        source: fonte,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": cor, "fill-opacity": 0.12 },
      },
      {
        id: `${fonte}-line`,
        type: "line",
        source: fonte,
        filter: [
          "in",
          ["geometry-type"],
          ["literal", ["Polygon", "LineString"]],
        ],
        paint: { "line-color": cor, "line-width": 1.5 },
      },
      {
        id: `${fonte}-circle`,
        type: "circle",
        source: fonte,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 4,
          "circle-color": cor,
          "circle-stroke-color": "#0f172a",
          "circle-stroke-width": 1,
        },
      },
      {
        id: `${fonte}-label`,
        type: "symbol",
        source: fonte,
        filter: ["==", ["geometry-type"], "Point"],
        minzoom: 8,
        layout: {
          "text-field": ROTULO as never,
          "text-font": ["Noto Sans Regular"],
          "text-size": 11,
          "text-offset": [0, 1],
          "text-anchor": "top",
        },
        paint: {
          "text-color": "#f8fafc",
          "text-halo-color": "#0f172a",
          "text-halo-width": 1.2,
        },
      },
    ];
    for (const spec of specs) {
      map.addLayer(spec, ancora);
    }
  });
}
