/**
 * Gerador determinístico de trilhas sintéticas para testes (T1 do plano parada-jitter).
 *
 * Coordenadas fictícias em torno de um centro informado. O ruído usa o PRNG
 * mulberry32 com semente explícita: mesma semente, mesma saída. Nenhum dado real.
 * Projeção local em metros (mesma aproximação equirretangular de `distanciaM`).
 */

import type { FixDwell, LngLat } from "../../../src/lib/dwell.js";

const R_TERRA = 6_371_008.8;
const RAD = Math.PI / 180;

/** PRNG de 32 bits com semente; devolve uniforme em [0, 1). */
export function mulberry32(semente: number): () => number {
  let estado = semente >>> 0;
  return () => {
    estado = (estado + 0x6d2b79f5) >>> 0;
    let t = estado;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Metros locais (leste, norte) de `p` em relação a `origem`. */
export function emMetros(origem: LngLat, p: LngLat): [number, number] {
  const cosLat = Math.cos(origem[1] * RAD);
  return [
    (p[0] - origem[0]) * RAD * cosLat * R_TERRA,
    (p[1] - origem[1]) * RAD * R_TERRA,
  ];
}

/** Ponto a `dxM` leste e `dyM` norte de `origem`. */
export function deslocar(origem: LngLat, dxM: number, dyM: number): LngLat {
  const cosLat = Math.cos(origem[1] * RAD);
  return [
    origem[0] + dxM / (RAD * cosLat * R_TERRA),
    origem[1] + dyM / (RAD * R_TERRA),
  ];
}

const fixAt = (pos: LngLat, tMs: number): FixDwell => ({
  pos,
  posTime: new Date(tMs).toISOString(),
});

/** Normal padrão por Box-Muller, com desvio `sigma`. */
function gauss(rng: () => number, sigma: number): number {
  const u = Math.max(rng(), Number.MIN_VALUE);
  return sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
}

/** Ruído de um fix: 80% σ 15 m, 15% σ 50 m, 5% spike de 100–300 m. */
function ruidoFix(rng: () => number): [number, number] {
  const u = rng();
  if (u < 0.8) {
    return [gauss(rng, 15), gauss(rng, 15)];
  }
  if (u < 0.95) {
    return [gauss(rng, 50), gauss(rng, 50)];
  }
  const raio = 100 + 200 * rng();
  const ang = 2 * Math.PI * rng();
  return [raio * Math.cos(ang), raio * Math.sin(ang)];
}

/** Ruído para `n` fixes, com 3 sequências de 2–6 fixes seguidos a 60–120 m. */
function ruidoParada(rng: () => number, n: number): [number, number][] {
  const desloc: [number, number][] = Array.from({ length: n }, () =>
    ruidoFix(rng),
  );
  for (let s = 0; s < 3; s++) {
    const tamanho = 2 + Math.floor(rng() * 5);
    if (tamanho > n) {
      continue;
    }
    const inicio = Math.floor(rng() * (n - tamanho + 1));
    const ang = 2 * Math.PI * rng();
    for (let j = 0; j < tamanho; j++) {
      const d = 60 + 60 * rng();
      desloc[inicio + j] = [d * Math.cos(ang), d * Math.sin(ang)];
    }
  }
  return desloc;
}

export interface OpcoesParada {
  centro: LngLat;
  inicioMs: number;
  duracaoMs: number;
  intervaloS?: number;
  semente?: number;
}

/** Barco parado em `centro` durante `duracaoMs`, com ruído de multipath. */
export function parada(o: OpcoesParada): FixDwell[] {
  const intervaloMs = (o.intervaloS ?? 30) * 1000;
  const rng = mulberry32(o.semente ?? 1);
  const n = Math.floor(o.duracaoMs / intervaloMs) + 1;
  return ruidoParada(rng, n).map(([dx, dy], k) =>
    fixAt(deslocar(o.centro, dx, dy), o.inicioMs + k * intervaloMs),
  );
}

export interface OpcoesDeriva {
  de: LngLat;
  rumoGraus: number;
  kmh: number;
  inicioMs: number;
  duracaoMs: number;
  intervaloS?: number;
  semente?: number;
}

/** Deslocamento lento (2–3 km/h) a partir de `de`, com o mesmo ruído da parada. */
export function deriva(o: OpcoesDeriva): FixDwell[] {
  const intervaloMs = (o.intervaloS ?? 30) * 1000;
  const rng = mulberry32(o.semente ?? 1);
  const n = Math.floor(o.duracaoMs / intervaloMs) + 1;
  const velM = o.kmh / 3.6;
  const rumo = o.rumoGraus * RAD;
  return ruidoParada(rng, n).map(([dx, dy], k) => {
    const s = (k * intervaloMs) / 1000;
    const x = dx + velM * s * Math.sin(rumo);
    const y = dy + velM * s * Math.cos(rumo);
    return fixAt(deslocar(o.de, x, y), o.inicioMs + k * intervaloMs);
  });
}

export interface OpcoesNavegacao {
  de: LngLat;
  para: LngLat;
  inicioMs: number;
  kmh: number;
  intervaloS?: number;
  ruidoM?: number;
  semente?: number;
}

/** Linha reta de `de` até `para` a `kmh`, com ruído gaussiano de `ruidoM`. */
export function navegacao(o: OpcoesNavegacao): FixDwell[] {
  if (!(o.kmh > 0)) {
    throw new Error("navegacao: kmh deve ser maior que zero");
  }
  const intervaloS = o.intervaloS ?? 30;
  const ruidoM = o.ruidoM ?? 10;
  const rng = mulberry32(o.semente ?? 1);
  const [ex, ey] = emMetros(o.de, o.para);
  const dist = Math.hypot(ex, ey);
  const passo = (o.kmh / 3.6) * intervaloS;
  const n = dist === 0 ? 1 : Math.ceil(dist / passo) + 1;
  const fixes: FixDwell[] = [];
  for (let k = 0; k < n; k++) {
    const f = dist === 0 ? 0 : Math.min(1, (k * passo) / dist);
    const rx = gauss(rng, ruidoM);
    const ry = gauss(rng, ruidoM);
    fixes.push(
      fixAt(
        deslocar(o.de, ex * f + rx, ey * f + ry),
        o.inicioMs + k * intervaloS * 1000,
      ),
    );
  }
  return fixes;
}

/** Concatena trilhas na ordem recebida. */
export function concat(...trilhas: FixDwell[][]): FixDwell[] {
  return ([] as FixDwell[]).concat(...trilhas);
}

/** Remove os fixes com horário em (aposMs, aposMs + lacunaMs): simula perda de sinal. */
export function comLacuna(
  trilha: readonly FixDwell[],
  aposMs: number,
  lacunaMs: number,
): FixDwell[] {
  return trilha.filter((p) => {
    const t = p.posTime === null ? Number.NaN : Date.parse(p.posTime);
    return !(t > aposMs && t < aposMs + lacunaMs);
  });
}
