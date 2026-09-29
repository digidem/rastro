import type { NodeInfo } from "../store.js";

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

/** Estado de auth (GET /api/auth/estado); campos normalizados do snake_case cru. */
export interface AuthEstado {
  exigida: boolean;
  autenticado: boolean;
  diasLembrar: number;
}

export interface ApiClient {
  latest(): Promise<NodeInfo[]>;
  track(node: string): Promise<{
    line: LngLat[] | null;
    points: TrackPoint[];
  }>;
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
  return {
    nodeNum,
    nodeId: str(p.node_id) ?? "",
    nome: str(p.nome) ?? `nó ${nodeNum}`,
    posTime: str(p.pos_time),
    battery: num(p.battery),
    lon: pos[0],
    lat: pos[1],
  };
};

const trackFromFeature = (
  f: ApiFeature,
  acc: { line: LngLat[] | null; points: TrackPoint[] },
): void => {
  const g = f.geometry;
  if (g?.type === "LineString") {
    if (acc.line === null) {
      acc.line = linha(g.coordinates);
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
 * (same-origin, o JS nunca o lê) ou Bearer DEV via getToken.
 */
export function createApiClient(opts: ApiClientOptions = {}): ApiClient {
  const base = opts.base ?? "";

  // fetch que engole falha de rede e traduz 401; nunca loga corpo/coords/token.
  // credentials "same-origin" explícito: o cookie da sessão viaja em toda
  // chamada de API (mesma origem).
  const request = async (
    path: string,
    token: string | undefined,
    method = "GET",
    body?: string,
  ): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (token !== undefined) {
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
        credentials: "same-origin",
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

    async track(node) {
      const fc = await pedir(
        `/api/nodes/${encodeURIComponent(node)}/track`,
        opts.getToken?.(),
      );
      const acc: { line: LngLat[] | null; points: TrackPoint[] } = {
        line: null,
        points: [],
      };
      for (const f of fc.features ?? []) {
        trackFromFeature(f, acc);
      }
      return acc;
    },

    async ping() {
      try {
        // healthz não exige auth; indicador de conexão.
        const res = await fetch(`${base}/api/healthz`, {
          credentials: "same-origin",
        });
        return res.ok;
      } catch {
        return false;
      }
    },

    async authEstado() {
      const res = await request("/api/auth/estado", undefined);
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
