/**
 * ST-DAH (Spatio-Temporal Dwell Accumulator with Hysteresis) — tarefa 19,
 * reescrito na T2 do plano parada-jitter.
 *
 * Detecta barcos parados/ancorados numa trilha e colapsa o "novelo" de GPS
 * (multipath sob dossel) num único centro: a mediana por eixo dos membros.
 * Função pura, sem dependências de mapa/DOM.
 *
 * Parada = janela de pelo menos `minDuracaoMs` com fixes dentro de
 * `raioEntradaM` do centro, sem deriva. A saída exige `saidaK` fixes fora de
 * `raioSaidaM` entre os últimos `saidaN`, cobrindo ao menos `saidaSpanMinMs`.
 * Velocidade sozinha não decide saída nem spike. Spikes (teste A–B–C de
 * gpsSpike.ts) ficam fora de tudo, mas são contados em `excluidos`.
 */

import { marcarSpikes } from "./gpsSpike.js";
import {
  type ItemLinha,
  douglasPeucker,
  mediana,
  segmentar,
  suavizarLocal,
} from "./suavizar.js";

export type LngLat = [number, number];

export interface FixDwell {
  pos: LngLat;
  posTime: string | null;
}

export interface DwellOptions {
  /** Ocupância para entrar em parada, m. */
  raioEntradaM: number;
  /** Fora deste raio, o fix é evidência de saída, m. */
  raioSaidaM: number;
  /** Duração mínima da janela candidata, ms. */
  minDuracaoMs: number;
  /** Fixes mínimos na janela candidata (cadência esparsa ~4 min existe). */
  minFixes: number;
  /** Fração mínima da janela dentro de `raioEntradaM`. */
  ocupanciaMin: number;
  /** Deslocamento máximo entre o centro do início e do fim da janela (veto de deriva), m. */
  derivaMaxM: number;
  /** Fixes fora de `raioSaidaM` entre os últimos `saidaN` que confirmam a saída. */
  saidaK: number;
  saidaN: number;
  /** Span mínimo da evidência de saída, ms. */
  saidaSpanMinMs: number;
  /** Distância máxima entre centros de paradas vizinhas para mesclar, m. */
  mesclarDistM: number;
  /** Intervalo máximo entre paradas mescladas, ms. */
  mesclarIntervaloMs: number;
  /** Lacuna temporal que quebra a linha em segmentos (espelha gap_secs da API), ms. */
  gapMs: number;
}

export const DWELL_PADRAO: DwellOptions = {
  raioEntradaM: 100,
  raioSaidaM: 150,
  minDuracaoMs: 10 * 60_000,
  minFixes: 4,
  ocupanciaMin: 0.8,
  derivaMaxM: 75,
  saidaK: 5,
  saidaN: 6,
  saidaSpanMinMs: 2 * 60_000,
  mesclarDistM: 100,
  mesclarIntervaloMs: 5 * 60_000,
  gapMs: 1_800_000,
};

/**
 * Papel de cada fix da entrada, alinhado com `pontos` (índice original).
 * Fix inválido (sem posição finita) fica "movimento": não entra em parada nem spike.
 * "parada" cobre os membros e a cauda de saída do intervalo da parada.
 */
export type PapelFix = "movimento" | "parada" | "spike";

export interface Dwell {
  centroide: LngLat;
  chegada: LngLat;
  chegadaMs: number;
  /** null = ainda parado no último fix. Lacuna encerra com o primeiro fix após ela. */
  partidaMs: number | null;
  /** Do primeiro ao último membro da parada, ms. */
  duracaoMs: number;
  /** Fixes da parada (membros e cauda de saída), sem spikes. */
  fixes: number;
  /** Distância radial dos membros ao centróide (sem spikes nem fixes de partida), m. */
  dispersaoP50M: number;
  dispersaoP90M: number;
  /** Spikes descartados dentro do intervalo da parada. */
  excluidos: number;
  /** Horário do último membro. */
  ultimoFixMs: number;
}

export interface TrilhaSimplificada {
  /** Segmentos sem o novelo: uma parada vira um único vértice (centróide). */
  linhas: LngLat[][];
  dwells: Dwell[];
  /** Último fix pertence a uma parada em curso. */
  parado: boolean;
  /** Vértices da linha até o centróide da parada em curso (rumo congelado); null se não parado. */
  aproximacao: LngLat[] | null;
  /** Velocidade entre os dois últimos fixes com horário, km/h; null se indefinida ou parado. */
  velocidadeKmh: number | null;
  /** Distância percorrida na linha simplificada, m. */
  distanciaM: number;
  /** Papel de cada fix de `pontos`, pelo índice original. */
  papel: PapelFix[];
}

const R_TERRA = 6_371_008.8;
const RECALCULO_CENTRO = 20;
const DOIS_MIN_MS = 120_000;

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

