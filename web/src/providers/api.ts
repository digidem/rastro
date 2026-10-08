import { type Loc, credenciaisPermitidas } from "../lib/credenciais.js";
import type { NodeAlert, NodeInfo, NodeKind } from "../store.js";

/** Token ausente/rejeitado (HTTP 401) — a UI deve pedir o token de novo. */
export class ErrTokenInvalid extends Error {
  constructor() {
    super("401: token inválido");
    this.name = "ErrTokenInvalid";
  }
}

/** Rede indisponível (fetch falhou antes de responder). */
export class ErrOffline extends Error {
  constructor() {
    super("sem conexão com a API");
    this.name = "ErrOffline";
  }
}

export interface TrackPoint {
  pos: LngLat;
  posTime: string | null;
  sats: number | null;
}

export type NodeEventKind = "pos" | "telem" | "msg";

/** Registro do nó (GET /api/nodes/:n/events); campos crus da API em snake_case. */
export interface NodeEvent {
  kind: NodeEventKind;
  id: number;
  ts: string;
  received_at?: string | null;
  // pos
  lat?: number;
  lon?: number;
  altitude_m?: number | null;
  sats_in_view?: number | null;
  snr?: number | null;
  rssi?: number | null;
  hop_limit?: number | null;
  packet_id?: number | null;
  gateway_num?: number | null;
  gateway_name?: string | null;
  time_source?: string;
  time_flag?: string | null;
  // telem
  battery_level?: number | null;
  voltage?: number | null;
  channel_util?: number | null;
  air_util_tx?: number | null;
  uptime_s?: number | null;
  // msg
  direction?: "in" | "out";
  text?: string;
  is_alert?: boolean;
}

export interface NodeEventsPage {
  events: NodeEvent[];
  /** Cursor da página seguinte (mais antiga); null = fim do histórico. */
  nextCursor: string | null;
}

export interface NodeEventsQuery {
  kinds?: NodeEventKind[];
  before?: string | null;
  limit?: number;
}

/** Estado de auth (GET /api/auth/estado); campos normalizados do snake_case cru. */
export interface AuthEstado {
  exigida: boolean;
  autenticado: boolean;
  diasLembrar: number;
}

export interface ApiClient {
  latest(): Promise<NodeInfo[]>;
  /** Alertas ativos (GET /api/alerts); API sem a rota → []. */
  alerts(): Promise<NodeAlert[]>;
  track(node: string): Promise<{
    line: LngLat[] | null;
    lines: LngLat[][];
    points: TrackPoint[];
  }>;
  /** Registros do nó (posição, telemetria, mensagem), mais novos primeiro. */
  events(node: string, query?: NodeEventsQuery): Promise<NodeEventsPage>;
  ping(): Promise<boolean>;
  /** Estado de autenticação público (cookie/token ainda não enviado). */
  authEstado(): Promise<AuthEstado>;
  /**
   * Cria a sessão (cookie HttpOnly; o token nunca volta ao JS) ou falha:
   * 401 → ErrTokenInvalid, 409 → Error "Autenticação por token não
   * configurada no servidor", rede → ErrOffline.
   */
  login(token: string, lembrar: boolean): Promise<void>;
  /** Apaga a sessão (DELETE /api/auth/sessao). Propaga o erro — o chamador decide. */
  logout(): Promise<void>;
}

export interface ApiClientOptions {
  /** Base da API; "" = relativo (mesma origem). Em dev, VITE_API_BASE. */
  base?: string;
  /**
   * Token DEV (.env local) resolvido a cada chamada; undefined na produção
   * e no modo cookie — nesse caso o cookie HttpOnly autentica via same-origin.
   */
  getToken?: () => string | undefined;
  /**
   * Localização usada no veto a credencial em canal plano; default
   * window.location (injetável só para teste).
   */
  loc?: Loc;
}

// Formato cru da API (GeoJSON); campos em snake_case são lidos por índice.
interface ApiFeature {
  geometry?: { type?: unknown; coordinates?: unknown } | null;
  properties?: Record<string, unknown> | null;
}

interface ApiFc {
  features?: ApiFeature[] | null;
}

type LngLat = [number, number];

// Number.isFinite rejeita NaN/Infinity; typeof aceita NaN como "number".
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const lngLat = (v: unknown): LngLat | null => {
  if (!Array.isArray(v) || v.length < 2) {
    return null;
  }
  const lon = v[0];
  const lat = v[1];
  return typeof lon === "number" &&
    Number.isFinite(lon) &&
    typeof lat === "number" &&
    Number.isFinite(lat)
    ? [lon, lat]
    : null;
};

const linha = (v: unknown): LngLat[] | null => {
  if (!Array.isArray(v)) {
    return null;
  }
  const pts: LngLat[] = [];
  for (const c of v) {
    const p = lngLat(c);
    if (p !== null) {
      pts.push(p);
    }
  }
  return pts.length >= 2 ? pts : null;
};

// Categoria do contrato; qualquer outra coisa (ausente, número, typo) vira
// "unknown" — a UI nunca inventa categoria a partir do nome.
const asKind = (v: unknown): NodeKind => {
  switch (v) {
    case "boat":
    case "fixed_station":
    case "handheld":
      return v;
    default:
      return "unknown";
  }
};

