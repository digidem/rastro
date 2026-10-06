import { bearingDaTrilha } from "../lib/bearing.js";
import type { NodeInfo } from "../store.js";

export type LngLat = [number, number];

export interface TrackPointFixture {
  lon: number;
  lat: number;
  altitudeM: number;
  sats: number;
  minutesAgo: number;
}

export interface MockNodeDefinition {
  id: string;
  nodeNum: number;
  nodeId: string;
  nome: string;
  shortName: string;
  kind: "boat" | "fixed_station" | "handheld";
  hwModel: string | null;
  battery: number | null;
  lon: number;
  lat: number;
  altitudeM: number | null;
  sats: number | null;
  timeSource: string | null;
  minutesAgo: number | null;
  trackPoints: TrackPointFixture[];
  telemetry?: {
    batteryLevel: number;
    voltage: number;
    channelUtil: number;
    airUtilTx: number;
    uptimeS: number;
  };
}

/**
 * Fixture completo derivado de devices/fleet.json do univaja-lora,
 * enriquecido com coordenadas geográficas coerentes no Vale do Javari
 * (bbox: [-74.2, -7.6, -70.0, -4.0]), trilhas e telemetria para
 * desenvolvimento local exclusivo da UI do mapa.
 */
export const FLEET_NODES: MockNodeDefinition[] = [
  {
    id: "heltec-v4-itq1",
    nodeNum: 2740643280,
    nodeId: "!a35ae5d0",
    nome: "univaja-itaquai-barco-1",
    shortName: "itq1",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 88,
    lon: -70.312,
    lat: -4.512,
    altitudeM: 82,
    sats: 8,
    timeSource: "device",
    minutesAgo: 2,
    trackPoints: [
      { lon: -70.198, lat: -4.382, altitudeM: 80, sats: 7, minutesAgo: 180 },
      { lon: -70.225, lat: -4.415, altitudeM: 81, sats: 8, minutesAgo: 140 },
      { lon: -70.26, lat: -4.46, altitudeM: 81, sats: 8, minutesAgo: 90 },
      { lon: -70.29, lat: -4.49, altitudeM: 82, sats: 8, minutesAgo: 45 },
      { lon: -70.312, lat: -4.512, altitudeM: 82, sats: 8, minutesAgo: 2 },
    ],
    telemetry: {
      batteryLevel: 88,
      voltage: 4.12,
      channelUtil: 3.2,
      airUtilTx: 0.15,
      uptimeS: 86400,
    },
  },
  {
    id: "cartao-2",
    nodeNum: 2277841729,
    nodeId: "!87c51b41",
    nome: "Meshtastic 1b41",
    shortName: "1b41",
    kind: "boat",
    hwModel: "TRACKER_T1000_E",
    battery: 94,
    lon: -70.285,
    lat: -4.475,
    altitudeM: 78,
    sats: 9,
    timeSource: "device",
    minutesAgo: 15,
    trackPoints: [
      { lon: -70.23, lat: -4.42, altitudeM: 76, sats: 8, minutesAgo: 120 },
      { lon: -70.255, lat: -4.445, altitudeM: 77, sats: 9, minutesAgo: 75 },
      { lon: -70.27, lat: -4.46, altitudeM: 78, sats: 9, minutesAgo: 40 },
      { lon: -70.285, lat: -4.475, altitudeM: 78, sats: 9, minutesAgo: 15 },
    ],
    telemetry: {
      batteryLevel: 94,
      voltage: 4.18,
      channelUtil: 2.1,
      airUtilTx: 0.08,
      uptimeS: 43200,
    },
  },
  {
    id: "heltec-v4-ccb1",
    nodeNum: 2740618360,
    nodeId: "!a35a8478",
    nome: "univaja-curuca-barco-1",
    shortName: "ccb1",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 101,
    lon: -71.215,
    lat: -4.952,
    altitudeM: 95,
    sats: 7,
    timeSource: "device",
    minutesAgo: 45,
    trackPoints: [
      { lon: -71.102, lat: -4.78, altitudeM: 92, sats: 7, minutesAgo: 240 },
      { lon: -71.14, lat: -4.835, altitudeM: 93, sats: 7, minutesAgo: 180 },
      { lon: -71.175, lat: -4.89, altitudeM: 94, sats: 8, minutesAgo: 120 },
      { lon: -71.198, lat: -4.925, altitudeM: 94, sats: 7, minutesAgo: 80 },
      { lon: -71.215, lat: -4.952, altitudeM: 95, sats: 7, minutesAgo: 45 },
    ],
    telemetry: {
      batteryLevel: 76,
      voltage: 3.98,
      channelUtil: 4.5,
      airUtilTx: 0.22,
      uptimeS: 129600,
    },
  },
  {
    id: "heltec-v4-itb1",
    nodeNum: 463845424,
    nodeId: "!1ba5b830",
    nome: "univaja-itui-barco-1",
    shortName: "itb1",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 101,
    lon: -70.521,
    lat: -4.815,
    altitudeM: 88,
    sats: 8,
    timeSource: "device",
    minutesAgo: 11520, // ~8 dias atrás (> 7 dias, dados reais da bancada)
    trackPoints: [
      { lon: -70.38, lat: -4.62, altitudeM: 85, sats: 7, minutesAgo: 11720 },
      { lon: -70.42, lat: -4.68, altitudeM: 86, sats: 8, minutesAgo: 11670 },
      { lon: -70.47, lat: -4.74, altitudeM: 87, sats: 8, minutesAgo: 11615 },
      { lon: -70.505, lat: -4.79, altitudeM: 88, sats: 8, minutesAgo: 11560 },
      { lon: -70.521, lat: -4.815, altitudeM: 88, sats: 8, minutesAgo: 11520 },
    ],
    telemetry: {
      batteryLevel: 82,
      voltage: 4.05,
      channelUtil: 2.8,
      airUtilTx: 0.12,
      uptimeS: 92000,
    },
  },
  {
    id: "tbeam-esc1",
    nodeNum: 4066790928,
    nodeId: "!f2664e10",
    nome: "escritorio-fixo-1",
    shortName: "esc1",
    kind: "fixed_station",
    hwModel: "TBEAM",
    battery: 100,
    lon: -70.1931,
    lat: -4.3718,
    altitudeM: 70,
    sats: 10,
    timeSource: "manual",
    minutesAgo: 5,
    trackPoints: [
      {
        lon: -70.1931,
        lat: -4.3718,
        altitudeM: 70,
        sats: 10,
        minutesAgo: 300,
      },
      {
        lon: -70.1931,
        lat: -4.3718,
        altitudeM: 70,
        sats: 10,
        minutesAgo: 180,
      },
      { lon: -70.1931, lat: -4.3718, altitudeM: 70, sats: 10, minutesAgo: 60 },
      { lon: -70.1931, lat: -4.3718, altitudeM: 70, sats: 10, minutesAgo: 5 },
    ],
    telemetry: {
      batteryLevel: 100,
      voltage: 4.25,
      channelUtil: 8.5,
      airUtilTx: 0.45,
      uptimeS: 604800,
    },
  },
  {
    id: "tracker-t1000-e-ctn1",
    nodeNum: 2080165616,
    nodeId: "!7bfccef0",
    nome: "univaja-cartao-1",
    shortName: "ctn1",
    kind: "handheld",
    hwModel: "TRACKER_T1000_E",
    battery: 68,
    lon: -70.215,
    lat: -4.398,
    altitudeM: 75,
    sats: 9,
    timeSource: "device",
    minutesAgo: 1,
    trackPoints: [
      { lon: -70.195, lat: -4.373, altitudeM: 72, sats: 8, minutesAgo: 90 },
      { lon: -70.201, lat: -4.38, altitudeM: 73, sats: 9, minutesAgo: 65 },
      { lon: -70.208, lat: -4.387, altitudeM: 74, sats: 9, minutesAgo: 45 },
      { lon: -70.212, lat: -4.392, altitudeM: 74, sats: 9, minutesAgo: 20 },
      { lon: -70.215, lat: -4.398, altitudeM: 75, sats: 9, minutesAgo: 1 },
    ],
    telemetry: {
      batteryLevel: 68,
      voltage: 3.89,
      channelUtil: 1.8,
      airUtilTx: 0.05,
      uptimeS: 28800,
    },
  },
  {
    id: "heltec-v4-mjb1",
    nodeNum: 2409765948,
    nodeId: "!8fa21c3c",
    nome: "univaja-medio-javari-barco-1",
    shortName: "mjb1",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 91,
    lon: -70.72,
    lat: -4.685,
    altitudeM: 92,
    sats: 8,
    timeSource: "device",
    minutesAgo: 60,
    trackPoints: [
      { lon: -70.45, lat: -4.48, altitudeM: 89, sats: 7, minutesAgo: 280 },
      { lon: -70.53, lat: -4.54, altitudeM: 90, sats: 8, minutesAgo: 220 },
      { lon: -70.61, lat: -4.6, altitudeM: 91, sats: 8, minutesAgo: 160 },
      { lon: -70.67, lat: -4.65, altitudeM: 91, sats: 8, minutesAgo: 100 },
      { lon: -70.72, lat: -4.685, altitudeM: 92, sats: 8, minutesAgo: 60 },
    ],
    telemetry: {
      batteryLevel: 91,
      voltage: 4.15,
      channelUtil: 3.5,
      airUtilTx: 0.16,
      uptimeS: 172800,
    },
  },
  {
    id: "cartao-3",
    nodeNum: 1374739518,
    nodeId: "!51f0dc3e",
    nome: "cartao-3",
    shortName: "C3",
    kind: "boat",
    hwModel: "TRACKER_T1000_E",
    battery: 55,
    lon: -70.485,
    lat: -4.76,
    altitudeM: 84,
    sats: 6,
    timeSource: "device",
    minutesAgo: 180,
    trackPoints: [
      { lon: -70.43, lat: -4.7, altitudeM: 83, sats: 6, minutesAgo: 300 },
      { lon: -70.455, lat: -4.725, altitudeM: 83, sats: 6, minutesAgo: 240 },
      { lon: -70.485, lat: -4.76, altitudeM: 84, sats: 6, minutesAgo: 180 },
    ],
    telemetry: {
      batteryLevel: 55,
      voltage: 3.75,
      channelUtil: 1.2,
      airUtilTx: 0.04,
      uptimeS: 54000,
    },
  },
  {
    id: "heltec-v4-jqb1",
    nodeNum: 2740733912,
    nodeId: "!a35c47d8",
    nome: "univaja-jaquirana-barco-1",
    shortName: "jqb1",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 83,
    lon: -72.015,
    lat: -5.624,
    altitudeM: 110,
    sats: 7,
    timeSource: "device",
    minutesAgo: 110,
    trackPoints: [
      { lon: -71.85, lat: -5.41, altitudeM: 105, sats: 7, minutesAgo: 360 },
      { lon: -71.905, lat: -5.48, altitudeM: 107, sats: 7, minutesAgo: 290 },
      { lon: -71.95, lat: -5.54, altitudeM: 108, sats: 7, minutesAgo: 220 },
      { lon: -71.985, lat: -5.585, altitudeM: 109, sats: 7, minutesAgo: 160 },
      { lon: -72.015, lat: -5.624, altitudeM: 110, sats: 7, minutesAgo: 110 },
    ],
    telemetry: {
      batteryLevel: 83,
      voltage: 4.07,
      channelUtil: 3.0,
      airUtilTx: 0.14,
      uptimeS: 150000,
    },
  },
  {
    id: "itui-campo-1",
    nodeNum: 463605292,
    nodeId: "!1ba1ee2c",
    nome: "ITUI_CAMPO_01",
    shortName: "IU1M",
    kind: "fixed_station",
    hwModel: "HELTEC_V4",
    battery: 98,
    lon: -70.548,
    lat: -4.862,
    altitudeM: 86,
    sats: 8,
    timeSource: "device",
    minutesAgo: 8,
    trackPoints: [
      { lon: -70.548, lat: -4.862, altitudeM: 86, sats: 8, minutesAgo: 180 },
      { lon: -70.548, lat: -4.862, altitudeM: 86, sats: 8, minutesAgo: 60 },
      { lon: -70.548, lat: -4.862, altitudeM: 86, sats: 8, minutesAgo: 8 },
    ],
    telemetry: {
      batteryLevel: 98,
      voltage: 4.22,
      channelUtil: 5.2,
      airUtilTx: 0.28,
      uptimeS: 345600,
    },
  },
  {
    id: "itaquai-campo-1",
    nodeNum: 463618540,
    nodeId: "!1ba221ec",
    nome: "ITAQUAI_CAMPO_01",
    shortName: "IQ1M",
    kind: "fixed_station",
    hwModel: "HELTEC_V4",
    battery: 95,
    lon: -70.354,
    lat: -4.582,
    altitudeM: 80,
    sats: 9,
    timeSource: "device",
    minutesAgo: 10,
    trackPoints: [
      { lon: -70.354, lat: -4.582, altitudeM: 80, sats: 9, minutesAgo: 200 },
      { lon: -70.354, lat: -4.582, altitudeM: 80, sats: 9, minutesAgo: 80 },
      { lon: -70.354, lat: -4.582, altitudeM: 80, sats: 9, minutesAgo: 10 },
    ],
    telemetry: {
      batteryLevel: 95,
      voltage: 4.19,
      channelUtil: 4.8,
      airUtilTx: 0.25,
      uptimeS: 410000,
    },
  },
  {
    id: "curuca-campo-1",
    nodeNum: 463577412,
    nodeId: "!1ba18144",
    nome: "CURUCA_CAMPO_01",
    shortName: "CU1M",
    kind: "fixed_station",
    hwModel: "HELTEC_V4",
    battery: 90,
    lon: -71.248,
    lat: -5.021,
    altitudeM: 98,
    sats: 7,
    timeSource: "device",
    minutesAgo: 12960, // 9 dias atrás
    trackPoints: [
      { lon: -71.248, lat: -5.021, altitudeM: 98, sats: 7, minutesAgo: 13200 },
      { lon: -71.248, lat: -5.021, altitudeM: 98, sats: 7, minutesAgo: 13080 },
      { lon: -71.248, lat: -5.021, altitudeM: 98, sats: 7, minutesAgo: 12960 },
    ],
    telemetry: {
      batteryLevel: 90,
      voltage: 4.14,
      channelUtil: 3.9,
      airUtilTx: 0.19,
      uptimeS: 259200,
    },
  },
  {
    id: "medio-javari-campo-1",
    nodeNum: 4134205900,
    nodeId: "!f66af9cc",
    nome: "MEDIO_JAVARI_CAMPO_01",
    shortName: "MJ1M",
    kind: "fixed_station",
    hwModel: "HELTEC_V4",
    battery: 92,
    lon: -70.785,
    lat: -4.74,
    altitudeM: 90,
    sats: 8,
    timeSource: "device",
    minutesAgo: 30,
    trackPoints: [
      { lon: -70.785, lat: -4.74, altitudeM: 90, sats: 8, minutesAgo: 240 },
      { lon: -70.785, lat: -4.74, altitudeM: 90, sats: 8, minutesAgo: 110 },
      { lon: -70.785, lat: -4.74, altitudeM: 90, sats: 8, minutesAgo: 30 },
    ],
    telemetry: {
      batteryLevel: 92,
      voltage: 4.16,
      channelUtil: 4.2,
      airUtilTx: 0.21,
      uptimeS: 310000,
    },
  },
  {
    id: "jaquirana-campo-1",
    nodeNum: 463662676,
    nodeId: "!1ba2ce54",
    nome: "JAQUIRANA_CAMPO_01",
    shortName: "JQ1M",
    kind: "fixed_station",
    hwModel: "HELTEC_V4",
    battery: 89,
    lon: -72.085,
    lat: -5.71,
    altitudeM: 115,
    sats: 7,
    timeSource: "device",
    minutesAgo: 17280, // 12 dias atrás
    trackPoints: [
      { lon: -72.085, lat: -5.71, altitudeM: 115, sats: 7, minutesAgo: 17500 },
      { lon: -72.085, lat: -5.71, altitudeM: 115, sats: 7, minutesAgo: 17350 },
      { lon: -72.085, lat: -5.71, altitudeM: 115, sats: 7, minutesAgo: 17280 },
    ],
    telemetry: {
      batteryLevel: 89,
      voltage: 4.11,
      channelUtil: 3.6,
      airUtilTx: 0.17,
      uptimeS: 198000,
    },
  },
  {
    id: "emb-01",
    nodeNum: 2625745619,
    nodeId: "!9c81b2d3",
    nome: "EMB_01",
    shortName: "E01",
    kind: "boat",
    hwModel: "HELTEC_V4",
    battery: 73,
    lon: -70.36,
    lat: -4.59,
    altitudeM: 81,
    sats: 8,
    timeSource: "device",
    minutesAgo: 18,
    trackPoints: [
      { lon: -70.34, lat: -4.55, altitudeM: 80, sats: 8, minutesAgo: 150 },
      { lon: -70.35, lat: -4.57, altitudeM: 81, sats: 8, minutesAgo: 90 },
      { lon: -70.36, lat: -4.59, altitudeM: 81, sats: 8, minutesAgo: 18 },
    ],
    telemetry: {
      batteryLevel: 73,
      voltage: 3.94,
      channelUtil: 2.4,
      airUtilTx: 0.11,
      uptimeS: 82000,
    },
  },
  {
    id: "axm4-f260",
    nodeNum: 1128133216,
    nodeId: "!433df260",
    nome: "AXM4 f260 GTW SCONRADO-RJ",
    shortName: "AXM4",
    kind: "fixed_station",
    hwModel: null,
    battery: null,
    lon: -70.1931,
    lat: -4.3718,
    altitudeM: null,
    sats: null,
    timeSource: null,
    minutesAgo: null, // Testa UI para nós com "sem fix" / "bateria —"
    trackPoints: [],
  },
];

