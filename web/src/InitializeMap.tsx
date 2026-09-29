import type { GeoJSONSource, StyleSpecification } from "maplibre-gl";
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

// Glyphs gerados no deploy (D9) — nenhum fonte remoto.
const GLYPHS_URL = "/tiles/glyphs/{fontstack}/{range}.pbf";

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
    basemap: {
      type: "vector",
      url: TILES_URL,
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
    map.on("load", () => {
      setCarregado(true);
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
    });
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
