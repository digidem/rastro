import type {
  GeoJSONSource,
  LayerSpecification,
  StyleSpecification,
} from "maplibre-gl";
import { Popup, addProtocol, Map as maplibregl } from "maplibre-gl";
import { Protocol } from "pmtiles";
import type { Component, JSXElement } from "solid-js";
import { createEffect, createSignal, onCleanup, untrack } from "solid-js";
import { useData } from "./providers/DataProvider.jsx";
import { MapContext } from "./providers/MapProvider.jsx";
import { LocalState } from "./store.js";

export interface InitializeMapProps {
  children?: JSXElement;
}

// Registra o protocolo pmtiles:// (tiles servidas pelo caddy, sem CDN).
addProtocol("pmtiles", new Protocol().tile);

// Caminho relativo resolve contra a origem da página; em dev o .env pode
// apontar VITE_TILES_URL para outro lugar.
const TILES_URL =
  (import.meta.env.VITE_TILES_URL as string | undefined) ||
  "pmtiles:///tiles/basemap.pmtiles";

// Glyphs (Noto Sans Regular, OFL) embutidos na própria imagem web — nenhum fonte
// remoto e nada para copiar à mão no servidor.
const GLYPHS_URL = "/glyphs/{fontstack}/{range}.pbf";

type Limites = [[number, number], [number, number]];

// Caixa [[oeste, sul], [leste, norte]] que cobre todos os pontos válidos, ou null.
function limitesDosPontos(
  pontos: { lon: number; lat: number }[],
): Limites | null {
  const ok = pontos.filter(
    (p) =>
      Number.isFinite(p.lon) &&
      Number.isFinite(p.lat) &&
      Math.abs(p.lon) <= 180 &&
      Math.abs(p.lat) <= 90,
  );
  if (ok.length === 0) {
    return null;
  }
  return [
    [Math.min(...ok.map((p) => p.lon)), Math.min(...ok.map((p) => p.lat))],
    [Math.max(...ok.map((p) => p.lon)), Math.max(...ok.map((p) => p.lat))],
  ];
}

// Basemap PADRÃO: tiles do OpenStreetMap servidos pela API (/api/osm/…, com token; o
// navegador não fala com o OSM). Ele já nasce no estilo — nenhuma fonte "que pode
// falhar" no boot: uma source pmtiles ausente (404) deixava o mapa eternamente
// "não carregado" (evento load nunca disparava) e os pins nunca apareciam.
const OSM_TILES = (): string =>
  `${window.location.origin}/api/osm/{z}/{x}/{y}.png`;

const CAMADAS_PMTILES: LayerSpecification[] = [
  {
    id: "landcover",
    type: "fill",
    source: "basemap",
    "source-layer": "landcover",
    paint: { "fill-color": "#1c2b1f", "fill-opacity": 0.9 },
  },
  {
    id: "water",
    type: "fill",
    source: "basemap",
    "source-layer": "water",
    paint: { "fill-color": "#14293b" },
  },
  {
    id: "waterway",
    type: "line",
    source: "basemap",
    "source-layer": "waterway",
    paint: { "line-color": "#14293b", "line-width": 1 },
  },
  {
    id: "boundary",
    type: "line",
    source: "basemap",
    "source-layer": "boundary",
    paint: {
      "line-color": "#4a5b4e",
      "line-width": 1,
      "line-dasharray": [2, 2],
    },
  },
];

// Se existir um basemap próprio (/tiles/basemap.pmtiles), troca o OSM por ele:
// funciona offline e sem nenhuma requisição externa.
function usarBasemapLocal(map: maplibregl) {
  if (map.getSource("basemap") !== undefined) {
    return;
  }
  map.addSource("basemap", {
    type: "vector",
    url: TILES_URL,
    attribution: "© OpenStreetMap contributors",
  });
  for (const camada of CAMADAS_PMTILES) {
    map.addLayer(camada, "track-line");
  }
  map.setLayoutProperty("osm-base", "visibility", "none");
}

