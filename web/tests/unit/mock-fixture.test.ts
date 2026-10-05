import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it, vi } from "vitest";
import {
  FLEET_NODES,
  findMockNode,
  getLatestGeoJson,
  getMockNodeInfoList,
  getTelemetryGeoJson,
  getTrackGeoJson,
  relativeIso,
} from "../../src/fixtures/nodesFixture.js";
import { mockApiPlugin } from "../../vite-plugin-mock-api.js";

describe("FLEET_NODES fixture", () => {
  it("contém todos os dispositivos da frota original do univaja-lora", () => {
    const ids = FLEET_NODES.map((d) => d.id);
    expect(ids).toContain("heltec-v4-itq1");
    expect(ids).toContain("cartao-2");
    expect(ids).toContain("heltec-v4-ccb1");
    expect(ids).toContain("heltec-v4-itb1");
    expect(ids).toContain("tbeam-esc1");
    expect(ids).toContain("tracker-t1000-e-ctn1");
    expect(ids).toContain("heltec-v4-mjb1");
    expect(ids).toContain("cartao-3");
    expect(ids).toContain("heltec-v4-jqb1");
  });

  it("todas as coordenadas estão estritamente dentro da bacia do Vale do Javari", () => {
    const minLon = -74.2;
    const maxLon = -70.0;
    const minLat = -7.6;
    const maxLat = -4.0;

    for (const d of FLEET_NODES) {
      expect(d.lon).toBeGreaterThanOrEqual(minLon);
      expect(d.lon).toBeLessThanOrEqual(maxLon);
      expect(d.lat).toBeGreaterThanOrEqual(minLat);
      expect(d.lat).toBeLessThanOrEqual(maxLat);

      for (const p of d.trackPoints) {
        expect(p.lon).toBeGreaterThanOrEqual(minLon);
        expect(p.lon).toBeLessThanOrEqual(maxLon);
        expect(p.lat).toBeGreaterThanOrEqual(minLat);
        expect(p.lat).toBeLessThanOrEqual(maxLat);
      }
    }
  });

  it("converte para NodeInfo[] com suporte a timestamps dinâmicos", () => {
    const fixedNow = new Date("2026-09-29T21:00:00Z");
    const list = getMockNodeInfoList(fixedNow);
    expect(list.length).toBe(FLEET_NODES.length);

    const itq1 = list.find((n) => n.nodeId === "!a35ae5d0");
    expect(itq1).toBeDefined();
    expect(itq1?.nome).toBe("univaja-itaquai-barco-1");
    expect(itq1?.posTime).toBe("2026-09-29T20:58:00.000Z");
    expect(itq1?.battery).toBe(88);

    const axm4 = list.find((n) => n.nodeId === "!433df260");
    expect(axm4).toBeDefined();
    expect(axm4?.posTime).toBeNull();
    expect(axm4?.battery).toBeNull();
  });

  it("findMockNode encontra por nodeId, hex, número decimal e nome", () => {
    expect(findMockNode("!a35ae5d0")?.id).toBe("heltec-v4-itq1");
    expect(findMockNode("a35ae5d0")?.id).toBe("heltec-v4-itq1");
    expect(findMockNode(2740643280)?.id).toBe("heltec-v4-itq1");
    expect(findMockNode("itq1")?.id).toBe("heltec-v4-itq1");
    expect(findMockNode("univaja-itaquai-barco-1")?.id).toBe("heltec-v4-itq1");
    expect(findMockNode("inexistente")).toBeUndefined();
  });

  it("getLatestGeoJson gera FeatureCollection com schema válido da API", () => {
    const fc = getLatestGeoJson(new Date("2026-09-29T21:00:00Z"));
    expect(fc.type).toBe("FeatureCollection");
    expect(fc.features.length).toBe(FLEET_NODES.length);

    const itq1 = fc.features.find((f) => f.properties.node_id === "!a35ae5d0");
    expect(itq1).toBeDefined();
    expect(itq1?.geometry.type).toBe("Point");
    expect(itq1?.geometry.coordinates).toEqual([-70.312, -4.512]);
    expect(itq1?.properties.node_num).toBe(2740643280);
    expect(itq1?.properties.battery).toBe(88);
  });

  it("getLatestGeoJson serializa os metadados que a UI usa (kind, short_name, hw_model)", () => {
    const fc = getLatestGeoJson(new Date("2026-09-29T21:00:00Z"));
    const itq1 = fc.features.find((f) => f.properties.node_id === "!a35ae5d0");

    expect(itq1?.properties.short_name).toBe("itq1");
    expect(itq1?.properties.kind).toBe("boat");
    expect(itq1?.properties.hw_model).toBe("HELTEC_V4");
    expect(itq1?.properties.altitude_m).toBe(82);
    expect(itq1?.properties.sats).toBe(8);
    expect(typeof itq1?.properties.bearing).toBe("number");
    expect(itq1?.properties.bearing).toBeGreaterThan(0);
    expect(itq1?.properties.bearing).toBeLessThan(360);
  });

  it("getTrackGeoJson gera LineString e Points ordenados cronologicamente", () => {
    const fixedNow = new Date("2026-09-29T21:00:00Z");
    const track = getTrackGeoJson("!a35ae5d0", fixedNow);
    expect(track.type).toBe("FeatureCollection");
    expect(track.features.length).toBe(6); // 1 LineString + 5 Points

    const line = track.features[0];
    expect(line.geometry.type).toBe("LineString");
    if (line.geometry.type === "LineString") {
      expect(line.geometry.coordinates.length).toBe(5);
      expect(line.geometry.coordinates[0]).toEqual([-70.198, -4.382]);
      expect(line.geometry.coordinates[4]).toEqual([-70.312, -4.512]);
    }

    const points = track.features.slice(1);
    for (const p of points) {
      expect(p.geometry.type).toBe("Point");
    }

    expect(getTrackGeoJson("nao-existe").features).toEqual([]);
  });

  it("getTelemetryGeoJson gera Feature sem geometria com métricas", () => {
    const telem = getTelemetryGeoJson("!a35ae5d0");
    expect(telem.type).toBe("FeatureCollection");
    expect(telem.features.length).toBe(1);
    expect(telem.features[0].geometry).toBeNull();
    expect(telem.features[0].properties.battery_level).toBe(88);
    expect(telem.features[0].properties.voltage).toBe(4.12);

    expect(getTelemetryGeoJson("nao-existe").features).toEqual([]);
  });

  it("relativeIso trata valores nulos e calcula UTC correto", () => {
    expect(relativeIso(null)).toBeNull();
    const now = new Date("2026-09-29T20:00:00Z");
    expect(relativeIso(10, now)).toBe("2026-09-29T19:50:00.000Z");
  });
});

