import type {
  ConditionFilter,
  KindFilter,
  NodeInfo,
  NodeKind,
} from "../store.js";

/**
 * Regras puras de apresentação da malha: posição confirmada, busca, filtros,
 * idade do fix e GeoJSON dos pins. Nada aqui toca store, rede ou MapLibre —
 * por isso é testável sem DOM.
 */

/** Limite visual inicial de "fix antigo"; não é diagnóstico de falha de rádio. */
const LIMITE_FIX_ANTIGO_MS = 12 * 60 * 60 * 1000;

/** Folga para timestamps à frente: acima disso o relógio do nó está suspeito. */
const TOLERANCIA_FUTURO_MS = 5 * 60 * 1000;

const FUSO_JAVARI = "America/Manaus";

const FORMATO_JAVARI = new Intl.DateTimeFormat("pt-BR", {
  timeZone: FUSO_JAVARI,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Busca sem acentos, sem caixa e sem espaços nas pontas. */
const normalizar = (s: string): string =>
  s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();

/** Hex do nodeNum (a base do `!abcd1234` do Meshtastic). */
const hexDoNo = (nodeNum: number): string => nodeNum.toString(16).toLowerCase();

/**
 * Posição confirmada: coordenadas finitas dentro dos intervalos geográficos E
 * `posTime` ISO parseável. Sem isso o par lon/lat não vira pin nem enquadramento
 * — nunca converter ausência em `[0, 0]`.
 */
export function hasConfirmedPosition(node: NodeInfo): boolean {
  if (!(Number.isFinite(node.lon) && Math.abs(node.lon) <= 180)) {
    return false;
  }
  if (!(Number.isFinite(node.lat) && Math.abs(node.lat) <= 90)) {
    return false;
  }
  if (node.posTime === null) {
    return false;
  }
  return !Number.isNaN(Date.parse(node.posTime));
}

/**
 * Busca textual contra nome, nome curto, nodeId (`!abcd1234`) e hex do nodeNum
 * (com ou sem `!`). Query vazia (ou só `!`) não filtra nada.
 */
export function matchesQuery(node: NodeInfo, query: string): boolean {
  const q = normalizar(query);
  if (q === "") {
    return true;
  }
  const semBang = q.startsWith("!") ? q.slice(1) : q;
  if (semBang === "") {
    return true;
  }
  const campos = [
    normalizar(node.nome),
    normalizar(node.shortName ?? ""),
    normalizar(node.nodeId),
    hexDoNo(node.nodeNum),
  ];
  return campos.some((c) => c !== "" && c.includes(semBang));
}

/**
 * Detecta se o nó é um barco:
 * - kind === "boat"
 * - ou se o nome contiver "barco" (normalizado sem acentos, case-insensitive).
 */
export function isBoatNode(node: NodeInfo): boolean {
  const nome = typeof node.nome === "string" ? normalizar(node.nome) : "";
  // Exclusão estrita: rádios portáteis e estações fixas nunca são barco.
  if (NAO_BARCO_RE.test(nome)) {
    return false;
  }
  if (node.kind === "boat") {
    return true;
  }
  return nome.includes("barco");
}

const NAO_BARCO_RE = /movel|handheld|cartao|teto|base|fixo|t1000/;

/** Infere kind pelo nome quando o backend não informa um explícito. */
function inferKindFromName(nome?: string): NodeKind | null {
  if (typeof nome !== "string") {
    return null;
  }
  const n = normalizar(nome);
  if (/teto|fixo|base/.test(n)) {
    return "fixed_station";
  }
  if (/movel|handheld|cartao|t1000/.test(n)) {
    return "handheld";
  }
  return null;
}

/** Categoria efetiva: barco pelo nome/kind; senão kind explícito, inferido do nome ou "unknown". */
export function nodeKind(node: NodeInfo): NodeKind {
  if (isBoatNode(node)) {
    return "boat";
  }
  if (node.kind === "boat") {
    // kind=boat do backend contradito pelo nome (movel/teto/cartao…): infere.
    return inferKindFromName(node.nome) ?? "unknown";
  }
  return node.kind ?? inferKindFromName(node.nome) ?? "unknown";
}

/** Fix estritamente mais velho que 12 h (timestamp inválido nunca é "antigo"). */
export function isFixStale(posTime: string | null, nowMs: number): boolean {
  if (posTime === null) {
    return false;
  }
  const t = Date.parse(posTime);
  if (Number.isNaN(t)) {
    return false;
  }
  return nowMs - t > LIMITE_FIX_ANTIGO_MS;
}

const SETE_DIAS_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Retorna true se o nó tem mais de 7 dias sem fix/comunicação.
 * Prioriza ageS/age_s se presente, senão compara posTime com nowMs.
 */
export function isNodeOlderThan7Days(
  node: {
    ageS?: number | null;
    age_s?: number | null;
    posTime?: string | null;
  },
  nowMs: number,
): boolean {
  const ageSeconds = node.ageS ?? node.age_s;
  if (typeof ageSeconds === "number" && Number.isFinite(ageSeconds)) {
    return ageSeconds > 7 * 86400;
  }
  if (node.posTime) {
    const t = Date.parse(node.posTime);
    if (!Number.isNaN(t)) {
      return nowMs - t > SETE_DIAS_MS;
    }
  }
  return false;
}

export interface ViewerFilters {
  query: string;
  kindFilter: KindFilter;
  conditionFilter: ConditionFilter;
}

/** Faixa visual da bateria: 0 é válido; fora de 0–100 não é percentual. */
export type BatteryLevel = "none" | "out-of-scale" | "critical" | "low" | "ok";

export function batteryLevel(battery: number | null): BatteryLevel {
  if (battery === null) {
    return "none";
  }
  if (battery < 0 || battery > 100) {
    return "out-of-scale";
  }
  if (battery < 20) {
    return "critical";
  }
  if (battery < 50) {
    return "low";
  }
  return "ok";
}

/** Texto de bateria para leitura rápida em campo. */
export function batteryLabel(battery: number | null): string {
  if (battery === null) {
    return "bateria —";
  }
  if (battery < 0 || battery > 100) {
    return "bateria fora da escala";
  }
  return `bateria ${battery}%`;
}

/** Busca + categoria + condição combinam por AND. */
export function matchesFilters(
  node: NodeInfo,
  filtros: ViewerFilters,
  nowMs: number,
): boolean {
  if (!matchesQuery(node, filtros.query)) {
    return false;
  }
  if (filtros.kindFilter !== "all" && nodeKind(node) !== filtros.kindFilter) {
    return false;
  }
  switch (filtros.conditionFilter) {
    case "no-position":
      return !hasConfirmedPosition(node);
    case "stale":
      return hasConfirmedPosition(node) && isFixStale(node.posTime, nowMs);
    default:
      return true;
  }
}

/**
 * Idade do fix em PT-BR. Timestamp futuro até 5 min vira "agora" (clamp);
 * acima disso é "horário inconsistente", sem badge de frescor.
 */
export function fixAgeLabel(iso: string | null, nowMs: number): string {
  if (iso === null) {
    return "sem fix";
  }
  const t = Date.parse(iso);
  if (Number.isNaN(t)) {
    return "sem fix";
  }
  const delta = nowMs - t;
  if (delta < -TOLERANCIA_FUTURO_MS) {
    return "horário inconsistente";
  }
  const s = Math.max(0, Math.floor(delta / 1000));
  if (s < 60) {
    return "agora";
  }
  if (s < 3600) {
    return `há ${Math.floor(s / 60)} min`;
  }
  if (s < 86400) {
    return `há ${Math.floor(s / 3600)} h`;
  }
  return `há ${Math.floor(s / 86400)} d`;
}

/** Formata idade em segundos (age_s retornado de /api/nodes/latest). */
export function formatAgeFromSeconds(
  ageS: number | null | undefined,
): string | null {
  if (ageS === null || ageS === undefined || !Number.isFinite(ageS)) {
    return null;
  }
  if (ageS < 60) {
    return "agora";
  }
  if (ageS < 3600) {
    return `há ${Math.floor(ageS / 60)} min`;
  }
  if (ageS < 86400) {
    return `há ${Math.floor(ageS / 3600)} h`;
  }
  return `há ${Math.floor(ageS / 86400)} d`;
}

/**
 * Sinaliza necessidade de estilo de aviso:
 * time_flag definido ou idade do fix estritamente maior que 1 h (3600 s).
 */
export function isAgeWarning(
  node: {
    ageS?: number | null;
    age_s?: number | null;
    timeFlag?: string | null;
    time_flag?: string | null;
    posTime?: string | null;
  },
  nowMs: number,
): boolean {
  if (node.timeFlag || node.time_flag) {
    return true;
  }
  const ageSeconds = node.ageS ?? node.age_s;
  if (typeof ageSeconds === "number" && Number.isFinite(ageSeconds)) {
    return ageSeconds > 3600;
  }
  if (node.posTime) {
    const t = Date.parse(node.posTime);
    if (!Number.isNaN(t)) {
      return nowMs - t > 3600 * 1000;
    }
  }
  return false;
}

/**
 * Rótulo de idade para o nó: prioriza age_s se presente, senão usa posTime.
 */
export function nodeAgeLabel(
  node: {
    ageS?: number | null;
    age_s?: number | null;
    posTime?: string | null;
    timeFlag?: string | null;
    time_flag?: string | null;
  },
  nowMs: number,
): string {
  const ageSeconds = node.ageS ?? node.age_s;
  if (typeof ageSeconds === "number" && Number.isFinite(ageSeconds)) {
    return formatAgeFromSeconds(ageSeconds) ?? "sem fix";
  }
  return fixAgeLabel(node.posTime ?? null, nowMs);
}

/** Data exata no horário do Javari, formato dd/MM/yyyy HH:mm:ss. */
export function formatDateTimeJavari(iso: string | null): string {
  if (iso === null) {
    return "sem fix";
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return "sem fix";
  }
  const partes = FORMATO_JAVARI.formatToParts(d);
  const valor = (tipo: Intl.DateTimeFormatPartTypes): string =>
    partes.find((p) => p.type === tipo)?.value ?? "";
  return `${valor("day")}/${valor("month")}/${valor("year")} ${valor("hour")}:${valor("minute")}:${valor("second")}`;
}

/**
 * FeatureCollection dos pins: apenas nós com posição confirmada, com as
 * propriedades que pintam por categoria e sinalizam fix antigo.
 */
export function nodesGeoJson(
  nos: NodeInfo[],
  nowMs: number,
): // biome-ignore lint/correctness/noUndeclaredVariables: GeoJSON é o namespace global dos tipos de @types/geojson (transitivo do maplibre-gl); o Biome só conhece globais de browser/Node.
GeoJSON.FeatureCollection {
  const features = nos.filter(hasConfirmedPosition).map((n) => {
    const properties: Record<string, unknown> = {
      id: n.nodeNum,
      nodeNum: n.nodeNum,
      nome: n.nome,
      shortName: n.shortName ?? "",
      kind: nodeKind(n),
      hwModel: n.hwModel ?? "",
      posTime: n.posTime,
      isStale: isFixStale(n.posTime, nowMs),
      battery: n.battery,
      bearing:
        typeof n.bearing === "number" && Number.isFinite(n.bearing)
          ? ((n.bearing % 360) + 360) % 360
          : 0,
    };
    if (n.ageS !== undefined || n.age_s !== undefined) {
      properties.age_s = n.ageS ?? n.age_s;
    }
    if (n.timeFlag !== undefined || n.time_flag !== undefined) {
      properties.time_flag = n.timeFlag ?? n.time_flag;
    }
    return {
      type: "Feature" as const,
      properties,
      geometry: {
        type: "Point" as const,
        coordinates: [n.lon, n.lat],
      },
    };
  });
  return { type: "FeatureCollection", features };
}

/**
 * Mapeia o modelo de rádio para o SVG da placa de hardware (public/devices/*.svg).
 * Os SVGs são servidos localmente, sem nenhuma CDN externa.
 */
export function deviceModelSvgUrl(hwModel: string | null | undefined): string {
  if (!hwModel) {
    return "/devices/unknown.svg";
  }
  const m = hwModel.toUpperCase().replace(/[- ]/g, "_");
  if (m.includes("HELTEC_V4")) {
    return "/devices/heltec_v4.svg";
  }
  if (m.includes("HELTEC_V3") || m.includes("HELTEC")) {
    return "/devices/heltec-v3.svg";
  }
  if (m.includes("TBEAM") || m.includes("T_BEAM")) {
    return "/devices/tbeam.svg";
  }
  if (m.includes("T1000")) {
    return "/devices/tracker-t1000-e.svg";
  }
  if (m.includes("WISMESH_TAG") || m.includes("RAK_TAG")) {
    return "/devices/rak_wismesh_tag.svg";
  }
  if (m.includes("RAK4631") || m.includes("RAK_4631")) {
    return "/devices/rak4631.svg";
  }
  if (m.includes("ECHO")) {
    return "/devices/t-echo.svg";
  }
  return "/devices/unknown.svg";
}

export function inferHardwareFromName(nome?: string): string | null {
  if (!nome) {
    return null;
  }
  const n = normalizar(nome);
  if (n.includes("heltec-v4") || n.includes("heltec_v4")) {
    return "HELTEC_V4";
  }
  if (n.includes("heltec-v3") || n.includes("heltec_v3")) {
    return "HELTEC_V3";
  }
  if (n.includes("tbeam") || n.includes("t-beam")) {
    return "TBEAM";
  }
  if (n.includes("t1000") || /(?:^|[-_\s])cartao(?:[-_\s\d]|$)/.test(n)) {
    return "TRACKER_T1000_E";
  }
  if (n.includes("wismesh") || n.includes("rak-tag") || n.includes("rak_tag")) {
    return "WISMESH_TAG";
  }
  if (
    n.includes("rak4631") ||
    n.includes("rak_4631") ||
    /(?:^|[-_\s])rak(?:[-_\s\d]|$)/.test(n)
  ) {
    return "RAK4631";
  }
  if (
    n.includes("t-echo") ||
    n.includes("techo") ||
    /(?:^|[-_\s])echo(?:[-_\s\d]|$)/.test(n)
  ) {
    return "ECHO";
  }
  // Convenções da frota Vale do Javari (barcos, bases em campo e rádios móveis utilizam Heltec V4 por padrão)
  if (
    n.includes("barco") ||
    n.includes("campo") ||
    n.includes("emb_") ||
    n.includes("emb-") ||
    n.includes("movel") ||
    n.includes("teto")
  ) {
    return "HELTEC_V4";
  }
  return null;
}

/**
 * Rótulo amigável do modelo de hardware para exibição textual na sidebar.
 * Normaliza nomes técnicos e infere do nome do nó quando hwModel estiver ausente.
 */
export function hardwareModelLabel(node: NodeInfo): string | null {
  const raw = node.hwModel || inferHardwareFromName(node.nome);
  if (!raw) {
    return null;
  }
  const m = raw.toUpperCase().replace(/[- ]/g, "_");
  if (m.includes("HELTEC_V4")) {
    return "Heltec V4";
  }
  if (m.includes("HELTEC_V3")) {
    return "Heltec V3";
  }
  if (m.includes("HELTEC")) {
    return "Heltec";
  }
  if (m.includes("TBEAM") || m.includes("T_BEAM")) {
    return "T-Beam";
  }
  if (m.includes("T1000")) {
    return "T1000-E";
  }
  if (m.includes("WISMESH_TAG") || m.includes("RAK_TAG")) {
    return "WisMesh Tag";
  }
  if (m.includes("RAK4631") || m.includes("RAK_4631")) {
    return "RAK4631";
  }
  if (m.includes("ECHO")) {
    return "T-Echo";
  }
  return raw;
}

/**
 * SVG do rádio exibido na sidebar (cards da lista e inspector).
 * O ícone de barco (/devices/boat.svg) é de uso exclusivo do mapa; a sidebar
 * apresenta a placa de hardware (Heltec, T-Beam, T1000-E, RAK, etc.).
 */
export function nodeSidebarSvgUrl(node: NodeInfo): string {
  const inferred = node.hwModel || inferHardwareFromName(node.nome);
  return deviceModelSvgUrl(inferred);
}