async function existeBasemapLocal(): Promise<boolean> {
  if (typeof fetch !== "function") {
    return false;
  }
  try {
    const res = await fetch("/tiles/basemap.pmtiles", {
      method: "HEAD",
      credentials: "omit",
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Shape estrutural mínimo; os setData recebem tipagem contextual do maplibre.
interface FeatureCollectionLike {
  type: "FeatureCollection";
  features: never[];
}

const EMPTY_FC: FeatureCollectionLike = {
  type: "FeatureCollection",
  features: [],
};

// Estilo inline com o schema planetiler (layers water/landcover/boundary).
const estilo: StyleSpecification = {
  version: 8,
  glyphs: GLYPHS_URL,
  sources: {
    osm: {
      type: "raster",
      tiles: [OSM_TILES()],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
    nodes: { type: "geojson", data: EMPTY_FC },
    track: { type: "geojson", data: EMPTY_FC },
    "track-points": { type: "geojson", data: EMPTY_FC },
  },
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#121b14" },
    },
    { id: "osm-base", type: "raster", source: "osm" },
    {
      id: "track-line",
      type: "line",
      source: "track",
      paint: { "line-color": "#f2b544", "line-width": 2.5 },
    },
    {
      id: "track-points",
      type: "circle",
      source: "track-points",
      paint: {
        "circle-radius": 3,
        "circle-color": "#f2b544",
        "circle-stroke-color": "#121b14",
        "circle-stroke-width": 1,
      },
    },
    {
      id: "nodes-circle",
      type: "circle",
      source: "nodes",
      paint: {
        "circle-radius": 6,
        "circle-color": "#67ea94",
        "circle-stroke-color": "#121b14",
        "circle-stroke-width": 2,
      },
    },
    {
      id: "nodes-label",
      type: "symbol",
      source: "nodes",
      minzoom: 10,
      layout: {
        "text-field": ["get", "nome"],
        "text-font": ["Noto Sans Regular"],
        "text-size": 12,
        "text-offset": [0, 1.2],
        "text-anchor": "top",
      },
      paint: {
        "text-color": "#e8f5ec",
        "text-halo-color": "#121b14",
        "text-halo-width": 1,
      },
    },
  ],
};

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) =>
    c === "&"
      ? "&amp;"
      : c === "<"
        ? "&lt;"
        : c === ">"
          ? "&gt;"
          : c === '"'
            ? "&quot;"
            : "&#39;",
  );

const dataFixa = (iso: string | null): string =>
  iso === null
    ? "sem fix"
    : new Date(iso).toLocaleString("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
      });