const preencherMetadadosExtras = (
  node: NodeInfo,
  p: Record<string, unknown>,
) => {
  if (Object.hasOwn(p, "short_name")) {
    node.shortName = str(p.short_name);
  }
  if (Object.hasOwn(p, "kind")) {
    node.kind = asKind(p.kind);
  }
  if (Object.hasOwn(p, "hw_model")) {
    node.hwModel = str(p.hw_model);
  }
  if (Object.hasOwn(p, "altitude_m")) {
    node.altitudeM = num(p.altitude_m);
  }
  if (Object.hasOwn(p, "sats")) {
    node.sats = num(p.sats);
  }
  if (Object.hasOwn(p, "time_source")) {
    node.timeSource = str(p.time_source);
  }
  if (Object.hasOwn(p, "received_at")) {
    node.receivedAt = str(p.received_at);
  }
  if (Object.hasOwn(p, "bearing")) {
    node.bearing = num(p.bearing);
  }
  if (Object.hasOwn(p, "age_s")) {
    node.ageS = num(p.age_s);
    node.age_s = num(p.age_s);
  }
  if (Object.hasOwn(p, "time_flag")) {
    node.timeFlag = str(p.time_flag);
    node.time_flag = str(p.time_flag);
  }
};

/** Alerta cru da API (snake_case); campos inválidos → null no parse. */
const alertFromRaw = (o: Record<string, unknown>): NodeAlert | null => {
  const tipo = str(o.alert_type);
  if (tipo !== "gateway_mudo" && tipo !== "bateria_critica") {
    return null;
  }
  return {
    nodeNum: num(o.node_num),
    alertId: str(o.alert_id) ?? "",
    nodeId: str(o.node_id),
    nodeNome: str(o.node_name),
    alertType: tipo,
    severity: str(o.severity) ?? "medium",
    triggeredAt: str(o.triggered_at) ?? "",
    details:
      typeof o.details === "object" && o.details !== null
        ? (o.details as Record<string, unknown>)
        : null,
  };
};

const nodeFromFeature = (f: ApiFeature): NodeInfo | null => {
  if (f.geometry?.type !== "Point") {
    return null;
  }
  const pos = lngLat(f.geometry.coordinates);
  if (pos === null) {
    return null;
  }
  const p = f.properties ?? {};
  const nodeNum = num(p.node_num);
  if (nodeNum === null) {
    return null;
  }
  const node: NodeInfo = {
    nodeNum,
    nodeId: str(p.node_id) ?? "",
    nome: str(p.nome) ?? `nó ${nodeNum}`,
    posTime: str(p.pos_time),
    battery: num(p.battery),
    lon: pos[0],
    lat: pos[1],
  };
  preencherMetadadosExtras(node, p);
  return node;
};

const adicionarSegmento = (
  acc: { line: LngLat[] | null; lines: LngLat[][] },
  coords: unknown,
) => {
  const l = linha(coords);
  if (l !== null) {
    if (acc.line === null) {
      acc.line = l;
    }
    acc.lines.push(l);
  }
};

const trackFromFeature = (
  f: ApiFeature,
  acc: { line: LngLat[] | null; lines: LngLat[][]; points: TrackPoint[] },
): void => {
  const g = f.geometry;
  if (g?.type === "LineString") {
    adicionarSegmento(acc, g.coordinates);
  } else if (g?.type === "MultiLineString") {
    if (Array.isArray(g.coordinates)) {
      for (const seg of g.coordinates) {
        adicionarSegmento(acc, seg);
      }
    }
  } else if (g?.type === "Point") {
    const pos = lngLat(g.coordinates);
    if (pos !== null) {
      const p = f.properties ?? {};
      acc.points.push({
        pos,
        posTime: str(p.pos_time),
        sats: num(p.sats),
      });
    }
  }
};

/**
 * Cliente da API FastAPI do viewer (GeoJSON). Sem logs de coordenadas:
 * erros carregam só status/texto curto. Autenticação: cookie HttpOnly
 * (same-origin, o JS nunca o lê) ou Bearer DEV via getToken — os DOIS vetados
 * em canal plano (http: fora de loopback), onde nenhuma credencial viaja.
 */
