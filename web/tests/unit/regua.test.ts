import { describe, expect, it } from "vitest";
import {
  distanciaTotalM,
  distanciasSegmentos,
  etaMs,
  haversineM,
  rotuloDistancia,
  rotuloRumo,
  rumo,
  textoCompartilhar,
} from "../../src/lib/regua.js";

describe("haversineM", () => {
  it("1° de latitude ≈ 111 195 m", () => {
    expect(haversineM([-70, -5], [-70, -4])).toBeGreaterThan(111_194);
    expect(haversineM([-70, -5], [-70, -4])).toBeLessThan(111_196);
  });

  it("distância zero para o mesmo ponto", () => {
    expect(haversineM([-70, -5], [-70, -5])).toBe(0);
  });
});

describe("distanciasSegmentos e distanciaTotalM", () => {
  const caminho: [number, number][] = [
    [-70, -5],
    [-70, -4],
    [-69, -4],
  ];

  it("retorna um valor por segmento", () => {
    const segmentos = distanciasSegmentos(caminho);
    expect(segmentos).toHaveLength(2);
    expect(segmentos[0]).toBeCloseTo(haversineM([-70, -5], [-70, -4]), 6);
    expect(segmentos[1]).toBeCloseTo(haversineM([-70, -4], [-69, -4]), 6);
  });

  it("total de 3 pontos soma os segmentos", () => {
    const soma = distanciasSegmentos(caminho).reduce((a, b) => a + b, 0);
    expect(distanciaTotalM(caminho)).toBeCloseTo(soma, 6);
  });

  it("sem segmentos com menos de 2 pontos", () => {
    expect(distanciasSegmentos([])).toEqual([]);
    expect(distanciasSegmentos([[-70, -5]])).toEqual([]);
    expect(distanciaTotalM([])).toBe(0);
    expect(distanciaTotalM([[-70, -5]])).toBe(0);
  });
});

describe("rotuloDistancia", () => {
  it.each([
    [0, "0 m"],
    [999.4, "999 m"],
    [999.6, "1,0 km"],
    [1000, "1,0 km"],
    [1234, "1,2 km"],
    [99_949, "99,9 km"],
    [99_960, "100 km"],
    [100_000, "100 km"],
    [134_400, "134 km"],
  ])("%s m → %s", (metros, esperado) => {
    expect(rotuloDistancia(metros)).toBe(esperado);
  });
});

describe("rotuloRumo", () => {
  it.each([
    [0, "N 0°"],
    [22.4, "N 22°"],
    [22.6, "NE 23°"],
    [90, "L 90°"],
    [180, "S 180°"],
    [225, "SO 225°"],
    [315, "NO 315°"],
    [359.6, "N 0°"],
    [null, "—"],
  ])("%s → %s", (graus, esperado) => {
    expect(rotuloRumo(graus)).toBe(esperado);
  });
});

describe("rumo", () => {
  it("reusa o azimute geodésico", () => {
    expect(rumo([-70, -5], [-70, -4])).toBeCloseTo(0, 8);
  });

  it("null para pontos coincidentes", () => {
    expect(rumo([-70, -5], [-70, -5])).toBeNull();
  });
});

describe("etaMs", () => {
  it.each([
    [null, null],
    [0, null],
    [1.9, null],
    [2, 1_800_000],
    [10, 360_000],
  ])("1000 m a velocidade %s km/h", (velocidade, esperado) => {
    expect(etaMs(1000, velocidade)).toBe(esperado);
  });

  it("rejeita velocidade não finita", () => {
    expect(etaMs(1000, Number.NaN)).toBeNull();
    expect(etaMs(1000, Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("textoCompartilhar", () => {
  it("uma linha por vértice e total no fim", () => {
    const texto = textoCompartilhar([
      { pos: [-70, -5] },
      { pos: [-70, -4], nome: "Barco 1" },
    ]);
    expect(texto).toBe(
      "1. -5.00000, -70.00000\n2. -4.00000, -70.00000 (Barco 1)\nTotal: 111 km",
    );
  });

  it("sem nome, sem parênteses", () => {
    const texto = textoCompartilhar([{ pos: [-70, -5], nome: null }]);
    expect(texto).toBe("1. -5.00000, -70.00000\nTotal: 0 m");
  });
});
