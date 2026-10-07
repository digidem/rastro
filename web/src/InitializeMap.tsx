import type {
  GeoJSONSource,
  LayerSpecification,
  StyleSpecification,
} from "maplibre-gl";
import { Popup, addProtocol, Map as maplibregl } from "maplibre-gl";
import { Protocol } from "pmtiles";
import type { Component, JSXElement } from "solid-js";
import { createEffect, createSignal, onCleanup, untrack } from "solid-js";
import { bearingDaTrilha } from "./lib/bearing.js";
import {
  batteryLabel,
  hardwareModelLabel,
  hasConfirmedPosition,
  isAgeWarning,
  isBoatNode,
  isNodeOlderThan7Days,
  matchesFilters,
  nodeAgeLabel,
  nodesGeoJson,
} from "./lib/nodes.js";
import { espalharPinsSobrepostos } from "./lib/overlap.js";
import { useData } from "./providers/DataProvider.jsx";
import { MapContext } from "./providers/MapProvider.jsx";
import { LocalState, type NodeKind } from "./store.js";

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

// Basemap PADRÃO: Imagens de satélite (ArcGIS World Imagery).
const SATELLITE_TILES =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

// Tiles do OpenStreetMap servidos pela API (/api/osm/…, com token; o
// navegador não fala com o OSM).
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

// Assegura que a fonte e camadas do basemap local (PMTiles) existam no mapa
function garantirBasemapLocal(map: maplibregl) {
  if (map.getSource("basemap") === undefined) {
    map.addSource("basemap", {
      type: "vector",
      url: TILES_URL,
      attribution: "© OpenStreetMap contributors",
    });
    for (const camada of CAMADAS_PMTILES) {
      map.addLayer(camada, "boat-tracks-line");
    }
  }
}

