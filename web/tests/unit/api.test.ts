import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ErrOffline,
  ErrTokenInvalid,
  createApiClient,
} from "../../src/providers/api.js";

const fetchMock = vi.fn();

// Fixtures usam as chaves snake_case do contrato da API; via tuplas para
// não esbarrar nas regras de nomenclatura do lint.
const props = (...kv: [string, unknown][]): Record<string, unknown> =>
  Object.fromEntries(kv);

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const jsonRes = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

// Última chamada de fetch: [url, init].
const ultimaChamada = (): [string, RequestInit] =>
  fetchMock.mock.calls.at(-1) as [string, RequestInit];

describe("latest()", () => {
  it("mapeia features Point para NodeInfo e envia Bearer", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: props(
              ["node_num", 1],
              ["node_id", "!abcd1234"],
              ["nome", "Base A"],
              ["pos_time", "2026-09-27T12:00:00Z"],
              ["battery", 87],
            ),
            geometry: { type: "Point", coordinates: [10, 20] },
          },
          {
            type: "Feature",
            properties: props(
              ["node_num", 2],
              ["node_id", "!efef5678"],
              ["nome", "Barco B"],
              ["pos_time", null],
              ["battery", null],
            ),
            geometry: { type: "Point", coordinates: [10.5, 20.5] },
          },
        ],
      }),
    );
    const api = createApiClient({ getToken: () => "segredo" });

    const nodes = await api.latest();

    expect(nodes).toEqual([
      {
        nodeNum: 1,
        nodeId: "!abcd1234",
        nome: "Base A",
        posTime: "2026-09-27T12:00:00Z",
        battery: 87,
        lon: 10,
        lat: 20,
      },
      {
        nodeNum: 2,
        nodeId: "!efef5678",
        nome: "Barco B",
        posTime: null,
        battery: null,
        lon: 10.5,
        lat: 20.5,
      },
    ]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer segredo",
    );
  });

  it("sem getToken não manda Authorization e manda credentials", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ type: "FeatureCollection", features: [] }),
    );
    const api = createApiClient();

    await api.latest();

    const [, init] = ultimaChamada();
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
  });

  it("401 vira ErrTokenInvalid", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    const api = createApiClient({ getToken: () => "errado" });

    await expect(api.latest()).rejects.toBeInstanceOf(ErrTokenInvalid);
  });

  it("falha de rede vira ErrOffline", async () => {
    fetchMock.mockRejectedValue(new TypeError("connection refused"));
    const api = createApiClient();

    await expect(api.latest()).rejects.toBeInstanceOf(ErrOffline);
  });
});

describe("authEstado()", () => {
  it("lê exigida/autenticado/diasLembrar sem Authorization", async () => {
    fetchMock.mockResolvedValue(
      jsonRes(
        props(["exigida", true], ["autenticado", false], ["dias_lembrar", 60]),
      ),
    );
    const api = createApiClient();

    const estado = await api.authEstado();

    expect(estado).toEqual({
      exigida: true,
      autenticado: false,
      diasLembrar: 60,
    });
    const [url, init] = ultimaChamada();
    expect(url).toBe("/api/auth/estado");
    expect(init.method).toBe("GET");
    expect(
      (init.headers as Record<string, string>).Authorization,
    ).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
  });

  it("corpo incompleto cai nos defaults", async () => {
    fetchMock.mockResolvedValue(jsonRes({}));

    const estado = await createApiClient().authEstado();

    expect(estado).toEqual({
      exigida: true,
      autenticado: false,
      diasLembrar: 30,
    });
  });

  it("status != 200 rejeita", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 500 }));

    await expect(createApiClient().authEstado()).rejects.toThrow();
  });

  it("falha de rede vira ErrOffline", async () => {
    fetchMock.mockRejectedValue(new TypeError("down"));

    await expect(createApiClient().authEstado()).rejects.toBeInstanceOf(
      ErrOffline,
    );
  });
});

describe("login()", () => {
  it("204: POST com Bearer, JSON e credentials same-origin", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const api = createApiClient();

    await expect(api.login("segredo", true)).resolves.toBeUndefined();

    const [url, init] = ultimaChamada();
    expect(url).toBe("/api/auth/sessao");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer segredo");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ lembrar: true }));
    expect(init.credentials).toBe("same-origin");
  });

  it("401 vira ErrTokenInvalid", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));

    await expect(
      createApiClient().login("errado", false),
    ).rejects.toBeInstanceOf(ErrTokenInvalid);
  });

  it("409 vira erro com mensagem de servidor não configurado", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 409 }));

    await expect(createApiClient().login("t", false)).rejects.toThrow(
      "Autenticação por token não configurada no servidor",
    );
  });

  it("falha de rede vira ErrOffline", async () => {
    fetchMock.mockRejectedValue(new TypeError("down"));

    await expect(createApiClient().login("t", false)).rejects.toBeInstanceOf(
      ErrOffline,
    );
  });

  it("não persiste nada em Storage durante o login", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await createApiClient().login("segredo", true);

    expect(setItem).not.toHaveBeenCalled();
  });
});

describe("logout()", () => {
  it("204: DELETE /api/auth/sessao com credentials", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(createApiClient().logout()).resolves.toBeUndefined();

    const [url, init] = ultimaChamada();
    expect(url).toBe("/api/auth/sessao");
    expect(init.method).toBe("DELETE");
    expect(init.credentials).toBe("same-origin");
  });

  it("status != 204 propaga o erro (não engole)", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 500 }));

    await expect(createApiClient().logout()).rejects.toThrow(
      "/api/auth/sessao: 500",
    );
  });

  it("falha de rede propaga ErrOffline", async () => {
    fetchMock.mockRejectedValue(new TypeError("down"));

    await expect(createApiClient().logout()).rejects.toBeInstanceOf(ErrOffline);
  });
});

describe("track()", () => {
  it("separa LineString e Points da trilha", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { node: "!abcd1234", nome: "Base A" },
            geometry: {
              type: "LineString",
              coordinates: [
                [10, 20],
                [10.1, 20.1],
                [10.2, 20.2],
              ],
            },
          },
          {
            type: "Feature",
            properties: props(
              ["pos_time", "2026-09-27T12:00:00Z"],
              ["sats", 7],
            ),
            geometry: { type: "Point", coordinates: [10, 20] },
          },
          {
            type: "Feature",
            properties: props(
              ["pos_time", "2026-09-27T12:05:00Z"],
              ["sats", null],
            ),
            geometry: { type: "Point", coordinates: [10.1, 20.1] },
          },
        ],
      }),
    );
    const api = createApiClient();

    const t = await api.track("!abcd1234");

    expect(t.line).toEqual([
      [10, 20],
      [10.1, 20.1],
      [10.2, 20.2],
    ]);
    expect(t.points).toEqual([
      {
        pos: [10, 20],
        posTime: "2026-09-27T12:00:00Z",
        sats: 7,
      },
      {
        pos: [10.1, 20.1],
        posTime: "2026-09-27T12:05:00Z",
        sats: null,
      },
    ]);
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/nodes/!abcd1234/track");
  });
});

describe("ping()", () => {
  it("healthz ok retorna true", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
    const api = createApiClient();

    await expect(api.ping()).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/healthz", {
      credentials: "same-origin",
    });
  });

  it("rede caída retorna false", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));
    const api = createApiClient();

    await expect(api.ping()).resolves.toBe(false);
  });
});
