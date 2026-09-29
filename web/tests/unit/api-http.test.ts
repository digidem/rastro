import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../src/providers/api.js";

// http:// de host não local (o caso CapRover sem TLS): nada de credencial.
const LOC = { protocol: "http:", hostname: "mapa.exemplo.org" };
const LOC_TLS = { protocol: "https:", hostname: "mapa.exemplo.org" };

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const jsonRes = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

// Chaves snake_case do contrato cru da API entram via tuplas (mesmo recurso do
// api.test.ts) para não brigar com o lint de nomenclatura.
const props = (...kv: [string, unknown][]): Record<string, unknown> =>
  Object.fromEntries(kv);

// Última chamada de fetch: [url, init].
const ultimaChamada = (): [string, RequestInit] =>
  fetchMock.mock.calls.at(-1) as [string, RequestInit];

const cabecalhos = (init: RequestInit): Record<string, string> =>
  init.headers as Record<string, string>;

describe("canal plano (http não local)", () => {
  it("latest não manda Authorization e usa credentials omit", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ type: "FeatureCollection", features: [] }),
    );
    const api = createApiClient({ getToken: () => "segredo", loc: LOC });

    await api.latest();

    const [url, init] = ultimaChamada();
    expect(url).toBe("/api/nodes/latest");
    expect(cabecalhos(init).Authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
  });

  it("track idem (nem token de dev, nem cookie)", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ type: "FeatureCollection", features: [] }),
    );
    const api = createApiClient({ getToken: () => "segredo", loc: LOC });

    await api.track("!abcd1234");

    const [, init] = ultimaChamada();
    expect(cabecalhos(init).Authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
  });

  it("authEstado usa credentials omit", async () => {
    fetchMock.mockResolvedValue(
      jsonRes(
        props(["exigida", true], ["autenticado", false], ["dias_lembrar", 30]),
      ),
    );

    await createApiClient({ loc: LOC }).authEstado();

    const [, init] = ultimaChamada();
    expect(cabecalhos(init).Authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
  });

  it("login não vaza o token em header nem leva credencial", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await createApiClient({ loc: LOC }).login("segredo", false);

    const [, init] = ultimaChamada();
    expect(init.method).toBe("POST");
    expect(cabecalhos(init).Authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
    // o corpo do POST é o único lugar onde o token entra — e é o próprio POST.
    expect(init.body).toBe(JSON.stringify({ lembrar: false }));
  });

  it("healthz nunca manda credencial (nem cookie)", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));

    await expect(createApiClient({ loc: LOC }).ping()).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith("/api/healthz", {
      credentials: "omit",
    });
  });
});

describe("com TLS (comportamento antigo preservado)", () => {
  it("latest manda Bearer + same-origin", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ type: "FeatureCollection", features: [] }),
    );
    const api = createApiClient({ getToken: () => "segredo", loc: LOC_TLS });

    await api.latest();

    const [, init] = ultimaChamada();
    expect(cabecalhos(init).Authorization).toBe("Bearer segredo");
    expect(init.credentials).toBe("same-origin");
  });

  it("logout continua com credentials same-origin", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await createApiClient({ loc: LOC_TLS }).logout();

    const [, init] = ultimaChamada();
    expect(init.credentials).toBe("same-origin");
  });
});