// Aplica a visibilidade do mapa base conforme o modo selecionado
function aplicarModoBasemap(map: maplibregl, modo: string) {
  const visSat = modo === "satellite" ? "visible" : "none";
  const visOsm = modo === "osm" ? "visible" : "none";
  const visLocal = modo === "local" ? "visible" : "none";

  if (map.getLayer("satellite-base")) {
    map.setLayoutProperty("satellite-base", "visibility", visSat);
  }
  if (map.getLayer("osm-base")) {
    map.setLayoutProperty("osm-base", "visibility", visOsm);
  }

  if (modo === "local") {
    existeBasemapLocal().then((existe) => {
      if (existe) {
        garantirBasemapLocal(map);
        for (const camada of CAMADAS_PMTILES) {
          if (map.getLayer(camada.id)) {
            map.setLayoutProperty(camada.id, "visibility", "visible");
          }
        }
      } else {
        // Fallback para satélite se o arquivo PMTiles não existir localmente
        LocalState.setBasemapMode("satellite");
      }
    });
    return;
  }
  for (const camada of CAMADAS_PMTILES) {
    if (map.getLayer(camada.id)) {
      map.setLayoutProperty(camada.id, "visibility", visLocal);
    }
  }
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
    if (!res.ok) {
      return false;
    }
    const ct = res.headers?.get?.("content-type");
    if (ct?.includes("text/html")) {
      return false;
    }
    return true;
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

// Estilo inline com satélite padrão e suporte a OSM e PMTiles
const estilo: StyleSpecification = {
  version: 8,
  glyphs: GLYPHS_URL,
  sources: {
    satellite: {
      type: "raster",
      tiles: [SATELLITE_TILES],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Esri, Maxar, Earthstar Geographics, CNES/Airbus DS",
    },
    osm: {
      type: "raster",
      tiles: [OSM_TILES()],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
    "boat-tracks": { type: "geojson", data: EMPTY_FC },
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
      id: "satellite-base",
      type: "raster",
      source: "satellite",
      layout: { visibility: "visible" },
    },
    {
      id: "osm-base",
      type: "raster",
      source: "osm",
      layout: { visibility: "none" },
    },
    {
      id: "boat-tracks-line",
      type: "line",
      source: "boat-tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#38bdf8",
        "line-width": 1.5,
        "line-opacity": 0.55,
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
      id: "nodes-selected-halo",
      type: "circle",
      source: "nodes",
      filter: ["==", ["get", "nodeNum"], -1],
      paint: {
        "circle-radius": 17,
        "circle-color": "#10b981",
        "circle-opacity": 0.25,
        "circle-stroke-color": "#34d399",
        "circle-stroke-width": 2,
        "circle-stroke-opacity": 0.9,
      },
    },
    {
      id: "nodes-circle",
      type: "circle",
      source: "nodes",
      // Barcos por baixo, demais categorias por cima (nunca mascarados).
      layout: {
        "circle-sort-key": ["case", ["==", ["get", "kind"], "boat"], 0, 1],
      },
      paint: {
        "circle-radius": ["case", ["==", ["get", "kind"], "boat"], 13, 6],
        // A cor representa a CATEGORIA (nunca frescor/bateria).
        // Barcos usam ícone SVG no lugar do círculo, mantendo a área de clique.
        "circle-color": [
          "match",
          ["get", "kind"],
          "boat",
          "transparent",
          "fixed_station",
          "#f59e0b",
          "handheld",
          "#10b981",
          "#94a3b8",
        ],
        "circle-stroke-color": [
          "case",
          ["==", ["get", "kind"], "boat"],
          "transparent",
          "#121b14",
        ],
        "circle-stroke-width": 2,
      },
    },
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
    },
    {
      id: "nodes-label",
      type: "symbol",
      source: "nodes",
      minzoom: 10,
      layout: {
        "text-field": ["get", "nome"],
        "text-font": ["Noto Sans Regular"],
        "text-size": 11,
        "text-anchor": "top",
        "text-offset": [0, 1.8],
        "text-padding": 4,
        "text-justify": "center",
      },
      paint: {
        "text-color": "#f8fafc",
        "text-halo-color": "#090f0b",
        "text-halo-width": 2.5,
        "text-halo-blur": 0.5,
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

// Enquadramento ciente do overlay: em telas >= md a sidebar ocupa a direita,
// então o padding direito cresce para o conteúdo não ficar escondido atrás dela.
const paddingLateral = (): number =>
  typeof window !== "undefined" && window.innerWidth >= 768 ? 380 : 40;

// nodeNum do pin (usado para selecionar); ausente/estranho não seleciona nada.
const nodeNumDoPin = (props: Record<string, unknown>): number | null =>
  typeof props.nodeNum === "number" ? props.nodeNum : null;

interface InfoPinPopup {
  nome: string;
  shortName: string;
  kind: NodeKind;
  hwModel: string;
  posTime: string | null;
  battery: number | null;
  hex: string;
  ageS?: number | null;
  timeFlag?: string | null;
}

const extrairInfoDoPin = (props: Record<string, unknown>): InfoPinPopup => {
  const nodeNum = nodeNumDoPin(props);
  const node =
    nodeNum !== null ? LocalState.localState.nodes[nodeNum] : undefined;
  return {
    nome: node?.nome ?? (typeof props.nome === "string" ? props.nome : "nó"),
    shortName:
      node?.shortName ??
      (typeof props.shortName === "string" ? props.shortName : ""),
    kind: (node?.kind ??
      (typeof props.kind === "string" ? props.kind : "unknown")) as NodeKind,
    hwModel:
      node?.hwModel ?? (typeof props.hwModel === "string" ? props.hwModel : ""),
    posTime:
      node?.posTime ??
      (typeof props.posTime === "string" ? props.posTime : null),
    battery:
      node?.battery ??
      (typeof props.battery === "number" ? props.battery : null),
    hex: nodeNum !== null ? node?.nodeId || `!${nodeNum.toString(16)}` : "",
    ageS:
      node?.ageS ??
      node?.age_s ??
      (typeof props.age_s === "number" ? props.age_s : null),
    timeFlag:
      node?.timeFlag ??
      node?.time_flag ??
      (typeof props.time_flag === "string" ? props.time_flag : null),
  };
};

const gerarHtmlPopup = (info: InfoPinPopup): string => {
  const rotuloModelo = info.hwModel
    ? (hardwareModelLabel({
        nome: info.nome,
        hwModel: info.hwModel,
      } as { nome: string; hwModel?: string | null }) ?? info.hwModel)
    : "Modelo não informado";
  const tagCurta = info.shortName
    ? `<span class="font-semibold text-slate-200">${esc(info.shortName)}</span><span class="text-slate-500">·</span>`
    : "";
  const tagBateria =
    info.battery !== null
      ? `<span class="text-emerald-400 font-semibold tabular-nums">${esc(batteryLabel(info.battery))}</span><span class="text-slate-600">·</span>`
      : "";

  const temAviso = isAgeWarning(
    { ageS: info.ageS, timeFlag: info.timeFlag, posTime: info.posTime },
    Date.now(),
  );
  const rotuloIdade = nodeAgeLabel(
    { ageS: info.ageS, timeFlag: info.timeFlag, posTime: info.posTime },
    Date.now(),
  );
  const tagIdade = `
    <span class="${temAviso ? "text-amber-400 font-semibold" : "text-slate-400"}" title="${info.timeFlag ? `Alerta de horário: ${esc(info.timeFlag)}` : "Idade da posição"}">
      ${esc(rotuloIdade)}
    </span>
    <span class="text-slate-600">·</span>
  `;

  return `
    <div class="px-3 py-2 text-slate-100 min-w-[240px] max-w-[320px]">
      <div class="font-bold text-white text-sm leading-snug truncate" title="${esc(info.nome)}">
        ${esc(info.nome)}
      </div>
      <div class="flex items-center gap-1.5 text-xs text-slate-300 mt-0.5">
        ${tagCurta}
        <span class="font-mono text-emerald-400 bg-slate-950 px-1 py-0.2 rounded border border-slate-800 text-[10px] font-medium">${esc(info.hex)}</span>
      </div>
      <div class="text-[11px] font-medium text-slate-300 mt-1.5 truncate">
        ${esc(rotuloModelo)}
      </div>
      <div class="flex items-center gap-1.5 text-[10px] text-slate-400 mt-1">
        ${tagBateria}
        ${tagIdade}
        <span>fix: ${esc(dataFixa(info.posTime))}</span>
      </div>
    </div>
  `;
};

// Popup do pin: nome + modelo com SVG ampliado + status + data do fix.
const abrirPopupDoPin = (
  map: maplibregl,
  pos: [number, number],
  props: Record<string, unknown>,
): Popup => {
  const info = extrairInfoDoPin(props);
  return new Popup({ offset: 16, maxWidth: "380px" })
    .setLngLat(pos)
    .setHTML(gerarHtmlPopup(info))
    .addTo(map);
};

const registrarIconeBarco = (map: maplibregl): void => {
  if (
    typeof window === "undefined" ||
    typeof map.addImage !== "function" ||
    map.hasImage?.("boat-icon")
  ) {
    return;
  }
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      canvas.width = 64;
      canvas.height = 64;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return;
      }
      ctx.drawImage(img, 0, 0, 64, 64);
      const imgData = ctx.getImageData(0, 0, 64, 64);
      if (!map.hasImage?.("boat-icon")) {
        map.addImage("boat-icon", imgData);
        map.triggerRepaint?.();
      }
    } catch {
      // Ignora falha de renderização canvas
    }
  };
  img.src = "/devices/boat.svg";
};

export const InitializeMap: Component<InitializeMapProps> = (props) => {
  const { api } = useData();
  const [mapRef, setMapRef] = createSignal<HTMLDivElement>();
  const [currentView, setCurrentView] = createSignal<maplibregl | undefined>(
    undefined,
  );
  const [carregado, setCarregado] = createSignal(false);
  let trackReq = 0; // descarta resposta de trilha de seleção anterior
  let boatTrackReq = 0; // descarta resposta de trilhas coletivas anterior
  let interagiu = false; // o usuário mexeu no mapa: para de recentralizar sozinho
  let idsCentralizados = ""; // conjunto de nós do último enquadramento automático
  let popup: Popup | null = null; // popup do pin, para fechar ao encerrar sessão
  // Pins reais (antes do espalhamento); reaplicados quando o zoom muda.
  let ultimosPins: Parameters<typeof espalharPinsSobrepostos>[0] | null = null;

  const aplicarPins = (map: maplibregl) => {
    const source = map.getSource("nodes") as GeoJSONSource | undefined;
    if (source === undefined || ultimosPins === null) {
      return;
    }
    const zoom = typeof map.getZoom === "function" ? map.getZoom() : 7;
    // biome-ignore lint/suspicious/noExplicitAny: FeatureCollection compatível com MapLibre
    source.setData(espalharPinsSobrepostos(ultimosPins, zoom) as any);
  };

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
      // Registra ícone de barco para nós fluviais
      registrarIconeBarco(map);
      map.on("styleimagemissing", (e) => {
        if (e.id === "boat-icon") {
          registrarIconeBarco(map);
        }
      });
      // Basemap próprio (offline): verifica se o arquivo existe e respeita o modo ativo
      existeBasemapLocal().then((existe) => {
        if (existe) {
          garantirBasemapLocal(map);
          const modoAtual = LocalState.localState.basemapMode;
          aplicarModoBasemap(map, modoAtual);
        }
      });
      // Cursor pointer ao passar o mouse sobre o pin
      const definirCursor = (cursor: string) => {
        if (typeof map.getCanvas === "function") {
          map.getCanvas().style.cursor = cursor;
        }
      };
      map.on("mouseenter", "nodes-circle", () => definirCursor("pointer"));
      map.on("mouseleave", "nodes-circle", () => definirCursor(""));
      // Pins espalhados dependem do zoom (raio constante em pixels).
      map.on("zoomend", () => aplicarPins(map));

      // Clique no pin: seleciona o nó (trilha + inspector) e abre o popup.
      map.on("click", "nodes-circle", (e) => {
        const f = e.features?.[0];
        if (f?.geometry.type !== "Point") {
          return;
        }
        const props = (f.properties ?? {}) as Record<string, unknown>;
        const selecionado = nodeNumDoPin(props);
        if (selecionado !== null) {
          LocalState.select(selecionado);
        }
        popup = abrirPopupDoPin(
          map,
          [f.geometry.coordinates[0], f.geometry.coordinates[1]],
          props,
        );
      });

      // Clique em área livre (sem pin sob o cursor): desseleciona e fecha o popup.
      map.on("click", (e) => {
        const sobrePin =
          map.queryRenderedFeatures(e.point, {
            layers: ["nodes-circle", "nodes-boat"].filter((id) =>
              map.getLayer(id),
            ),
          }).length > 0;
        if (sobrePin) {
          return;
        }
        popup?.remove();
        popup = null;
        LocalState.select(null);
      });
    };
    map.on("style.load", aoCarregar);
    map.on("load", aoCarregar);
  };

  // Ação explícita ("Centralizar no mapa"): só aqui a câmera se move além da
  // seleção/clique — nunca em tick de relógio ou atualização de polling.
  const centerOnNode = (nodeNum: number) => {
    const map = currentView();
    const node = LocalState.localState.nodes[nodeNum];
    if (
      map === undefined ||
      node === undefined ||
      !hasConfirmedPosition(node)
    ) {
      return;
    }
    map.flyTo({
      center: [node.lon, node.lat],
      zoom: Math.max(map.getZoom(), 12),
    });
  };

  // Ação explícita ("Enquadrar todos"): caixa de TODOS os nós com posição
  // confirmada, com padding que respeita a sidebar aberta.
  const fitAllNodes = () => {
    const map = currentView();
    if (map === undefined) {
      return;
    }
    const caixa = limitesDosPontos(
      Object.values(LocalState.localState.nodes).filter(hasConfirmedPosition),
    );
    if (caixa === null) {
      return;
    }
    // Decisão do usuário: o enquadramento automático não recoloca a câmera.
    interagiu = true;
    map.fitBounds(caixa, {
      padding: {
        top: 60,
        bottom: 40,
        left: 40,
        right: paddingLateral(),
      },
      maxZoom: 12,
      duration: 600,
    });
  };

  onCleanup(() => {
    currentView()?.remove();
    setCurrentView(undefined);
    setCarregado(false);
  });

  // Pins: qualquer mudança em nodes, filtros ou relógio reativo vira setData no source "nodes"
  // com os nós de posição CONFIRMADA que atendem aos filtros ativos.
  createEffect(() => {
    const todos = Object.values(LocalState.localState.nodes);
    const currentNow = LocalState.localState.nowMs;
    const nos = todos.filter((n) =>
      matchesFilters(
        n,
        {
          query: LocalState.localState.query,
          kindFilter: LocalState.localState.kindFilter,
          conditionFilter: LocalState.localState.conditionFilter,
        },
        currentNow,
      ),
    );
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    ultimosPins = nodesGeoJson(nos, currentNow) as NonNullable<
      typeof ultimosPins
    >;
    aplicarPins(map);
  });

  // Enquadra os nós da malha: na primeira carga e quando entra/sai um nó da malha —
  // a não ser que o usuário já tenha mexido no mapa ou haja um nó selecionado.
  // Filtros de busca, categoria, condição ou avanço do relógio NUNCA movem a câmera.
  createEffect(() => {
    const todos = Object.values(LocalState.localState.nodes);
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    const posicionados = todos.filter(hasConfirmedPosition);
    const ids = posicionados
      .map((n) => n.nodeNum)
      .sort()
      .join(",");
    const caixa = limitesDosPontos(posicionados);
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

  // Destaque visual do nó selecionado: halo pulsante/expandido e pin maior
  createEffect(() => {
    const sel = LocalState.localState.selected;
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    if (
      typeof map.setFilter === "function" &&
      map.getLayer("nodes-selected-halo")
    ) {
      map.setFilter(
        "nodes-selected-halo",
        sel !== null
          ? ["==", ["get", "nodeNum"], sel]
          : ["==", ["get", "nodeNum"], -1],
      );
    }
    if (
      typeof map.setPaintProperty === "function" &&
      map.getLayer("nodes-circle")
    ) {
      map.setPaintProperty("nodes-circle", "circle-radius", [
        "case",
        ["==", ["get", "kind"], "boat"],
        13,
        ["==", ["get", "nodeNum"], sel ?? -1],
        8,
        6,
      ]);
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
    // Só desloca a câmera para coordenada confirmada (nunca inventa posição);
    // a trilha é buscada de qualquer forma.
    if (hasConfirmedPosition(node)) {
      map.flyTo({
        center: [node.lon, node.lat],
        zoom: Math.max(map.getZoom(), 12),
      });
    }
    const req = ++trackReq;
    const target = node.nodeId || String(node.nodeNum);
    api
      .track(target)
      .then((t) => {
        if (req !== trackReq) {
          return; // seleção mudou durante o fetch
        }
        const segments =
          t.lines && t.lines.length > 0 ? t.lines : t.line ? [t.line] : [];

        if (segments.length > 0) {
          const lastSeg = segments[segments.length - 1];
          if (lastSeg.length >= 2) {
            const bearing = bearingDaTrilha(lastSeg);
            if (bearing !== null) {
              LocalState.setNodeBearing(sel, bearing);
            }
          }
        }

        const lineFeatures = segments.map((seg, idx) => ({
          type: "Feature" as const,
          properties: { segment: idx },
          geometry: { type: "LineString" as const, coordinates: seg },
        }));

        lineSrc.setData(
          lineFeatures.length === 0
            ? EMPTY_FC
            : {
                type: "FeatureCollection",
                features: lineFeatures,
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

  // Mapa base: reage a mudanças em localState.basemapMode
  createEffect(() => {
    const modo = LocalState.localState.basemapMode;
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }
    aplicarModoBasemap(map, modo);
  });

  // Trilhas coletivas dos barcos:
  // - Quando NENHUM nó estiver selecionado (selected === null): exibe as trilhas suaves de todos os barcos.
  // - Quando ALGUM nó for selecionado (selected !== null): oculta todas as trilhas coletivas (visibilidade = none).
  createEffect(() => {
    const sel = LocalState.localState.selected;
    const map = currentView();
    if (map === undefined || !carregado()) {
      return;
    }

    if (sel !== null) {
      // Regra 14: Se um nó for selecionado, esconde as trilhas de todos os outros barcos.
      if (map.getLayer("boat-tracks-line")) {
        map.setLayoutProperty("boat-tracks-line", "visibility", "none");
      }
      return;
    }

    // Regra 14: Nenhum nó selecionado -> exibe o caminho coletivo e suave dos barcos.
    if (map.getLayer("boat-tracks-line")) {
      map.setLayoutProperty("boat-tracks-line", "visibility", "visible");
    }

    const boatSrc = map.getSource("boat-tracks") as GeoJSONSource | undefined;
    if (boatSrc === undefined) {
      return;
    }

    const todosNos = Object.values(LocalState.localState.nodes);
    const nowMs = LocalState.localState.nowMs;
    const showInactive = LocalState.localState.showInactive;
    const barcos = todosNos.filter((n) => {
      if (!isBoatNode(n) || !hasConfirmedPosition(n)) {
        return false;
      }
      if (!showInactive && isNodeOlderThan7Days(n, nowMs)) {
        return false;
      }
      return true;
    });

    if (barcos.length === 0) {
      boatSrc.setData(EMPTY_FC);
      return;
    }

    if (typeof api?.track !== "function") {
      return;
    }

    const req = ++boatTrackReq;
    // Busca as trilhas dos barcos em paralelo e junta em um único FeatureCollection
    Promise.allSettled(
      barcos.map((b) => api.track(b.nodeId || String(b.nodeNum))),
    ).then((resultados) => {
      // Descarta se uma nova requisição de trilhas coletivas iniciou ou se um nó foi selecionado
      if (
        req !== boatTrackReq ||
        untrack(() => LocalState.localState.selected) !== null
      ) {
        return;
      }
      const features: Array<{
        type: "Feature";
        properties: { nodeNum: number };
        geometry: { type: "LineString"; coordinates: [number, number][] };
      }> = [];

      resultados.forEach((res, idx) => {
        if (res.status === "fulfilled") {
          const t = res.value;
          const segments =
            t.lines && t.lines.length > 0 ? t.lines : t.line ? [t.line] : [];
          for (const seg of segments) {
            if (seg.length >= 2) {
              features.push({
                type: "Feature",
                properties: { nodeNum: barcos[idx].nodeNum },
                geometry: {
                  type: "LineString",
                  coordinates: seg,
                },
              });
            }
          }
        }
      });

      boatSrc.setData(
        features.length === 0
          ? EMPTY_FC
          : {
              type: "FeatureCollection",
              // biome-ignore lint/suspicious/noExplicitAny: GeoJSON Features compatíveis com MapLibre
              features: features as any,
            },
      );
    });
  });

  return (
    <MapContext.Provider
      value={{ setMapRef, initializeMap, fitAllNodes, centerOnNode }}
    >
      {props.children}
    </MapContext.Provider>
  );
};
