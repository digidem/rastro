/**
 * ST-DAH (Spatio-Temporal Dwell Accumulator with Hysteresis) — tarefa 19.
 *
 * Detecta barcos parados/ancorados numa trilha de fixes e colapsa o "novelo"
 * de GPS (multipath + giro de fundeio) num único centróide. Função pura, sem
 * dependências de mapa/DOM.
 *
 * Parada = fixes consecutivos dentro de `raioM` do centróide corrente por
 * pelo menos `minDuracaoMs`. A saída exige `kSaida` fixes consecutivos fora do
 * raio (spikes isolados não quebram a parada), ou 1 fix a mais de
 * `saidaImediataM` com velocidade ≥ `velNavMinKmh` (partida franca).
 */

export type LngLat = [number, number];

export interface FixDwell {
  pos: LngLat;
  posTime: string | null;
}

export interface DwellOptions {
  /** Raio de fundeio, metros. */
  raioM: number;
  /** Duração mínima para considerar parada, ms. */
  minDuracaoMs: number;
  /** Fixes consecutivos fora do raio que confirmam a saída. */
  kSaida: number;
  /** Distância (m) que, com velocidade de navegação, encerra a parada num único fix. */
  saidaImediataM: number;
  /** Velocidade mínima de navegação, km/h. */
  velNavMinKmh: number;
  /** Lacuna temporal que quebra a linha em segmentos (espelha gap_secs da API), ms. */
  gapMs: number;
}

export const DWELL_PADRAO: DwellOptions = {
  raioM: 50,
  minDuracaoMs: 15 * 60_000,
  kSaida: 2,
  saidaImediataM: 100,
  velNavMinKmh: 4,
  gapMs: 1_800_000,
};

export interface Dwell {
  centroide: LngLat;
  chegada: LngLat;
  chegadaMs: number;
  /** null = ainda parado no último fix. */
  partidaMs: number | null;
  /** Do primeiro ao último fix da parada, ms. */
  duracaoMs: number;
  fixes: number;
}

export interface TrilhaSimplificada {
  /** Segmentos sem o novelo: chegada → centróide → fix de partida. */
  linhas: LngLat[][];
  dwells: Dwell[];
  /** Último fix pertence a uma parada em curso. */
  parado: boolean;
  /** Linha de aproximação (até a chegada da parada em curso) para congelar o rumo; null se não parado. */
  aproximacao: LngLat[] | null;
  /** Velocidade entre os dois últimos fixes com horário, km/h; null se indefinida. */
  velocidadeKmh: number | null;
  /** Distância percorrida na linha simplificada, m. */
  distanciaM: number;
}

const R_TERRA = 6_371_008.8;

/** Projeção equirretangular local: precisa e barata nas latitudes amazônicas. */
export function distanciaM(a: readonly number[], b: readonly number[]): number {
  const lat0 = (((a[1] + b[1]) / 2) * Math.PI) / 180;
  const dx = (((b[0] - a[0]) * Math.PI) / 180) * Math.cos(lat0) * R_TERRA;
  const dy = (((b[1] - a[1]) * Math.PI) / 180) * R_TERRA;
  return Math.hypot(dx, dy);
}

const tempoMs = (iso: string | null): number => {
  if (iso === null) {
    return Number.NaN;
  }
  return Date.parse(iso);
};

const valido = (p: FixDwell): boolean =>
  Array.isArray(p.pos) &&
  Number.isFinite(p.pos[0]) &&
  Number.isFinite(p.pos[1]) &&
  Math.abs(p.pos[0]) <= 180 &&
  Math.abs(p.pos[1]) <= 90;

interface Item {
  pos: LngLat;
  ini: number; // ms (NaN se desconhecido)
  fim: number;
}

interface Cluster {
  fim: number; // índice do último fix dentro da parada
  centroide: LngLat;
}

