/**
 * Desambiguação de pins sobrepostos (tarefa 18).
 *
 * Nós com coordenadas (quase) idênticas — ex.: rádios de bancada em Atalaia do
 * Norte — se escondem uns aos outros e o clique só alcança o de cima. Aqui os
 * pins de cada grupo são distribuídos num anel de raio constante em PIXELS
 * (por isso depende do zoom), apenas para exibição: o estado do nó e a
 * trilha continuam com a coordenada real.
 */

interface PinFeature {
  type: "Feature";
  properties: Record<string, unknown> | null;
  geometry: { type: "Point"; coordinates: number[] };
}

interface PinCollection {
  type: "FeatureCollection";
  features: PinFeature[];
}

/** Resolução da grade de agrupamento (~11 m): pins mais próximos que isso disputam o mesmo ponto. */
const GRADE_GRAUS = 1e-4;

/** Graus de longitude por pixel no zoom dado (tiles de 512 px do MapLibre). */
export const grausPorPixel = (zoom: number): number => 360 / (512 * 2 ** zoom);

export function espalharPinsSobrepostos<T extends PinCollection>(
  fc: T,
  zoom: number,
  raioPx = 18,
): T {
  const grupos = new Map<string, number[]>();
  fc.features.forEach((f, i) => {
    const [lon, lat] = f.geometry.coordinates;
    const chave = `${Math.round(lon / GRADE_GRAUS)}:${Math.round(lat / GRADE_GRAUS)}`;
    const lista = grupos.get(chave);
    if (lista === undefined) {
      grupos.set(chave, [i]);
    } else {
      lista.push(i);
    }
  });

  const features = fc.features.slice();
  const dLon = grausPorPixel(zoom) * raioPx;
  for (const indices of grupos.values()) {
    if (indices.length < 2) {
      continue;
    }
    // Ordem estável (nodeNum) para o anel não "girar" a cada atualização.
    const ordenados = indices
      .slice()
      .sort(
        (a, b) =>
          Number(fc.features[a].properties?.nodeNum ?? 0) -
          Number(fc.features[b].properties?.nodeNum ?? 0),
      );
    const n = ordenados.length;
    ordenados.forEach((idx, k) => {
      const f = fc.features[idx];
      const [lon, lat] = f.geometry.coordinates;
      const ang = (2 * Math.PI * k) / n - Math.PI / 2;
      const dLat = dLon * Math.cos((lat * Math.PI) / 180);
      features[idx] = {
        ...f,
        properties: { ...f.properties, espalhado: true },
        geometry: {
          type: "Point",
          coordinates: [lon + Math.cos(ang) * dLon, lat + Math.sin(ang) * dLat],
        },
      };
    });
  }
  return { ...fc, features };
}