export const InitializeMap: Component<InitializeMapProps> = (props) => {
  const { api } = useData();
  const [mapRef, setMapRef] = createSignal<HTMLDivElement>();
  const [currentView, setCurrentView] = createSignal<maplibregl | undefined>(
    undefined,
  );
  const [carregado, setCarregado] = createSignal(false);
  let trackReq = 0; // descarta resposta de trilha de seleção anterior
  let interagiu = false; // o usuário mexeu no mapa: para de recentralizar sozinho
  let idsCentralizados = ""; // conjunto de nós do último enquadramento automático
  let popup: Popup | null = null; // popup do pin, para fechar ao encerrar sessão

  const initializeMap = () => {
    const el = mapRef();
    if (el === undefined || currentView() !== undefined) {
      return;
    }
    const map = new maplibregl({
      container: el,
      style: estilo,
      center: [-51.9, -14.2], // centro padrão (Brasil)
      zoom: 7,
      attributionControl: { compact: false },
    });
    setCurrentView(map);
    // Gesto do usuário (arrastar/zoom/toque) desliga o enquadramento automático.
    for (const ev of ["dragstart", "wheel", "touchstart", "dblclick"]) {
      map.on(ev, () => {
        interagiu = true;
      });
    }
    // O estilo (JSON) fica pronto em "style.load", antes de qualquer tile — é o que
    // basta para pins/trilha. "load" (todos os tiles) pode demorar ou nem vir se um
    // tile falhar, então os dois eventos chamam a mesma rotina, uma vez só.
    let iniciado = false;
    const aoCarregar = () => {
      if (iniciado) {
        return;
      }
      iniciado = true;
      setCarregado(true);
      // Basemap próprio (offline) tem prioridade sobre o OSM, se o arquivo existir.
      void existeBasemapLocal().then((existe) => {
        if (existe) {
          usarBasemapLocal(map);
        }
      });
      // Popup do pin: nome + data do fix.
      map.on("click", "nodes-circle", (e) => {
        const f = e.features?.[0];
        if (f?.geometry.type !== "Point") {
          return;
        }
        const props = (f.properties ?? {}) as Record<string, unknown>;
        const nome = typeof props.nome === "string" ? props.nome : "nó";
        const posTime =
          typeof props.posTime === "string" ? props.posTime : null;
        popup = new Popup({ offset: 12 })
          .setLngLat([f.geometry.coordinates[0], f.geometry.coordinates[1]])
          .setHTML(
            `<strong>${esc(nome)}</strong><br/>fix: ${dataFixa(posTime)}`,
          )
          .addTo(map);
      });
    };
    map.on("style.load", aoCarregar);
    map.on("load", aoCarregar);
  };

  onCleanup(() => {
    currentView()?.remove();
    setCurrentView(undefined);
    setCarregado(false);
  });

  // Pins: qualquer mudança em nodes vira setData no source "nodes".
  createEffect(() => {
    const nos = Object.values(LocalState.localState.nodes);
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    const source = map.getSource("nodes") as GeoJSONSource | undefined;
    source?.setData({
      type: "FeatureCollection",
      features: nos.map((n) => ({
        type: "Feature",
        properties: { nome: n.nome, posTime: n.posTime },
        geometry: { type: "Point", coordinates: [n.lon, n.lat] },
      })),
    });
    // Enquadra todos os nós: na primeira carga e quando entra/sai um nó — a não ser
    // que o usuário já tenha mexido no mapa ou haja um nó selecionado.
    const ids = nos
      .map((n) => n.nodeNum)
      .sort()
      .join(",");
    const caixa = limitesDosPontos(nos);
    if (
      caixa !== null &&
      ids !== idsCentralizados &&
      !interagiu &&
      untrack(() => LocalState.localState.selected) === null
    ) {
      idsCentralizados = ids;
      map.fitBounds(caixa, { padding: 70, maxZoom: 12, duration: 600 });
    }
  });

  // Trilha: reage à seleção, voa até o nó e limpa ao desselecionar.
  createEffect(() => {
    const sel = LocalState.localState.selected;
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    const lineSrc = map.getSource("track") as GeoJSONSource | undefined;
    const pointsSrc = map.getSource("track-points") as
      | GeoJSONSource
      | undefined;
    if (lineSrc === undefined || pointsSrc === undefined) {
      return;
    }
    if (sel === null) {
      // Desseleção também deve matar a resposta em voo (mesma corrida da troca).
      trackReq++;
      lineSrc.setData(EMPTY_FC);
      pointsSrc.setData(EMPTY_FC);
      return;
    }
    // R4-4 (gate agregado): `nodes` é lido com untrack — sem isto o efeito
    // dependia da lista e nunca re-roda quando um nó some/entra no reconcile.
    const nodesAhora = LocalState.localState.nodes; // tracked
    const node = untrack(() => nodesAhora[sel]);
    if (node === undefined) {
      // Seleção órfã (nó sumiu do reconcile): trilha congelada do nó velho
      // não pode ficar no mapa quando a lista some.
      trackReq++;
      lineSrc.setData(EMPTY_FC);
      pointsSrc.setData(EMPTY_FC);
      return;
    }
    map.flyTo({
      center: [node.lon, node.lat],
      zoom: Math.max(map.getZoom(), 12),
    });
    const req = ++trackReq;
    const target = node.nodeId || String(node.nodeNum);
    api
      .track(target)
      .then((t) => {
        if (req !== trackReq) {
          return; // seleção mudou durante o fetch
        }
        lineSrc.setData(
          t.line === null
            ? EMPTY_FC
            : {
                type: "FeatureCollection",
                features: [
                  {
                    type: "Feature",
                    properties: {},
                    geometry: { type: "LineString", coordinates: t.line },
                  },
                ],
              },
        );
        pointsSrc.setData({
          type: "FeatureCollection",
          features: t.points.map((p) => ({
            type: "Feature",
            properties: { posTime: p.posTime, sats: p.sats },
            geometry: { type: "Point", coordinates: p.pos },
          })),
        });
      })
      .catch(() => {
        // 401/rede: painel e indicador já refletem; mapa fica como está.
      });
  });

  // Reação à geração de sessão (logout/401): limpa JÁ a visualização —
  // desseleciona, fecha popup, apaga trilha e mata resposta de trilha em voo.
  createEffect(() => {
    LocalState.localState.pollingGeracao;
    trackReq++;
    popup?.remove();
    popup = null;
    const map = currentView();
    if (map === undefined) {
      return;
    }
    LocalState.select(null);
    if (!carregado()) {
      return; // fonte ainda não existe; ao carregar nasce vazia
    }
    const lineSrc = map.getSource("track") as GeoJSONSource | undefined;
    const pointsSrc = map.getSource("track-points") as
      | GeoJSONSource
      | undefined;
    lineSrc?.setData(EMPTY_FC);
    pointsSrc?.setData(EMPTY_FC);
  });

  return (
    <MapContext.Provider value={{ setMapRef, initializeMap }}>
      {props.children}
    </MapContext.Provider>
  );
};
