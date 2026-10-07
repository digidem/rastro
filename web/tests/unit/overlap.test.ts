import { describe, expect, it } from "vitest";
import {
  espalharPinsSobrepostos,
  grausPorPixel,
} from "../../src/lib/overlap.js";

const pin = (nodeNum: number, lon: number, lat: number, kind = "handheld") => ({
  type: "Feature" as const,
  properties: { nodeNum, kind },
  geometry: { type: "Point" as const, coordinates: [lon, lat] },
});
const fc = (features: ReturnType<typeof pin>[]) => ({
  type: "FeatureCollection" as const,
  features,
});

describe("espalharPinsSobrepostos", () => {
  it("não mexe em pins isolados", () => {
    const entrada = fc([pin(1, -70, -4), pin(2, -69, -4)]);
    const saida = espalharPinsSobrepostos(entrada, 10);
    expect(saida.features.map((f) => f.geometry.coordinates)).toEqual([
      [-70, -4],
      [-69, -4],
    ]);
  });

  it("separa pins na mesma coordenada, todos distintos e a ~raio px do centro", () => {
    const zoom = 12;
    const entrada = fc([
      pin(3, -70.1, -4.37, "boat"),
      pin(1, -70.1, -4.37),
      pin(2, -70.1, -4.37, "fixed_station"),
    ]);
    const saida = espalharPinsSobrepostos(entrada, zoom, 18);
    const coords = saida.features.map((f) => f.geometry.coordinates.join());
    expect(new Set(coords).size).toBe(3);
    for (const f of saida.features) {
      const dLon = f.geometry.coordinates[0] - -70.1;
      expect(Math.abs(dLon)).toBeLessThanOrEqual(
        grausPorPixel(zoom) * 18 + 1e-12,
      );
      expect(f.properties?.espalhado).toBe(true);
    }
  });

  it("é determinístico (mesma posição independente da ordem de entrada)", () => {
    const a = pin(1, -70, -4);
    const b = pin(2, -70, -4, "boat");
    const s1 = espalharPinsSobrepostos(fc([a, b]), 11);
    const s2 = espalharPinsSobrepostos(fc([b, a]), 11);
    expect(s1.features[0].geometry.coordinates).toEqual(
      s2.features[1].geometry.coordinates,
    );
  });

  it("não muta a entrada", () => {
    const entrada = fc([pin(1, -70, -4), pin(2, -70, -4)]);
    espalharPinsSobrepostos(entrada, 10);
    expect(entrada.features[0].geometry.coordinates).toEqual([-70, -4]);
  });
});
