import { describe, expect, it } from "vitest";
import { circuloGeo } from "../../src/lib/circulo.js";
import { distanciaM } from "../../src/lib/dwell.js";

const CENTRO: [number, number] = [-70.0, -5.0];

describe("circuloGeo", () => {
  it("anel fechado com passos + 1 vértices", () => {
    const anel = circuloGeo(CENTRO, 120, 48);
    expect(anel).toHaveLength(49);
    expect(anel[0]).toEqual(anel[48]);
  });

  it("todo vértice fica a raioM do centro (±1%)", () => {
    for (const raio of [15, 90, 400]) {
      for (const [lon, lat] of circuloGeo(CENTRO, raio)) {
        const d = distanciaM(CENTRO, [lon, lat]);
        expect(Math.abs(d - raio) / raio).toBeLessThan(0.01);
      }
    }
  });

  it("padrão de 48 passos", () => {
    expect(circuloGeo(CENTRO, 50)).toHaveLength(49);
  });
});