interface Entrada {
  p: FixDwell;
  /** Índice em `pontos`. */
  orig: number;
  /** Horário em ms (NaN se desconhecido). */
  t: number;
}

/** Fixes válidos em ordem de horário; empates e sem horário mantêm a ordem de entrada. */
function ordenarFixesValidos(pontos: readonly FixDwell[]): Entrada[] {
  return pontos
    .map((p, orig) => ({ p, orig, t: tempoMs(p.posTime) }))
    .filter((e) => valido(e.p))
    .sort((a, b) =>
      Number.isFinite(a.t) && Number.isFinite(b.t) && a.t !== b.t
        ? a.t - b.t
        : a.orig - b.orig,
    );
}

/**
 * Centro por eixo. A mediana de lon e de lat é a mediana em metros locais:
 * a transformação por eixo é afim e preserva a mediana.
 */
function centroMediano(pts: readonly LngLat[]): LngLat {
  return [mediana(pts.map((p) => p[0])), mediana(pts.map((p) => p[1]))];
}

function quantil(valores: readonly number[], q: number): number {
  const s = [...valores].sort((a, b) => a - b);
  const pos = q * (s.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}

/** Fração dos pontos a até `raioM` de `centro`. */
function ocupancia(
  pts: readonly LngLat[],
  centro: LngLat,
  raioM: number,
): number {
  return pts.filter((p) => distanciaM(p, centro) <= raioM).length / pts.length;
}

/** Lacuna > gapMs entre dois fixes consecutivos (gapMs <= 0 desliga). */
function quebraEm(
  ts: readonly number[],
  a: number,
  b: number,
  gapMs: number,
): boolean {
  return (
    gapMs > 0 &&
    Number.isFinite(ts[a]) &&
    Number.isFinite(ts[b]) &&
    ts[b] - ts[a] > gapMs
  );
}

/** Fixes sem spike, com horário e posição, em ordem. `orig` aponta para `pontos`. */
interface Limpa {
  pos: LngLat[];
  ts: number[];
  orig: number[];
}

function separarSpikes(
  entradas: readonly Entrada[],
  spikes: readonly boolean[],
): Limpa {
  const limpa: Limpa = { pos: [], ts: [], orig: [] };
  for (const [k, e] of entradas.entries()) {
    if (!spikes[k]) {
      limpa.pos.push(e.p.pos);
      limpa.ts.push(e.t);
      limpa.orig.push(e.orig);
    }
  }
  return limpa;
}

const centroDe = (L: Limpa, idx: readonly number[]): LngLat =>
  centroMediano(idx.map((k) => L.pos[k]));

const ultimoMsDe = (L: Limpa, p: Parada): number =>
  L.ts[p.membros[p.membros.length - 1]];

/** Índice final da janela candidata que começa em `i`; -1 se não cobre minDuracaoMs antes de uma lacuna. */
function janelaCandidata(L: Limpa, i: number, o: DwellOptions): number {
  let j = i;
  while (
    j + 1 < L.ts.length &&
    L.ts[j] - L.ts[i] < o.minDuracaoMs &&
    !quebraEm(L.ts, j, j + 1, o.gapMs)
  ) {
    j++;
  }
  return L.ts[j] - L.ts[i] >= o.minDuracaoMs ? j : -1;
}

/** Deslocamento entre o centro dos primeiros e o dos últimos 2 min da janela. */
function derivaJanela(L: Limpa, i: number, j: number): number {
  const ini: LngLat[] = [];
  const fim: LngLat[] = [];
  for (let k = i; k <= j; k++) {
    if (L.ts[k] - L.ts[i] <= DOIS_MIN_MS) {
      ini.push(L.pos[k]);
    }
    if (L.ts[j] - L.ts[k] <= DOIS_MIN_MS) {
      fim.push(L.pos[k]);
    }
  }
  return distanciaM(centroMediano(ini), centroMediano(fim));
}

function janelaAceita(
  L: Limpa,
  i: number,
  j: number,
  o: DwellOptions,
): boolean {
  if (j - i + 1 < o.minFixes) {
    return false;
  }
  const pts = L.pos.slice(i, j + 1);
  const centro = centroMediano(pts);
  return (
    ocupancia(pts, centro, o.raioEntradaM) >= o.ocupanciaMin &&
    derivaJanela(L, i, j) <= o.derivaMaxM
  );
}

interface Marca {
  k: number;
  fora: boolean;
}

/** Índice do primeiro fix fora se os últimos `saidaN` confirmam a saída; -1 caso contrário. */
function partidaEm(
  L: Limpa,
  recentes: readonly Marca[],
  o: DwellOptions,
): number {
  const foras = recentes.filter((m) => m.fora).map((m) => m.k);
  if (foras.length < o.saidaK) {
    return -1;
  }
  const primeiro = foras[0];
  const span = L.ts[foras[foras.length - 1]] - L.ts[primeiro];
  // span NaN (sem horário) não confirma saída
  return span >= o.saidaSpanMinMs ? primeiro : -1;
}

interface Estado {
  membros: number[];
  centro: LngLat;
  recentes: Marca[];
}

/** Avalia o fix `k` contra o centro congelado; devolve o índice de partida ou -1. */
function avaliarFix(L: Limpa, est: Estado, k: number, o: DwellOptions): number {
  const fora = distanciaM(L.pos[k], est.centro) > o.raioSaidaM;
  if (!fora) {
    est.membros.push(k);
    if (est.membros.length % RECALCULO_CENTRO === 0) {
      est.centro = centroDe(L, est.membros);
    }
  }
  est.recentes.push({ k, fora });
  if (est.recentes.length > o.saidaN) {
    est.recentes.shift();
  }
  return partidaEm(L, est.recentes, o);
}

/** Parada já com centro final; `fimL` é exclusivo na lista sem spikes. */
interface Parada {
  iniL: number;
  fimL: number;
  emCurso: boolean;
  membros: number[];
  centro: LngLat;
}

function fecharParada(
  L: Limpa,
  iniL: number,
  fimL: number,
  emCurso: boolean,
  membros: number[],
): Parada {
  return { iniL, fimL, emCurso, membros, centro: centroDe(L, membros) };
}

/** Cresce a parada a partir da janela [i..j]; encerra na partida, lacuna ou fim da lista. */
function crescerParada(
  L: Limpa,
  i: number,
  j: number,
  o: DwellOptions,
): Parada {
  const est: Estado = {
    membros: Array.from({ length: j - i + 1 }, (_, k) => i + k),
    centro: [0, 0],
    recentes: [],
  };
  est.centro = centroDe(L, est.membros);
  for (let k = j + 1; k < L.ts.length; k++) {
    if (quebraEm(L.ts, k - 1, k, o.gapMs)) {
      return fecharParada(L, i, k, false, est.membros);
    }
    const partida = avaliarFix(L, est, k, o);
    if (partida >= 0) {
      const membros = est.membros.filter((m) => m < partida);
      return fecharParada(L, i, partida, false, membros);
    }
  }
  return fecharParada(L, i, L.ts.length, true, est.membros);
}

/** Varre a lista sem spikes e devolve as paradas (ainda sem mesclar). */
function detectarParadas(L: Limpa, o: DwellOptions): Parada[] {
  const paradas: Parada[] = [];
  let i = 0;
  while (i < L.ts.length) {
    const j = janelaCandidata(L, i, o);
    if (j >= 0 && janelaAceita(L, i, j, o)) {
      const parada = crescerParada(L, i, j, o);
      paradas.push(parada);
      i = parada.fimL;
    } else {
      i++;
    }
  }
  return paradas;
}

function podeMesclar(L: Limpa, a: Parada, b: Parada, o: DwellOptions): boolean {
  const intervalo = L.ts[b.iniL] - ultimoMsDe(L, a);
  return (
    distanciaM(a.centro, b.centro) <= o.mesclarDistM &&
    intervalo <= o.mesclarIntervaloMs &&
    (o.gapMs <= 0 || intervalo <= o.gapMs)
  );
}

function unirParadas(L: Limpa, a: Parada, b: Parada): Parada {
  return fecharParada(L, a.iniL, b.fimL, b.emCurso, [
    ...a.membros,
    ...b.membros,
  ]);
}

/** Mescla paradas vizinhas próximas e em curto intervalo; recalcula o centro. */
function mesclarParadas(
  L: Limpa,
  paradas: Parada[],
  o: DwellOptions,
): Parada[] {
  const saida: Parada[] = [];
  for (const p of paradas) {
    const ultima = saida[saida.length - 1];
    if (ultima !== undefined && podeMesclar(L, ultima, p, o)) {
      saida[saida.length - 1] = unirParadas(L, ultima, p);
    } else {
      saida.push(p);
    }
  }
  return saida;
}

function contarEntre(
  tempos: readonly number[],
  ini: number,
  fim: number,
): number {
  return tempos.filter((t) => t >= ini && t < fim).length;
}

function paraDwell(L: Limpa, p: Parada, temposSpike: readonly number[]): Dwell {
  const chegadaMs = L.ts[p.iniL];
  const ultimoFixMs = ultimoMsDe(L, p);
  const partidaMs = p.emCurso ? null : L.ts[p.fimL];
  const raios = p.membros.map((k) => distanciaM(L.pos[k], p.centro));
  return {
    centroide: p.centro,
    chegada: L.pos[p.iniL],
    chegadaMs,
    partidaMs,
    duracaoMs: ultimoFixMs - chegadaMs,
    fixes: p.fimL - p.iniL,
    dispersaoP50M: quantil(raios, 0.5),
    dispersaoP90M: quantil(raios, 0.9),
    excluidos: contarEntre(
      temposSpike,
      chegadaMs,
      partidaMs ?? Number.POSITIVE_INFINITY,
    ),
    ultimoFixMs,
  };
}

/** Vértices da linha: fix a fix fora de paradas; cada parada vira um único vértice (centro). */
function montarItens(L: Limpa, paradas: readonly Parada[]): ItemLinha[] {
  const itens: ItemLinha[] = [];
  let k = 0;
  let p = 0;
  while (k < L.ts.length) {
    const parada: Parada | undefined = paradas[p];
    if (parada !== undefined && parada.iniL === k) {
      itens.push({
        pos: parada.centro,
        ini: L.ts[k],
        fim: ultimoMsDe(L, parada),
        ancora: true,
      });
      k = parada.fimL;
      p++;
    } else {
      itens.push({ pos: L.pos[k], ini: L.ts[k], fim: L.ts[k], ancora: false });
      k++;
    }
  }
  return itens;
}

/** Soma das distâncias entre vértices consecutivos de cada segmento, m. */
function somarDistancias(segmentos: readonly ItemLinha[][]): number {
  let total = 0;
  for (const seg of segmentos) {
    for (let k = 1; k < seg.length; k++) {
      total += distanciaM(seg[k - 1].pos, seg[k].pos);
    }
  }
  return total;
}

/**
 * Odômetro sobre a linha suavizada ANTES do Douglas–Peucker;
 * `linhas` vem DEPOIS (segmentos com menos de 2 vértices somem).
 */
function montarLinhaOdometro(
  suavizados: readonly ItemLinha[],
  gapMs: number,
): { linhas: LngLat[][]; distanciaM: number } {
  const segmentos = segmentar(suavizados, gapMs);
  const linhas = segmentos
    .map((seg) => douglasPeucker(seg).map((it) => it.pos))
    .filter((linha) => linha.length >= 2);
  return { linhas, distanciaM: somarDistancias(segmentos) };
}

function calcularVelocidade(
  entradas: readonly Entrada[],
  parado: boolean,
): number | null {
  if (parado) {
    return 0;
  }
  if (entradas.length < 2) {
    return null;
  }
  const a = entradas[entradas.length - 2];
  const b = entradas[entradas.length - 1];
  const dt = (b.t - a.t) / 3_600_000;
  return Number.isFinite(dt) && dt > 0
    ? distanciaM(b.p.pos, a.p.pos) / 1000 / dt
    : null;
}

function montarPapel(
  n: number,
  entradas: readonly Entrada[],
  spikes: readonly boolean[],
  L: Limpa,
  paradas: readonly Parada[],
): PapelFix[] {
  const papel: PapelFix[] = Array.from(
    { length: n },
    (): PapelFix => "movimento",
  );
  for (const [k, e] of entradas.entries()) {
    if (spikes[k]) {
      papel[e.orig] = "spike";
    }
  }
  for (const p of paradas) {
    for (let k = p.iniL; k < p.fimL; k++) {
      papel[L.orig[k]] = "parada";
    }
  }
  return papel;
}

export function simplifyTrackDwells(
  pontos: readonly FixDwell[],
  opcoes: Partial<DwellOptions> = {},
): TrilhaSimplificada {
  const o = { ...DWELL_PADRAO, ...opcoes };
  const entradas = ordenarFixesValidos(pontos);
  const spikes = marcarSpikes(entradas.map((e) => e.p));
  const L = separarSpikes(entradas, spikes);
  const paradas = mesclarParadas(L, detectarParadas(L, o), o);
  const temposSpike = entradas.filter((_, k) => spikes[k]).map((e) => e.t);
  const itens = montarItens(L, paradas);
  const suavizados = suavizarLocal(itens, undefined, undefined, o.gapMs);
  const { linhas, distanciaM } = montarLinhaOdometro(suavizados, o.gapMs);
  const ultima: Parada | undefined = paradas[paradas.length - 1];
  const parado = ultima?.emCurso === true;

  return {
    linhas,
    dwells: paradas.map((p) => paraDwell(L, p, temposSpike)),
    parado,
    aproximacao: parado ? suavizados.map((it) => it.pos) : null,
    velocidadeKmh: calcularVelocidade(entradas, parado),
    distanciaM,
    papel: montarPapel(pontos.length, entradas, spikes, L, paradas),
  };
}

/** "Ancorado há 4h 15m" / "há 12m". */
export function duracaoLabel(ms: number): string {
  const min = Math.max(0, Math.round(ms / 60_000));
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