// Tuplas para não esbarrar em regras de camelCase ao gerar contratos da API
const props = (...kv: [string, unknown][]): Record<string, unknown> =>
  Object.fromEntries(kv);

/** Calcula timestamp ISO UTC relativo a `now` menos `minutesAgo` minutos. */
export function relativeIso(
  minutesAgo: number | null,
  now = new Date(),
): string | null {
  if (minutesAgo === null) {
    return null;
  }
  return new Date(now.getTime() - minutesAgo * 60 * 1000).toISOString();
}

/** Calcula o rumo a partir dos últimos pontos de trilha conhecidos do nó. */
function mockNodeBearing(d: MockNodeDefinition): number | null {
  if (!d.trackPoints || d.trackPoints.length < 2) {
    return null;
  }
  const sorted = [...d.trackPoints].sort((a, b) => b.minutesAgo - a.minutesAgo);
  return bearingDaTrilha(sorted.map((p) => [p.lon, p.lat]));
}

/** Converte a lista da frota no formato `NodeInfo[]` consumido pelo store do Solid. */
export function getMockNodeInfoList(now = new Date()): NodeInfo[] {
  return FLEET_NODES.map((d) => {
    const posTime = relativeIso(d.minutesAgo, now);
    const ageSeconds = d.minutesAgo !== null ? d.minutesAgo * 60 : null;
    return {
      nodeNum: d.nodeNum,
      nodeId: d.nodeId,
      nome: d.nome,
      posTime,
      battery: d.battery,
      lon: d.lon,
      lat: d.lat,
      shortName: d.shortName,
      kind: d.kind,
      hwModel: d.hwModel,
      altitudeM: d.altitudeM,
      sats: d.sats,
      timeSource: d.timeSource,
      receivedAt: posTime,
      bearing: mockNodeBearing(d),
      ageS: ageSeconds,
      age_s: ageSeconds,
    };
  });
}

