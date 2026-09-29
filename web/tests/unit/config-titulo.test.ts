import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  TITULO_PADRAO,
  carregarTitulo,
  iniciarTitulo,
  titulo,
} from "../../src/lib/config.js";

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

describe("carregarTitulo()", () => {
  it("usa o title de /config.json, sem credencial e sem cache", async () => {
    fetchMock.mockResolvedValue(jsonRes({ title: "Mapa da Ilha" }));

    await expect(carregarTitulo()).resolves.toBe("Mapa da Ilha");

    expect(fetchMock).toHaveBeenCalledWith("/config.json", {
      cache: "no-store",
      credentials: "omit",
    });
  });

  it("falha de rede cai no padrão", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));

    await expect(carregarTitulo()).resolves.toBe(TITULO_PADRAO);
    await expect(carregarTitulo()).resolves.toBe("Rastro");
  });

  it("title não-string cai no padrão", async () => {
    for (const corpo of [
      { title: 42 },
      { title: null },
      { title: ["a"] },
      {},
    ]) {
      fetchMock.mockResolvedValue(jsonRes(corpo));
      await expect(carregarTitulo()).resolves.toBe(TITULO_PADRAO);
    }
  });

  it("title vazio/sozinho-em-espaço cai no padrão", async () => {
    fetchMock.mockResolvedValue(jsonRes({ title: "   " }));

    await expect(carregarTitulo()).resolves.toBe(TITULO_PADRAO);
  });

  it("404 (sem /config.json, ex.: pnpm dev) cai no padrão", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>não é json</html>", { status: 404 }),
    );

    await expect(carregarTitulo()).resolves.toBe(TITULO_PADRAO);
  });

  it("200 com corpo não-JSON cai no padrão", async () => {
    fetchMock.mockResolvedValue(new Response("quebra", { status: 200 }));

    await expect(carregarTitulo()).resolves.toBe(TITULO_PADRAO);
  });
});

describe("iniciarTitulo()", () => {
  it("publica no sinal e no document.title", async () => {
    fetchMock.mockResolvedValue(jsonRes({ title: "Mapa da Malha" }));

    await iniciarTitulo();

    expect(titulo()).toBe("Mapa da Malha");
    expect(document.title).toBe("Mapa da Malha");
  });

  it("deu errado: volta para Rastro (não deixa título velho na aba)", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));

    await iniciarTitulo();

    expect(titulo()).toBe(TITULO_PADRAO);
    expect(document.title).toBe(TITULO_PADRAO);
  });
});
