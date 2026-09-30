import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import {
  getLatestGeoJson,
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
  if (qIndex === -1) return {};
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
      if (done) break;
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
