import { describe, expect, it, vi } from "vitest";
import type { VerticeResolvido } from "../../src/lib/regua.js";
import {
  CAMADA_REGUA_LINHA,
  CAMADA_REGUA_ROTULOS,
  CAMADA_REGUA_VERTICES,
  FONTE_REGUA,
  type FeatureCollectionRegua,
  atualizarRegua,
  garantirCamadasRegua,
  geojsonRegua,
} from "../../src/lib/reguaMapa.js";

const livre = (indice: number, lon: number, lat: number): VerticeResolvido => ({
  pos: [lon, lat],
  nome: null,
  nodeNum: null,
  indice,
});

const noAncorado = (
  indice: number,
  lon: number,
  lat: number,
  nodeNum: number,
): VerticeResolvido => ({
  pos: [lon, lat],
  nome: "Barco teste",
  nodeNum,
  indice,
});

const contar = (fc: FeatureCollectionRegua) => {
  const linhas = fc.features.filter((f) => f.geometry.type === "LineString");
  const vertices = fc.features.filter(
    (f) => f.geometry.type === "Point" && "indice" in f.properties,
  );
  const rotulos = fc.features.filter(
    (f) => f.geometry.type === "Point" && "rotulo" in f.properties,
  );
  return {
    linhas: linhas.length,
    vertices: vertices.length,
    rotulos: rotulos.length,
  };
};

/** Mapa falso com a API mínima usada pela régua. */
function criarMapaFalso() {
  const fontes = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const camadas = new Map<string, unknown>();
  const addSource = vi.fn((id: string) => {
    fontes.set(id, { setData: vi.fn() });
  });
  const addLayer = vi.fn((camada: { id: string }) => {
    camadas.set(camada.id, camada);
  });
  const mapa = {
    getSource: (id: string) => fontes.get(id),
    addSource,
    getLayer: (id: string) => camadas.get(id),
    addLayer,
  };
  return { mapa: mapa as never, fontes, camadas, addSource, addLayer };
}

describe("geojsonRegua", () => {
  it("sem pontos devolve FeatureCollection vazia", () => {
    expect(geojsonRegua([])).toEqual({
      type: "FeatureCollection",
      features: [],
    });
  });

  it("um ponto gera só o vértice, sem linha nem rótulo", () => {
    const fc = geojsonRegua([livre(0, -70, -5)]);
    expect(contar(fc)).toEqual({ linhas: 0, vertices: 1, rotulos: 0 });
    expect(fc.features[0]?.properties).toEqual({ indice: 0, ancorado: false });
  });

  it("três pontos geram 3 vértices, 1 linha e 2 rótulos", () => {
    const fc = geojsonRegua([
      livre(0, -70, -5),
      livre(1, -70, -4.99),
      livre(2, -69.99, -4.99),
    ]);
    expect(contar(fc)).toEqual({ linhas: 1, vertices: 3, rotulos: 2 });
    expect(fc.features).toHaveLength(6);
  });

  it("rótulo do meio do segmento usa a média dos vértices e distância + rumo", () => {
    const fc = geojsonRegua([livre(0, -70, -5), livre(1, -70, -4)]);
    const rotulo = fc.features.find((f) => "rotulo" in f.properties);
    expect(rotulo?.geometry).toEqual({
      type: "Point",
      coordinates: [-70, -4.5],
    });
    expect(rotulo?.properties.rotulo).toBe("111 km · N 0°");
  });

  it("vértice de nó marca ancorado e mantém o índice original", () => {
    const fc = geojsonRegua([livre(0, -70, -5), noAncorado(2, -69, -5, 7)]);
    const ancorado = fc.features.find(
      (f) => "indice" in f.properties && f.properties.indice === 2,
    );
    expect(ancorado?.properties.ancorado).toBe(true);
  });
});

describe("garantirCamadasRegua", () => {
  it("cria fonte e as três camadas, sem beforeId", () => {
    const { mapa, fontes, camadas, addSource, addLayer } = criarMapaFalso();
    garantirCamadasRegua(mapa);

    expect(fontes.has(FONTE_REGUA)).toBe(true);
    expect(addSource).toHaveBeenCalledTimes(1);
    expect(addLayer).toHaveBeenCalledTimes(3);
    expect([...camadas.keys()]).toEqual([
      CAMADA_REGUA_LINHA,
      CAMADA_REGUA_VERTICES,
      CAMADA_REGUA_ROTULOS,
    ]);
    for (const chamada of addLayer.mock.calls) {
      expect(chamada).toHaveLength(1);
    }
  });

  it("é idempotente: segunda chamada não duplica fonte nem camadas", () => {
    const { mapa, addSource, addLayer } = criarMapaFalso();
    garantirCamadasRegua(mapa);
    garantirCamadasRegua(mapa);

    expect(addSource).toHaveBeenCalledTimes(1);
    expect(addLayer).toHaveBeenCalledTimes(3);
  });
});

describe("atualizarRegua", () => {
  it("faz setData na fonte com a FeatureCollection", () => {
    const { mapa, fontes } = criarMapaFalso();
    garantirCamadasRegua(mapa);
    atualizarRegua(mapa, [livre(0, -70, -5), livre(1, -70, -4)]);

    const setData = fontes.get(FONTE_REGUA)?.setData;
    expect(setData).toHaveBeenCalledTimes(1);
    const enviado = setData?.mock.calls[0]?.[0] as FeatureCollectionRegua;
    expect(contar(enviado)).toEqual({ linhas: 1, vertices: 2, rotulos: 1 });
  });

  it("sem fonte não faz nada e não lança", () => {
    const mapa = {
      getSource: () => undefined,
      addSource: vi.fn(),
      getLayer: () => undefined,
      addLayer: vi.fn(),
    } as never;
    expect(() => atualizarRegua(mapa, [livre(0, -70, -5)])).not.toThrow();
  });
});