/** Localiza definição de nó por id, nodeId ou nodeNum. */
export function findMockNode(
  target: string | number,
): MockNodeDefinition | undefined {
  const t = String(target).toLowerCase();
  const hexClean = t.startsWith("!") ? t.slice(1) : t;
  const num = Number(target);

  return FLEET_NODES.find((d) => {
    if (d.id.toLowerCase() === t) {
      return true;
    }
    if (d.nodeId.toLowerCase() === t || d.nodeId.toLowerCase() === `!${t}`) {
      return true;
    }
    if (d.shortName.toLowerCase() === t) {
      return true;
    }
    if (d.nome.toLowerCase() === t) {
      return true;
    }
    if (Number.isFinite(num) && d.nodeNum === num) {
      return true;
    }
    const nodeHex = d.nodeNum.toString(16).toLowerCase();
    if (nodeHex === hexClean) {
      return true;
    }
    return false;
  });
}

/**
 * Constrói resposta GeoJSON para GET /api/nodes/latest exatamente compatível
 * com o contrato da API Python do Rastro (FeatureCollection com Point features).
 */
export function getLatestGeoJson(now = new Date()): {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: Record<string, unknown>;
  }>;
} {
  const features = FLEET_NODES.map((d) => {
    const posTime = relativeIso(d.minutesAgo, now);
    return {
      type: "Feature" as const,
      geometry: {
        type: "Point" as const,
        coordinates: [d.lon, d.lat] as [number, number],
      },
      properties: props(
        ["node_num", d.nodeNum],
        ["node_id", d.nodeId],
        ["nome", d.nome],
        ["short_name", d.shortName],
        ["kind", d.kind],
        ["hw_model", d.hwModel],
        ["pos_time", posTime],
        ["time_source", d.timeSource],
        ["altitude_m", d.altitudeM],
        ["sats", d.sats],
        ["battery", d.battery],
        ["received_at", posTime],
        ["bearing", mockNodeBearing(d)],
        ["age_s", d.minutesAgo !== null ? d.minutesAgo * 60 : null],
      ),
    };
  });

  return {
    type: "FeatureCollection",
    features,
  };
}

