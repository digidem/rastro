import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import { getAlerts } from "./src/fixtures/alertsFixture.js";
import {
  type MockEventKind,
  getLatestGeoJson,
  getNodeEvents,
  getTelemetryGeoJson,
  getTrackGeoJson,
} from "./src/fixtures/nodesFixture.js";

const TRANSPARENT_1X1_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
  "base64",
);

const tileCache = new Map<string, Buffer>();

const props = (...kv: [string, unknown][]): Record<string, unknown> =>
  Object.fromEntries(kv);

function sendJson(
  res: ServerResponse,
  statusCode: number,
  data: unknown,
): void {
  const json = JSON.stringify(data);
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(json),
    "Cache-Control": "no-store",
  });
  res.end(json);
}

function sendNoContent(res: ServerResponse, cookieHeader?: string): void {
  const headers: Record<string, string> = {
    "Cache-Control": "no-store",
  };
  if (cookieHeader) {
    headers["Set-Cookie"] = cookieHeader;
  }
  res.writeHead(204, headers);
  res.end();
}

const MAX_TILE_SIZE = 512 * 1024;
const FETCH_TIMEOUT_MS = 5000;

function safeDecode(encoded: string): string | null {
  try {
    return decodeURIComponent(encoded);
  } catch {
    return null;
  }
}

function parseQueryParams(rawUrl: string): {
  from?: string | null;
  to?: string | null;
  limit?: number | null;
} {
  const qIndex = rawUrl.indexOf("?");
  if (qIndex === -1) {
    return {};
  }
  try {
    const params = new URLSearchParams(rawUrl.slice(qIndex + 1));
    const from = params.get("from");
    const to = params.get("to");
    const limitRaw = params.get("limit");
    let limit: number | null = null;
    if (limitRaw !== null) {
      const parsed = Number.parseInt(limitRaw, 10);
      if (!Number.isNaN(parsed) && parsed >= 1) {
        limit = parsed;
      }
    }
    return { from, to, limit };
  } catch {
    return {};
  }
}

async function readBoundedBody(
  response: Response,
  maxBytes: number,
): Promise<Buffer> {
  const contentLength = response.headers.get("content-length");
  if (contentLength && Number.parseInt(contentLength, 10) > maxBytes) {
    throw new Error("Tile upstream excede tamanho máximo");
  }

  if (!response.body) {
    return Buffer.alloc(0);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      if (value) {
        totalBytes += value.byteLength;
        if (totalBytes > maxBytes) {
          await reader.cancel("Response body exceeded maxBytes");
          throw new Error("Tile upstream excede tamanho máximo");
        }
        chunks.push(value);
      }
    }
  } finally {
    reader.releaseLock();
  }

  return Buffer.concat(chunks);
}