/** Cresce um cluster a partir de `i` com histerese de saída. */
function crescerCluster(
  fixes: FixDwell[],
  ts: number[],
  i: number,
  o: DwellOptions,
): Cluster {
  let soma0 = fixes[i].pos[0];
  let soma1 = fixes[i].pos[1];
  let n = 1;
  let ultimo = i;
  let fora = 0;
  for (let j = i + 1; j < fixes.length; j++) {
    const centro: LngLat = [soma0 / n, soma1 / n];
    const d = distanciaM(fixes[j].pos, centro);
    if (d <= o.raioM) {
      soma0 += fixes[j].pos[0];
      soma1 += fixes[j].pos[1];
      n++;
      ultimo = j;
      fora = 0;
      continue;
    }
    fora++;
    const dt = (ts[j] - ts[ultimo]) / 3_600_000;
    const vKmh =
      dt > 0 ? distanciaM(fixes[j].pos, fixes[ultimo].pos) / 1000 / dt : 0;
    if (fora >= o.kSaida || (d > o.saidaImediataM && vKmh >= o.velNavMinKmh)) {
      break;
    }
  }
  return { fim: ultimo, centroide: [soma0 / n, soma1 / n] };
}

export function simplifyTrackDwells(
  pontos: readonly FixDwell[],
  opcoes: Partial<DwellOptions> = {},
): TrilhaSimplificada {
  const o = { ...DWELL_PADRAO, ...opcoes };
  // Ordem cronológica estável; fixes inválidos são descartados.
  const fixes = pontos
    .filter(valido)
    .map((p, idx) => ({ p, idx, t: tempoMs(p.posTime) }))
    .sort((a, b) =>
      Number.isFinite(a.t) && Number.isFinite(b.t) && a.t !== b.t
        ? a.t - b.t
        : a.idx - b.idx,
    );
  const lista = fixes.map((f) => f.p);
  const ts = fixes.map((f) => f.t);

  const itens: Item[] = [];
  const dwells: Dwell[] = [];
  let parado = false;
  let aproximacaoN = 0; // itens até a chegada da parada em curso

  let i = 0;
  while (i < lista.length) {
    const c = crescerCluster(lista, ts, i, o);
    const duracao = ts[c.fim] - ts[i];
    if (c.fim > i && Number.isFinite(duracao) && duracao >= o.minDuracaoMs) {
      const emCurso = c.fim === lista.length - 1;
      dwells.push({
        centroide: c.centroide,
        chegada: lista[i].pos,
        chegadaMs: ts[i],
        partidaMs: emCurso ? null : ts[c.fim + 1],
        duracaoMs: duracao,
        fixes: c.fim - i + 1,
      });
      itens.push({ pos: lista[i].pos, ini: ts[i], fim: ts[i] });
      if (emCurso) {
        parado = true;
        aproximacaoN = itens.length;
      }
      itens.push({ pos: c.centroide, ini: ts[i], fim: ts[c.fim] });
      i = c.fim + 1;
    } else {
      itens.push({ pos: lista[i].pos, ini: ts[i], fim: ts[i] });
      i++;
    }
  }

  const linhas: LngLat[][] = [];
  let atual: LngLat[] = [];
  let anterior: Item | null = null;
  let distancia = 0;
  for (const it of itens) {
    if (anterior !== null && o.gapMs > 0 && it.ini - anterior.fim > o.gapMs) {
      if (atual.length >= 2) {
        linhas.push(atual);
      }
      atual = [];
    }
    if (anterior !== null && atual.length > 0) {
      distancia += distanciaM(anterior.pos, it.pos);
    }
    atual.push(it.pos);
    anterior = it;
  }
  if (atual.length >= 2) {
    linhas.push(atual);
  }

  let velocidadeKmh: number | null = null;
  if (lista.length >= 2) {
    const n = lista.length - 1;
    const dt = (ts[n] - ts[n - 1]) / 3_600_000;
    if (Number.isFinite(dt) && dt > 0) {
      velocidadeKmh = distanciaM(lista[n].pos, lista[n - 1].pos) / 1000 / dt;
    }
  }

  return {
    linhas,
    dwells,
    parado,
    aproximacao: parado
      ? itens.slice(0, aproximacaoN).map((it) => it.pos)
      : null,
    velocidadeKmh,
    distanciaM: distancia,
  };
}

/** "Ancorado há 4h 15m" / "há 12m". */
export function duracaoLabel(ms: number): string {
  const min = Math.max(0, Math.round(ms / 60_000));
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