export interface TrackQueryOptions {
  from?: string | null;
  to?: string | null;
  limit?: number | null;
}

/**
 * Constrói resposta GeoJSON para GET /api/nodes/:node/track compatível com
 * o formato da API (LineString inicial + Point features de cada fix).
 */
export function getTrackGeoJson(
  target: string | number,
  now = new Date(),
  options?: TrackQueryOptions,
): {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry:
      | { type: "LineString"; coordinates: [number, number][] }
      | { type: "Point"; coordinates: [number, number] };
    properties: Record<string, unknown>;
  }>;
} {
  const node = findMockNode(target);
  if (!node || node.trackPoints.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }

  // Ordena cronologicamente (do mais antigo para o mais recente)
  let sorted = [...node.trackPoints].sort(
    (a, b) => b.minutesAgo - a.minutesAgo,
  );

  const nowMs = now.getTime();
  if (options?.from) {
    const fromMs = Date.parse(options.from);
    if (!Number.isNaN(fromMs)) {
      sorted = sorted.filter((p) => nowMs - p.minutesAgo * 60000 >= fromMs);
    }
  }

  if (options?.to) {
    const toMs = Date.parse(options.to);
    if (!Number.isNaN(toMs)) {
      sorted = sorted.filter((p) => nowMs - p.minutesAgo * 60000 <= toMs);
    }
  }

  if (
    options?.limit !== undefined &&
    options?.limit !== null &&
    options.limit > 0
  ) {
    sorted = sorted.slice(-options.limit);
  }

  if (sorted.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }

  const coordinates: [number, number][] = sorted.map((p) => [p.lon, p.lat]);
  const oldestTime = relativeIso(sorted[0].minutesAgo, now);
  const newestTime = relativeIso(sorted[sorted.length - 1].minutesAgo, now);

  const lineFeature = {
    type: "Feature" as const,
    geometry: {
      type: "LineString" as const,
      coordinates,
    },
    properties: {
      node: node.nodeId,
      nome: node.nome,
      from: oldestTime,
      de: oldestTime,
      to: newestTime,
    },
  };

  const pointFeatures = sorted.map((p) => ({
    type: "Feature" as const,
    geometry: {
      type: "Point" as const,
      coordinates: [p.lon, p.lat] as [number, number],
    },
    properties: props(
      ["pos_time", relativeIso(p.minutesAgo, now)],
      ["time_source", node.timeSource ?? "device"],
      ["sats", p.sats],
    ),
  }));

  return {
    type: "FeatureCollection",
    features: [lineFeature, ...pointFeatures],
  };
}

