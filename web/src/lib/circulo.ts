import type { LngLat } from "./dwell.js";

const R_TERRA = 6_371_008.8;

/**
 * Anel fechado (primeiro vértice repetido no fim) de um círculo de `raioM` metros
 * em torno de `centro`, em graus. Projeção local: aceita a aproximação plana para
 * raios de até alguns quilômetros.
 */
export function circuloGeo(
  centro: LngLat,
  raioM: number,
  passos = 48,
): LngLat[] {
  const [lon, lat] = centro;
  const cosLat = Math.cos((lat * Math.PI) / 180);
  const anel: LngLat[] = [];
  for (let k = 0; k <= passos; k++) {
    const ang = (2 * Math.PI * (k % passos)) / passos;
    const dLat = (raioM * Math.sin(ang)) / R_TERRA;
    const dLon = (raioM * Math.cos(ang)) / (R_TERRA * cosLat);
    anel.push([lon + (dLon * 180) / Math.PI, lat + (dLat * 180) / Math.PI]);
  }
  return anel;
}
