import { describe, expect, it } from "vitest";
import { bearingDaTrilha, calcularBearing } from "../../src/lib/bearing.js";

describe("calcularBearing", () => {
  it.each([
    ["Norte", 0, 0, 0, 1, 0],
    ["Leste", 0, 0, 1, 0, 90],
    ["Sul", 0, 0, 0, -1, 180],
    ["Oeste", 0, 0, -1, 0, 270],
  ])("%s", (_nome, lon1, lat1, lon2, lat2, esperado) => {
    expect(calcularBearing(lon1, lat1, lon2, lat2)).toBeCloseTo(esperado, 8);
  });

  it("calcula direção no hemisfério sul", () => {
    expect(calcularBearing(-70, -5, -70, -4)).toBeCloseTo(0, 8);
    expect(calcularBearing(-70, -4, -70, -5)).toBeCloseTo(180, 8);
  });

  it("retorna null para coordenadas idênticas", () => {
    expect(calcularBearing(-70, -5, -70, -5)).toBeNull();
  });

  it("retorna null para pontos antípodas", () => {
    expect(calcularBearing(0, 0, 180, 0)).toBeNull();
  });

  it.each([
    [Number.NaN, 0, 1, 0],
    [0, Number.POSITIVE_INFINITY, 1, 0],
    [181, 0, 1, 0],
    [0, 91, 1, 0],
    [0, 0, -181, 0],
    [0, 0, 1, -91],
  ])("rejeita coordenadas inválidas", (a, b, c, d) => {
    expect(calcularBearing(a, b, c, d)).toBeNull();
  });

  it("cruza o antimeridiano pelo caminho curto", () => {
    expect(calcularBearing(179, 0, -179, 0)).toBeCloseTo(90, 8);
    expect(calcularBearing(-179, 0, 179, 0)).toBeCloseTo(270, 8);
  });

  it("normaliza uma direção noroeste em [0, 360)", () => {
    const bearing = calcularBearing(0, 0, -1, 1);
    expect(typeof bearing).toBe("number");
    if (typeof bearing === "number") {
      expect(bearing).toBeGreaterThan(270);
      expect(bearing).toBeLessThan(360);
    }
  });
});

describe("bearingDaTrilha", () => {
  it("não inventa direção sem dois pontos", () => {
    expect(bearingDaTrilha([])).toBeNull();
    expect(bearingDaTrilha([[0, 0]])).toBeNull();
  });

  it("usa o último segmento, inclusive após uma curva", () => {
    expect(
      bearingDaTrilha([
        [0, 0],
        [1, 0],
        [1, 1],
      ]),
    ).toBeCloseTo(0, 8);
  });

  it("ignora repetições finais e preserva o último segmento", () => {
    expect(
      bearingDaTrilha([
        [0, 0],
        [1, 0],
        [1, 0],
        [1, 0],
      ]),
    ).toBeCloseTo(90, 8);
  });

  it("não inventa direção em uma trilha estacionária", () => {
    expect(
      bearingDaTrilha([
        [1, 1],
        [1, 1],
      ]),
    ).toBeNull();
  });

  it("não atravessa uma interrupção por coordenada inválida", () => {
    expect(
      bearingDaTrilha([
        [0, 0],
        [Number.NaN, 0],
        [1, 0],
      ]),
    ).toBeNull();
  });
});
