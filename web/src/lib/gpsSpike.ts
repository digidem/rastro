/**
 * Filtro de spike GPS por teste A–B–C (T1 do plano parada-jitter).
 *
 * B é spike quando sai da reta entre os vizinhos A e C, no instante de B,
 * mas A e C estão próximos entre si (o barco voltou ao mesmo lugar). Velocidade
 * sozinha nunca marca spike: partida rápida real segue em frente e não gera resíduo.
 * Função pura, sem dependências de mapa/DOM.
 */

import { type FixDwell, distanciaM } from "./dwell.js";

export interface SpikeOpcoes {
  /** Intervalo máximo entre vizinhos para o teste valer, s. */
  intervaloMaxS: number;
  /** Resíduo mínimo de B em relação à interpolação A→C, m. */
  residualM: number;
  /** Excesso mínimo de A→B→C sobre a corda A→C, m. */
  excessoM: number;
  /** Distância máxima entre A e C para o teste valer, m. */
  cordaMaxM: number;
}

export const SPIKE_PADRAO: SpikeOpcoes = {
  intervaloMaxS: 120,
  residualM: 100,
  excessoM: 150,
  cordaMaxM: 100,
};

const R_TERRA = 6_371_008.8;

/** HDOP acima disto: fix de qualidade ruim, limite de resíduo cai para RESIDUO_HDOP_RUIM_M. */
const HDOP_RUIM = 5;
const RESIDUO_HDOP_RUIM_M = 60;

/** Metros locais (leste, norte) de `p` em relação a `origem`. */
function emMetros(
  origem: FixDwell["pos"],
  p: FixDwell["pos"],
): [number, number] {
  const cosLat = Math.cos((origem[1] * Math.PI) / 180);
  return [
    (((p[0] - origem[0]) * Math.PI) / 180) * cosLat * R_TERRA,
    (((p[1] - origem[1]) * Math.PI) / 180) * R_TERRA,
  ];
}

/** Horário em ms; NaN se ausente ou inválido. */
const tempoMs = (p: FixDwell): number =>
  p.posTime === null ? Number.NaN : Date.parse(p.posTime);

function ehSpike(
  a: FixDwell,
  b: FixDwell,
  c: FixDwell,
  o: SpikeOpcoes,
): boolean {
  const tA = tempoMs(a);
  const tB = tempoMs(b);
  const tC = tempoMs(c);
  if (!(Number.isFinite(tA) && Number.isFinite(tB) && Number.isFinite(tC))) {
    return false;
  }
  const dtAb = tB - tA;
  const dtBc = tC - tB;
  const limiteMs = o.intervaloMaxS * 1000;
  if (!(dtAb > 0 && dtBc > 0 && dtAb <= limiteMs && dtBc <= limiteMs)) {
    return false;
  }

  // Origem em A: Bhat = A + f·(C − A), com f = fração do tempo entre A e C.
  const [xB, yB] = emMetros(a.pos, b.pos);
  const [xC, yC] = emMetros(a.pos, c.pos);
  const f = dtAb / (dtAb + dtBc);
  const residual = Math.hypot(xB - f * xC, yB - f * yC);

  const ab = distanciaM(a.pos, b.pos);
  const bc = distanciaM(b.pos, c.pos);
  const ac = distanciaM(a.pos, c.pos);
  const excesso = ab + bc - ac;

  // Com HDOP ruim no próprio B, o resíduo que basta para spike cai (nunca sobe).
  const residualMax =
    b.hdop !== undefined && b.hdop > HDOP_RUIM
      ? Math.min(o.residualM, RESIDUO_HDOP_RUIM_M)
      : o.residualM;
  return residual > residualMax && excesso > o.excessoM && ac <= o.cordaMaxM;
}

/**
 * Marca spikes numa trilha já ordenada por horário. Os vizinhos são os índices
 * i-1 e i+1 da entrada. Primeiro e último fix nunca são spike.
 */
export function marcarSpikes(
  fixes: readonly FixDwell[],
  opcoes: Partial<SpikeOpcoes> = {},
): boolean[] {
  const o = { ...SPIKE_PADRAO, ...opcoes };
  return fixes.map(
    (_, i) =>
      i > 0 &&
      i < fixes.length - 1 &&
      ehSpike(fixes[i - 1], fixes[i], fixes[i + 1], o),
  );
}
