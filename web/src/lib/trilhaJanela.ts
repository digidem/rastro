import {
  type ApiClient,
  TRACK_LIMITE,
  type TrackPoint,
} from "../providers/api.js";

/** Janelas de trilha oferecidas na interface, em horas. */
export const JANELAS_TRILHA_H = [24, 72, 168, 336] as const;
export type JanelaTrilhaH = (typeof JANELAS_TRILHA_H)[number];
export const JANELA_TRILHA_PADRAO: JanelaTrilhaH = 336;

export const rotuloJanela = (h: number): string =>
  h < 48 ? `${h} h` : `${Math.round(h / 24)} dias`;

export const rotuloJanelaCurto = (h: number): string =>
  h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;

export type Trilha = Awaited<ReturnType<ApiClient["track"]>>;

const HORA_MS = 3_600_000;
const DIA_MS = 24 * HORA_MS;
// Dia já fechado ainda pode receber fix atrasado do spool do gateway.
const FECHADA_TTL_MS = 15 * 60_000;
// Fatia que bate no teto da API é dividida ao meio até esta profundidade.
const PROFUNDIDADE_MAX = 5;

/**
 * Fatias de um dia UTC cobrindo a janela. O início desce até a meia-noite
 * UTC para que todas as fatias, menos a última, tenham chave estável no cache.
 */
export const fatiasDaJanela = (
  agoraMs: number,
  horas: number,
): [number, number][] => {
  const inicio = Math.floor((agoraMs - horas * HORA_MS) / DIA_MS) * DIA_MS;
  const fatias: [number, number][] = [];
  for (let a = inicio; a < agoraMs; a += DIA_MS) {
    fatias.push([a, Math.min(a + DIA_MS, agoraMs)]);
  }
  return fatias;
};

type Entrada = { p: Promise<Trilha>; em: number };
const cache = new Map<string, Entrada>();

/** Esquece as fatias guardadas (logout/401: dado da sessão velha não volta). */
export const limparCacheTrilhas = (): void => {
  cache.clear();
};

const tempoMs = (p: TrackPoint): number =>
  p.posTime === null ? Number.NaN : Date.parse(p.posTime);

/** Junta fatias: fixes únicos (as fatias se tocam nas bordas) em ordem cronológica. */
export const juntarTrilhas = (partes: Trilha[]): Trilha => {
  const vistos = new Set<string>();
  const points: TrackPoint[] = [];
  const lines: Trilha["lines"] = [];
  for (const t of partes) {
    lines.push(...t.lines);
    for (const p of t.points) {
      const chave = `${p.posTime}|${p.pos[0]}|${p.pos[1]}`;
      if (!vistos.has(chave)) {
        vistos.add(chave);
        points.push(p);
      }
    }
  }
  points.sort((a, b) => {
    const ta = tempoMs(a);
    const tb = tempoMs(b);
    if (Number.isNaN(ta) || Number.isNaN(tb)) {
      return Number.isNaN(ta) ? (Number.isNaN(tb) ? 0 : 1) : -1;
    }
    return ta - tb;
  });
  return { line: lines[0] ?? null, lines, points };
};

const buscarFatia = async (
  api: Pick<ApiClient, "track">,
  node: string,
  a: number,
  b: number,
  profundidade: number,
): Promise<Trilha> => {
  const t = await api.track(node, { fromMs: a, toMs: b });
  // No teto a API corta os fixes mais velhos: divide a fatia e busca de novo.
  if (t.points.length < TRACK_LIMITE || profundidade >= PROFUNDIDADE_MAX) {
    return t;
  }
  const meio = Math.floor((a + b) / 2);
  const [antes, depois] = await Promise.all([
    buscarFatia(api, node, a, meio, profundidade + 1),
    buscarFatia(api, node, meio, b, profundidade + 1),
  ]);
  return juntarTrilhas([antes, depois]);
};

const fatiaEmCache = (
  api: Pick<ApiClient, "track">,
  node: string,
  a: number,
  b: number,
  agoraMs: number,
): Promise<Trilha> => {
  const chave = `${node}|${a}|${b}`;
  const fechada = b - a === DIA_MS && b <= agoraMs;
  const guardada = cache.get(chave);
  if (
    fechada &&
    guardada !== undefined &&
    agoraMs - guardada.em < FECHADA_TTL_MS
  ) {
    return guardada.p;
  }
  const p = buscarFatia(api, node, a, b, 0);
  if (fechada) {
    cache.set(chave, { p, em: agoraMs });
    // Falha não fica guardada: a próxima busca tenta de novo.
    p.catch(() => {
      if (cache.get(chave)?.p === p) {
        cache.delete(chave);
      }
    });
  }
  return p;
};

/**
 * Trilha do nó nas últimas `horas`, em fatias diárias (cada chamada fica abaixo
 * do teto de fixes da API). Dias fechados vêm do cache; o dia corrente é
 * buscado sempre.
 */
export const buscarTrilhaJanela = async (
  api: Pick<ApiClient, "track">,
  node: string,
  horas: number,
  agoraMs: number = Date.now(),
): Promise<Trilha> => {
  // Fatia fora da maior janela não serve a ninguém.
  const maisVelha =
    agoraMs - (JANELAS_TRILHA_H.at(-1) ?? horas) * HORA_MS - DIA_MS;
  for (const [chave] of cache) {
    const b = Number(chave.split("|").at(-1));
    if (b < maisVelha) {
      cache.delete(chave);
    }
  }
  const inicioJanela = agoraMs - horas * HORA_MS;
  const partes = await Promise.all(
    fatiasDaJanela(agoraMs, horas).map(([a, b]) =>
      fatiaEmCache(api, node, a, b, agoraMs),
    ),
  );
  const t = juntarTrilhas(partes);
  // A primeira fatia começa na meia-noite: corta o que é anterior à janela.
  return {
    ...t,
    points: t.points.filter((p) => !(tempoMs(p) < inicioJanela)),
  };
};