export interface TelemetryQueryOptions {
  from?: string | null;
  to?: string | null;
  limit?: number | null;
}

/**
 * Constrói resposta GeoJSON para GET /api/nodes/:node/telemetry.
 */
export function getTelemetryGeoJson(
  target: string | number,
  now = new Date(),
  options?: TelemetryQueryOptions,
): {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: null;
    properties: Record<string, unknown>;
  }>;
} {
  const node = findMockNode(target);
  if (!node?.telemetry) {
    return { type: "FeatureCollection", features: [] };
  }

  if (
    options?.limit !== undefined &&
    options?.limit !== null &&
    options.limit <= 0
  ) {
    return { type: "FeatureCollection", features: [] };
  }

  const telemMs = now.getTime() - (node.minutesAgo ?? 0) * 60000;
  if (options?.from) {
    const fromMs = Date.parse(options.from);
    if (!Number.isNaN(fromMs) && telemMs < fromMs) {
      return { type: "FeatureCollection", features: [] };
    }
  }

  if (options?.to) {
    const toMs = Date.parse(options.to);
    if (!Number.isNaN(toMs) && telemMs > toMs) {
      return { type: "FeatureCollection", features: [] };
    }
  }

  const telemTime = relativeIso(node.minutesAgo, now);
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: null,
        properties: props(
          ["battery_level", node.telemetry.batteryLevel],
          ["voltage", node.telemetry.voltage],
          ["channel_util", node.telemetry.channelUtil],
          ["air_util_tx", node.telemetry.airUtilTx],
          ["uptime_s", node.telemetry.uptimeS],
          ["telem_time", telemTime],
        ),
      },
    ],
  };
}