async function proxyOsmTile(
  zStr: string,
  xStr: string,
  yStr: string,
  res: ServerResponse,
): Promise<void> {
  const z = Number.parseInt(zStr, 10);
  const x = Number.parseInt(xStr, 10);
  const y = Number.parseInt(yStr, 10);

  // Validação estrita de zoom e coordenadas do padrão Slippy Map:
  // z em [0, 19], e x, y em [0, 2^z - 1]
  if (
    Number.isNaN(z) ||
    Number.isNaN(x) ||
    Number.isNaN(y) ||
    z < 0 ||
    z > 19 ||
    x < 0 ||
    y < 0
  ) {
    sendJson(res, 400, { detail: "Coordenadas de tile OSM inválidas" });
    return;
  }

  const maxCoord = 2 ** z;
  if (x >= maxCoord || y >= maxCoord) {
    sendJson(res, 400, {
      detail: "Coordenadas de tile fora dos limites para o zoom",
    });
    return;
  }

  const key = `${z}/${x}/${y}`;
  const cached = tileCache.get(key);
  if (cached) {
    res.writeHead(200, {
      "Content-Type": "image/png",
      "Content-Length": cached.length,
      "Cache-Control": "public, max-age=86400",
    });
    res.end(cached);
    return;
  }

  try {
    const osmUrl = `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
    const upstream = await fetch(osmUrl, {
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      headers: {
        "User-Agent":
          "RastroViewerDevMock/1.0 (local dev; contact: dev@digidem.org)",
      },
    });

    if (upstream.ok) {
      const buffer = await readBoundedBody(upstream, MAX_TILE_SIZE);
      if (tileCache.size > 2000) {
        tileCache.clear();
      }
      tileCache.set(key, buffer);
      res.writeHead(200, {
        "Content-Type": "image/png",
        "Content-Length": buffer.length,
        "Cache-Control": "public, max-age=86400",
      });
      res.end(buffer);
      return;
    }
  } catch {
    // Modo offline ou falha no upstream: entrega fallback transparente 1x1
  }

  res.writeHead(200, {
    "Content-Type": "image/png",
    "Content-Length": TRANSPARENT_1X1_PNG.length,
    "Cache-Control": "public, max-age=3600",
  });
  res.end(TRANSPARENT_1X1_PNG);
}

const TRACK_RE = /^\/api\/nodes\/([^/]+)\/track(?:\?.*)?$/;
const TELEM_RE = /^\/api\/nodes\/([^/]+)\/telemetry(?:\?.*)?$/;
const EVENTS_RE = /^\/api\/nodes\/([^/]+)\/events(?:\?.*)?$/;
const OSM_RE = /^\/api\/osm\/(\d+)\/(\d+)\/(\d+)\.png(?:\?.*)?$/;

function handleAuthRoute(
  pathname: string,
  method: string,
  res: ServerResponse,
): boolean {
  const isGetOrHead = method === "GET" || method === "HEAD";

  if (pathname === "/api/healthz" && isGetOrHead) {
    sendJson(
      res,
      200,
      props(["status", "ok"], ["db_latency_ms", 0.9], ["mock", true]),
    );
    return true;
  }

  if (pathname === "/api/overlays" && isGetOrHead) {
    // Coordenadas fictícias (dado territorial real nunca vai para fixtures).
    const ponto = (nome: string, lon: number, lat: number) => ({
      type: "Feature",
      properties: { ALDEIA: nome },
      geometry: { type: "Point", coordinates: [lon, lat] },
    });
    sendJson(res, 200, {
      layers: [
        {
          name: "aldeias_teste",
          data: {
            type: "FeatureCollection",
            features: [
              ponto("ALDEIA TESTE 1", -60.0, -3.0),
              ponto("ALDEIA TESTE 2", -60.1, -3.1),
            ],
          },
        },
        {
          name: "contorno_teste",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                properties: { NOME: "AREA TESTE" },
                geometry: {
                  type: "Polygon",
                  coordinates: [
                    [
                      [-60.3, -3.3],
                      [-59.8, -3.3],
                      [-59.8, -2.8],
                      [-60.3, -2.8],
                      [-60.3, -3.3],
                    ],
                  ],
                },
              },
            ],
          },
        },
      ],
    });
    return true;
  }

  if (pathname === "/api/auth/estado" && isGetOrHead) {
    sendJson(
      res,
      200,
      props(
        ["exigida", true],
        ["autenticado", true],
        ["dias_lembrar", 30],
        ["mock", true],
      ),
    );
    return true;
  }

  if (pathname === "/api/auth/sessao" && method === "POST") {
    sendNoContent(
      res,
      "rastro_sessao=mock-dev-token; Path=/api; HttpOnly; SameSite=Strict",
    );
    return true;
  }

  if (pathname === "/api/auth/sessao" && method === "DELETE") {
    sendNoContent(
      res,
      "rastro_sessao=; Path=/api; Max-Age=0; HttpOnly; SameSite=Strict",
    );
    return true;
  }

  return false;
}

const OUTBOX_RE = /^\/api\/chat\/outbox\/(\d+)(?:\?.*)?$/;

interface MockChatBoat {
  boat_id: string;
  boat: string;
  gateway_id: string;
  virtual_node_num: number;
  active: boolean;
}

const MOCK_CHAT_BOATS: MockChatBoat[] = [
  {
    boat_id: "Barco 1",
    boat: "Barco 1",
    gateway_id: "!a35ae5d0",
    virtual_node_num: 1,
    active: true,
  },
  {
    boat_id: "Barco 2",
    boat: "Barco 2",
    gateway_id: "!6fe2ba80",
    virtual_node_num: 2,
    active: true,
  },
  {
    boat_id: "Barco 3",
    boat: "Barco 3",
    gateway_id: "!5b8fa170",
    virtual_node_num: 3,
    active: true,
  },
  {
    boat_id: "Barco 4",
    boat: "Barco 4",
    gateway_id: "!c14de890",
    virtual_node_num: 4,
    active: true,
  },
  {
    boat_id: "Barco 5",
    boat: "Barco 5",
    gateway_id: "!e27ba910",
    virtual_node_num: 5,
    active: true,
  },
  {
    boat_id: "Barco 6",
    boat: "Barco 6",
    gateway_id: "!d83cc240",
    virtual_node_num: 6,
    active: true,
  },
];

interface MockChatMessage {
  id: number;
  direction: "in" | "out";
  boat_id: string;
  from_num: number | null;
  text: string;
  is_alert: boolean;
  observed_at: string;
  received_at: string;
}

interface MockOutboxRecord {
  id: number;
  boat_id: string;
  text: string;
  status: "queued" | "sent" | "expired" | "failed";
  created_by: string;
  created_at: string;
  expires_at: string;
  sent_at: string | null;
  error: string | null;
}

const OUTBOX_STATUS_LABELS: Record<string, string> = {
  queued: "na fila",
  sent: "enviado ao gateway do barco",
  expired: "expirada (não entregue)",
  failed: "falhou",
};

let nextOutboxId = 1;
let nextMessageId = 10;
const mockOutbox = new Map<number, MockOutboxRecord>();

const mockChatMessages: MockChatMessage[] = [
  {
    id: 1,
    direction: "in",
    boat_id: "Barco 1",
    from_num: 1001,
    text: "Barco 1 em trânsito no Rio Curuçá, navegando normalmente.",
    is_alert: false,
    observed_at: new Date(Date.now() - 40 * 60 * 1000).toISOString(),
    received_at: new Date(Date.now() - 40 * 60 * 1000).toISOString(),
  },
  {
    id: 2,
    direction: "in",
    boat_id: "Barco 2",
    from_num: 1002,
    text: "ALERTA: Motor com superaquecimento acima do normal, parando para vistoria!",
    is_alert: true,
    observed_at: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
    received_at: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
  },
  {
    id: 3,
    direction: "out",
    boat_id: "Barco 2",
    from_num: null,
    text: "Base ciente. Equipe técnica monitorando telemetria.",
    is_alert: false,
    observed_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
    received_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
  },
  {
    id: 4,
    direction: "in",
    boat_id: "Barco 3",
    from_num: 1003,
    text: "Chegamos ao posto avançado de vigilância territorial.",
    is_alert: false,
    observed_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    received_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
  },
];

async function readJsonBody(
  req: IncomingMessage,
): Promise<Record<string, unknown>> {
  // biome-ignore lint/suspicious/noExplicitAny: suporte a middlewares com body pré-processado
  const anyReq = req as any;
  if (anyReq.body && typeof anyReq.body === "object") {
    return anyReq.body;
  }
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 64 * 1024) {
        req.destroy();
        reject(new Error("Corpo da requisição excede limite"));
      }
    });
    req.on("end", () => {
      if (!raw.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve({});
      }
    });
    req.on("error", reject);
  });
}

function handleChatRoute(
  rawUrl: string,
  pathname: string,
  method: string,
  req: IncomingMessage,
  res: ServerResponse,
): boolean {
  const isGetOrHead = method === "GET" || method === "HEAD";

  if (pathname === "/api/chat/boats" && isGetOrHead) {
    sendJson(res, 200, MOCK_CHAT_BOATS);
    return true;
  }

  if (pathname === "/api/chat/messages" && isGetOrHead) {
    const qIndex = rawUrl.indexOf("?");
    const params =
      qIndex !== -1
        ? new URLSearchParams(rawUrl.slice(qIndex + 1))
        : new URLSearchParams();
    const since = params.get("since");

    if (since) {
      const sinceMs = Date.parse(since.trim().replace("Z", "+00:00"));
      if (Number.isNaN(sinceMs)) {
        sendJson(res, 400, {
          detail: "parâmetro 'since' inválido: use data ISO 8601",
        });
        return true;
      }
      const filtered = mockChatMessages.filter((m) => {
        const ts = Date.parse(
          (m.received_at || m.observed_at).replace("Z", "+00:00"),
        );
        return !Number.isNaN(ts) && ts > sinceMs;
      });
      sendJson(res, 200, filtered);
      return true;
    }

    sendJson(res, 200, mockChatMessages);
    return true;
  }

  if (pathname === "/api/chat/send" && method === "POST") {
    void (async () => {
      try {
        const body = await readJsonBody(req);
        const boatId = (
          typeof body.boat === "string"
            ? body.boat
            : typeof body.boat_id === "string"
              ? body.boat_id
              : ""
        ).trim();

        if (!boatId) {
          sendJson(res, 404, { detail: "barco não informado ou inexistente" });
          return;
        }

        const activeBoat = MOCK_CHAT_BOATS.find(
          (b) => b.boat_id === boatId || b.boat === boatId,
        );
        if (!activeBoat || !activeBoat.active) {
          sendJson(res, 404, {
            detail: "barco não encontrado ou inativo na malha",
          });
          return;
        }

        const text = typeof body.text === "string" ? body.text : "";
        const byteLen = Buffer.byteLength(text, "utf-8");
        if (byteLen < 1 || byteLen > 200) {
          sendJson(res, 422, {
            detail: "o texto da mensagem deve conter entre 1 e 200 bytes UTF-8",
          });
          return;
        }

        const outboxId = nextOutboxId++;
        const nowIso = new Date().toISOString();
        const expiresAt = new Date(Date.now() + 900 * 1000).toISOString();

        const outboxRecord: MockOutboxRecord = {
          id: outboxId,
          boat_id: activeBoat.boat_id,
          text,
          status: "queued",
          created_by: "session",
          created_at: nowIso,
          expires_at: expiresAt,
          sent_at: null,
          error: null,
        };
        mockOutbox.set(outboxId, outboxRecord);

        mockChatMessages.push({
          id: nextMessageId++,
          direction: "out",
          boat_id: activeBoat.boat_id,
          from_num: null,
          text,
          is_alert: false,
          observed_at: nowIso,
          received_at: nowIso,
        });

        // Simula avanço de status para 'sent' no gateway após 2s
        setTimeout(() => {
          const item = mockOutbox.get(outboxId);
          if (item && item.status === "queued") {
            item.status = "sent";
            item.sent_at = new Date().toISOString();
          }
        }, 2000);

        sendJson(res, 200, {
          id: outboxId,
          status: "queued",
          expires_at: expiresAt,
        });
      } catch {
        sendJson(res, 400, { detail: "Corpo JSON inválido" });
      }
    })();
    return true;
  }

  const outboxMatch = OUTBOX_RE.exec(rawUrl);
  if (outboxMatch && isGetOrHead) {
    const outboxId = Number.parseInt(outboxMatch[1], 10);
    const item = mockOutbox.get(outboxId);
    if (!item) {
      sendJson(res, 404, { detail: "mensagem não encontrada na fila" });
      return true;
    }

    const label = OUTBOX_STATUS_LABELS[item.status] || item.status;
    sendJson(res, 200, {
      id: item.id,
      boat_id: item.boat_id,
      status: item.status,
      label,
      status_label: label,
      created_by: item.created_by,
      created_at: item.created_at,
      expires_at: item.expires_at,
      sent_at: item.sent_at,
      error: item.error,
    });
    return true;
  }

  return false;
}

function handleNodeRoute(
  rawUrl: string,
  pathname: string,
  method: string,
  res: ServerResponse,
): boolean {
  const isGetOrHead = method === "GET" || method === "HEAD";

  if (pathname === "/api/nodes/latest" && isGetOrHead) {
    sendJson(res, 200, getLatestGeoJson());
    return true;
  }

  if (pathname === "/api/alerts" && isGetOrHead) {
    sendJson(res, 200, getAlerts());
    return true;
  }

  const trackMatch = TRACK_RE.exec(rawUrl);
  if (trackMatch && isGetOrHead) {
    const target = safeDecode(trackMatch[1]);
    if (target === null) {
      sendJson(res, 400, { detail: "Identificador de nó inválido" });
      return true;
    }
    const query = parseQueryParams(rawUrl);
    sendJson(res, 200, getTrackGeoJson(target, undefined, query));
    return true;
  }

  const eventsMatch = EVENTS_RE.exec(rawUrl);
  if (eventsMatch && isGetOrHead) {
    const target = safeDecode(eventsMatch[1]);
    if (target === null) {
      sendJson(res, 400, { detail: "Identificador de nó inválido" });
      return true;
    }
    const qs = new URLSearchParams(rawUrl.split("?")[1] ?? "");
    const kinds = (qs.get("kinds") ?? "")
      .split(",")
      .filter((k): k is MockEventKind => ["pos", "telem", "msg"].includes(k));
    const limit = Number.parseInt(qs.get("limit") ?? "", 10);
    sendJson(
      res,
      200,
      getNodeEvents(target, {
        kinds,
        before: qs.get("before"),
        limit: Number.isNaN(limit) ? null : limit,
      }),
    );
    return true;
  }

  const telemMatch = TELEM_RE.exec(rawUrl);
  if (telemMatch && isGetOrHead) {
    const target = safeDecode(telemMatch[1]);
    if (target === null) {
      sendJson(res, 400, { detail: "Identificador de nó inválido" });
      return true;
    }
    const query = parseQueryParams(rawUrl);
    sendJson(res, 200, getTelemetryGeoJson(target, undefined, query));
    return true;
  }

  return false;
}

function handleOsmRoute(
  rawUrl: string,
  method: string,
  res: ServerResponse,
): boolean {
  const isGetOrHead = method === "GET" || method === "HEAD";
  const osmMatch = OSM_RE.exec(rawUrl);
  if (osmMatch && isGetOrHead) {
    proxyOsmTile(osmMatch[1], osmMatch[2], osmMatch[3], res).catch(() => {
      // Ignora erro silenciosamente
    });
    return true;
  }
  return false;
}

/**
 * Plugin do Vite que provê uma API mock local completa para desenvolvimento
 * exclusivo da interface web (frontend map UI), sem precisar de PostgreSQL,
 * bridge MQTT nem backend FastAPI rodando.
 */
export function mockApiPlugin(): Plugin {
  return {
    name: "vite-plugin-rastro-mock-api",
    configureServer(server) {
      server.middlewares.use(
        (req: IncomingMessage, res: ServerResponse, next: () => void) => {
          const rawUrl = req.url ?? "";
          if (rawUrl.startsWith("/tiles/")) {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Tiles not found in mock mode");
            return;
          }
          if (!rawUrl.startsWith("/api/")) {
            return next();
          }

          const pathname = rawUrl.split("?")[0];
          const method = (req.method ?? "GET").toUpperCase();

          if (
            handleAuthRoute(pathname, method, res) ||
            handleChatRoute(rawUrl, pathname, method, req, res) ||
            handleNodeRoute(rawUrl, pathname, method, res) ||
            handleOsmRoute(rawUrl, method, res)
          ) {
            return;
          }

          sendJson(res, 404, {
            detail: `Mock endpoint não encontrado: ${pathname}`,
          });
        },
      );
    },
  };
}