describe("mockApiPlugin", () => {
  it("instala middleware que responde rotas da API", async () => {
    const plugin = mockApiPlugin();
    expect(plugin.name).toBe("vite-plugin-rastro-mock-api");

    let middlewareFn:
      | ((req: IncomingMessage, res: ServerResponse, next: () => void) => void)
      | undefined;
    const fakeServer = {
      middlewares: {
        use(
          fn: (
            req: IncomingMessage,
            res: ServerResponse,
            next: () => void,
          ) => void,
        ) {
          middlewareFn = fn;
        },
      },
    };

    // biome-ignore lint/suspicious/noExplicitAny: mock de configureServer
    (plugin.configureServer as any)(fakeServer);
    expect(middlewareFn).toBeDefined();

    const simular = (url: string, method = "GET", bodyPayload?: unknown) => {
      let statusCode = 0;
      let headers: Record<string, unknown> = {};
      let body = "";

      // biome-ignore lint/suspicious/noExplicitAny: mock de IncomingMessage
      const req = { url, method, body: bodyPayload } as any;
      const res = {
        writeHead(code: number, h: Record<string, unknown>) {
          statusCode = code;
          headers = h;
        },
        end(data?: string | Buffer) {
          if (typeof data === "string") {
            body = data;
          } else if (Buffer.isBuffer(data)) {
            body = data.toString("utf-8");
          }
        },
      } as unknown as ServerResponse;

      const next = vi.fn();
      middlewareFn?.(req, res, next);
      return {
        get statusCode() {
          return statusCode;
        },
        get headers() {
          return headers;
        },
        get body() {
          return body;
        },
        next,
      };
    };

    // 1. Rota que não é /api passa adiante
    const r1 = simular("/index.html");
    expect(r1.next).toHaveBeenCalled();

    // 2. /api/healthz responde 200
    const r2 = simular("/api/healthz");
    expect(r2.statusCode).toBe(200);
    expect(JSON.parse(r2.body).status).toBe("ok");

    // 3. /api/auth/estado responde 200
    const r3 = simular("/api/auth/estado");
    expect(r3.statusCode).toBe(200);
    expect(JSON.parse(r3.body).autenticado).toBe(true);

    // 4. /api/auth/sessao (POST) responde 204
    const r4 = simular("/api/auth/sessao", "POST");
    expect(r4.statusCode).toBe(204);

    // 5. /api/auth/sessao (DELETE) responde 204
    const r5 = simular("/api/auth/sessao", "DELETE");
    expect(r5.statusCode).toBe(204);

    // 6. /api/nodes/latest responde GeoJSON
    const r6 = simular("/api/nodes/latest");
    expect(r6.statusCode).toBe(200);
    const fc = JSON.parse(r6.body);
    expect(fc.type).toBe("FeatureCollection");
    expect(fc.features.length).toBe(FLEET_NODES.length);

    // 7. /api/nodes/:node/track responde GeoJSON
    const r7 = simular("/api/nodes/!a35ae5d0/track");
    expect(r7.statusCode).toBe(200);
    expect(JSON.parse(r7.body).type).toBe("FeatureCollection");

    // 8. /api/nodes/:node/track respeita ?limit=1
    const r8 = simular("/api/nodes/!a35ae5d0/track?limit=1");
    expect(r8.statusCode).toBe(200);
    const fcLimit = JSON.parse(r8.body);
    expect(fcLimit.features.length).toBe(2); // 1 line + 1 point
    expect(fcLimit.features[0].geometry.coordinates.length).toBe(1);

    // 9. /api/nodes/:node/track com percent-encoding malformado retorna 400 JSON
    const r9 = simular("/api/nodes/%ZZ/track");
    expect(r9.statusCode).toBe(400);
    expect(JSON.parse(r9.body).detail).toBe("Identificador de nó inválido");

    // 10. /api/nodes/:node/telemetry com percent-encoding malformado retorna 400 JSON
    const r10 = simular("/api/nodes/%ZZ/telemetry");
    expect(r10.statusCode).toBe(400);
    expect(JSON.parse(r10.body).detail).toBe("Identificador de nó inválido");

    // 11. /api/osm valida coordenadas de zoom e limites
    const r11a = simular("/api/osm/999/999999999/999999999.png");
    expect(r11a.statusCode).toBe(400);
    expect(JSON.parse(r11a.body).detail).toBe(
      "Coordenadas de tile OSM inválidas",
    );

    const r11b = simular("/api/osm/0/5/5.png");
    expect(r11b.statusCode).toBe(400);
    expect(JSON.parse(r11b.body).detail).toBe(
      "Coordenadas de tile fora dos limites para o zoom",
    );

    // 12. /api/chat/boats lista barcos da frota
    const rChatBoats = simular("/api/chat/boats");
    expect(rChatBoats.statusCode).toBe(200);
    const boats = JSON.parse(rChatBoats.body);
    expect(boats.length).toBe(6);
    expect(boats[0].boat_id).toBe("Barco 1");
    expect(boats[5].boat_id).toBe("Barco 6");

    // 13. /api/chat/messages lista mensagens com suporte a alerta
    const rChatMsgs = simular("/api/chat/messages");
    expect(rChatMsgs.statusCode).toBe(200);
    const msgs = JSON.parse(rChatMsgs.body);
    expect(msgs.length).toBeGreaterThanOrEqual(4);
    expect(msgs.some((m: Record<string, unknown>) => Boolean(m.is_alert))).toBe(
      true,
    );

    // 14. /api/chat/messages?since= filtra por cursor
    const rChatSince = simular("/api/chat/messages?since=2099-01-01T00:00:00Z");
    expect(rChatSince.statusCode).toBe(200);
    expect(JSON.parse(rChatSince.body)).toEqual([]);

    // 15. /api/chat/messages?since= com formato inválido retorna 400
    const rChatInvalidSince = simular("/api/chat/messages?since=invalido");
    expect(rChatInvalidSince.statusCode).toBe(400);

    // 16. /api/chat/send e /api/chat/outbox/:id gerenciam ciclo de saída
    const rChatSend = simular("/api/chat/send", "POST", {
      boat: "Barco 1",
      text: "Mensagem mock de teste",
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(rChatSend.statusCode).toBe(200);
    const sendData = JSON.parse(rChatSend.body);
    expect(sendData.status).toBe("queued");
    expect(sendData.id).toBeDefined();

    const rChatOutbox = simular(`/api/chat/outbox/${sendData.id}`);
    expect(rChatOutbox.statusCode).toBe(200);
    const outboxData = JSON.parse(rChatOutbox.body);
    expect(outboxData.status).toBe("queued");
    expect(outboxData.status_label).toBe("na fila");
    expect(rChatOutbox.body).not.toContain("lido");

    // 17. /api/chat/send valida limite de 200 bytes UTF-8
    const rChatSendOver = simular("/api/chat/send", "POST", {
      boat: "Barco 1",
      text: "a".repeat(201),
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(rChatSendOver.statusCode).toBe(422);

    // 18. /api/chat/send rejeita barco inexistente
    const rChatSendGhost = simular("/api/chat/send", "POST", {
      boat: "Barco 999",
      text: "Teste",
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(rChatSendGhost.statusCode).toBe(404);

    // 19. Rota desconhecida responde 404
    const r12 = simular("/api/desconhecida");
    expect(r12.statusCode).toBe(404);
  });

  it("getTrackGeoJson filtra por limit, from e to", () => {
    const fixedNow = new Date("2026-09-29T21:00:00Z");
    // limit = 1 retorna apenas o fix mais recente
    const trackLimit1 = getTrackGeoJson("!a35ae5d0", fixedNow, { limit: 1 });
    expect(trackLimit1.features.length).toBe(2); // 1 LineString + 1 Point
    expect(trackLimit1.features[0].geometry.type).toBe("LineString");
    if (trackLimit1.features[0].geometry.type === "LineString") {
      expect(trackLimit1.features[0].geometry.coordinates.length).toBe(1);
    }

    // from no futuro retorna vazio
    const trackFuturo = getTrackGeoJson("!a35ae5d0", fixedNow, {
      from: "2099-01-01T00:00:00Z",
    });
    expect(trackFuturo.features).toEqual([]);

    // to no passado distante retorna vazio
    const trackPassado = getTrackGeoJson("!a35ae5d0", fixedNow, {
      to: "2000-01-01T00:00:00Z",
    });
    expect(trackPassado.features).toEqual([]);
  });

  it("getTelemetryGeoJson filtra por from, to e limit", () => {
    const fixedNow = new Date("2026-09-29T21:00:00Z");
    // limit = 0 retorna vazio
    expect(
      getTelemetryGeoJson("!a35ae5d0", fixedNow, { limit: 0 }).features,
    ).toEqual([]);

    // from no futuro retorna vazio
    expect(
      getTelemetryGeoJson("!a35ae5d0", fixedNow, {
        from: "2099-01-01T00:00:00Z",
      }).features,
    ).toEqual([]);

    // to no futuro mantém telemetria
    expect(
      getTelemetryGeoJson("!a35ae5d0", fixedNow, {
        to: "2099-01-01T00:00:00Z",
      }).features.length,
    ).toBe(1);
  });

  it("proxyOsmTile rejeita e cancela resposta upstream que excede MAX_TILE_SIZE sem Content-Length", async () => {
    const plugin = mockApiPlugin();
    let middlewareFn:
      | ((req: IncomingMessage, res: ServerResponse, next: () => void) => void)
      | undefined;
    const fakeServer = {
      middlewares: {
        use(
          fn: (
            req: IncomingMessage,
            res: ServerResponse,
            next: () => void,
          ) => void,
        ) {
          middlewareFn = fn;
        },
      },
    };
    // biome-ignore lint/suspicious/noExplicitAny: mock de configureServer
    (plugin.configureServer as any)(fakeServer);

    let streamCancelled = false;
    const chunk300kb = new Uint8Array(300 * 1024);
    const mockStream = new ReadableStream({
      pull(controller) {
        controller.enqueue(chunk300kb);
      },
      cancel() {
        streamCancelled = true;
      },
    });

    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers(), // sem Content-Length
      body: mockStream,
    } as unknown as Response);

    try {
      let statusCode = 0;
      let bodyBuffer: Buffer | null = null;
      const req = {
        url: "/api/osm/2/1/1.png",
        method: "GET",
      } as IncomingMessage;
      const res = {
        writeHead(code: number) {
          statusCode = code;
        },
        end(data?: string | Buffer) {
          if (Buffer.isBuffer(data)) {
            bodyBuffer = data;
          }
        },
      } as unknown as ServerResponse;

      middlewareFn?.(req, res, () => {});
      // Espera o fluxo assíncrono do proxy
      await new Promise((r) => setTimeout(r, 50));

      expect(statusCode).toBe(200);
      expect(streamCancelled).toBe(true);
      // Fallback para o PNG 1x1 transparente (68 bytes)
      expect(bodyBuffer?.length).toBe(68);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