export function createApiClient(opts: ApiClientOptions = {}): ApiClient {
  const base = opts.base ?? "";

  // Sem TLS e fora do loopback nada de credencial: nem o Bearer de dev, nem o
  // cookie de sessão (credentials "omit"). /api/healthz (público) nunca manda
  // credencial em canal nenhum.
  const credencialOk = credenciaisPermitidas(opts.loc);

  // fetch que engole falha de rede e traduz 401; nunca loga corpo/coords/token.
  // credentials "same-origin" explícito: o cookie da sessão viaja em toda
  // chamada de API (mesma origem); "omit" no caso de canal plano acima.
  const request = async (
    path: string,
    token: string | undefined,
    method = "GET",
    body?: string,
  ): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (token !== undefined && credencialOk) {
      headers.Authorization = `Bearer ${token}`;
    }
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    let res: Response;
    try {
      res = await fetch(base + path, {
        method,
        headers,
        body,
        credentials: credencialOk ? "same-origin" : "omit",
      });
    } catch {
      throw new ErrOffline();
    }
    if (res.status === 401) {
      throw new ErrTokenInvalid();
    }
    return res;
  };

  // fetch + 401 + status; devolve o corpo GeoJSON parseado.
  const pedir = async (
    path: string,
    token: string | undefined,
  ): Promise<ApiFc> => {
    const res = await request(path, token);
    if (!res.ok) {
      throw new Error(`${path}: ${res.status}`);
    }
    return (await res.json()) as ApiFc;
  };

  return {
    async latest() {
      const fc = await pedir("/api/nodes/latest", opts.getToken?.());
      const nodes: NodeInfo[] = [];
      for (const f of fc.features ?? []) {
        const n = nodeFromFeature(f);
        if (n !== null) {
          nodes.push(n);
        }
      }
      return nodes;
    },

    async alerts() {
      const res = await request("/api/alerts", opts.getToken?.());
      if (res.status === 404) {
        // API antiga sem a rota: zero alertas (badge some no próximo tick).
        return [];
      }
      if (!res.ok) {
        throw new Error(`/api/alerts: ${res.status}`);
      }
      const body = (await res.json().catch(() => null)) as unknown;
      if (!Array.isArray(body)) {
        // Corpo malformado num 200: falha (o polling mantém os alertas
        // anteriores) em vez de [] que limparia badges válidos.
        throw new Error("/api/alerts: corpo inesperado");
      }
      const out: NodeAlert[] = [];
      for (const item of body) {
        if (typeof item === "object" && item !== null) {
          const a = alertFromRaw(item as Record<string, unknown>);
          if (a !== null) {
            out.push(a);
          }
        }
      }
      return out;
    },

    async track(node) {
      const fc = await pedir(
        `/api/nodes/${encodeURIComponent(node)}/track`,
        opts.getToken?.(),
      );
      const acc: {
        line: LngLat[] | null;
        lines: LngLat[][];
        points: TrackPoint[];
      } = {
        line: null,
        lines: [],
        points: [],
      };
      for (const f of fc.features ?? []) {
        trackFromFeature(f, acc);
      }
      return acc;
    },

    async events(node, query = {}) {
      const qs = new URLSearchParams();
      if (query.kinds !== undefined && query.kinds.length > 0) {
        qs.set("kinds", query.kinds.join(","));
      }
      if (query.before) {
        qs.set("before", query.before);
      }
      qs.set("limit", String(query.limit ?? 50));
      const path = `/api/nodes/${encodeURIComponent(node)}/events?${qs}`;
      const res = await request(path, opts.getToken?.());
      if (!res.ok) {
        throw new Error(`${path}: ${res.status}`);
      }
      const body = (await res.json()) as {
        events?: NodeEvent[];
        next_cursor?: string | null;
      };
      return {
        events: body.events ?? [],
        nextCursor: body.next_cursor ?? null,
      };
    },

    async ping() {
      try {
        // healthz não exige auth: nunca manda credencial (nem cookie), em
        // qualquer canal — só mede conexão.
        const res = await fetch(`${base}/api/healthz`, {
          credentials: "omit",
        });
        return res.ok;
      } catch {
        return false;
      }
    },

    async authEstado() {
      const res = await request("/api/auth/estado", undefined);
      if (res.status === 404) {
        // Backend legado ou contêiner de desenvolvimento sem /api/auth/estado:
        // Se houver devToken configurado ou se a rota não existir, não bloqueia com erro.
        const temToken = !!opts.getToken?.();
        return {
          exigida: !temToken,
          autenticado: temToken,
          diasLembrar: 30,
        };
      }
      if (!res.ok) {
        throw new Error(`/api/auth/estado: ${res.status}`);
      }
      // Tipagem leve + leitura por índice (snake_case cru da API): campos
      // inválidos caem nos defaults (exigido/sessão/expira 30 d).
      const corpo = (await res.json().catch(() => ({}))) as unknown;
      const o = (corpo ?? {}) as Record<string, unknown>;
      return {
        exigida: typeof o.exigida === "boolean" ? o.exigida : true,
        autenticado: typeof o.autenticado === "boolean" ? o.autenticado : false,
        // biome-ignore lint/complexity/useLiteralKeys: chave snake_case do contrato cru da API
        diasLembrar: num(o["dias_lembrar"]) ?? 30,
      };
    },

    async login(token, lembrar) {
      const res = await request(
        "/api/auth/sessao",
        token,
        "POST",
        JSON.stringify({ lembrar }),
      );
      if (res.status === 409) {
        throw new Error("Autenticação por token não configurada no servidor");
      }
      if (!res.ok) {
        throw new Error(`/api/auth/sessao: ${res.status}`);
      }
      // 204: o cookie HttpOnly já está no jar do browser; nada a ler.
    },

    async logout() {
      const res = await request("/api/auth/sessao", undefined, "DELETE");
      if (!res.ok) {
        throw new Error(`/api/auth/sessao: ${res.status}`);
      }
    },
  };
}
