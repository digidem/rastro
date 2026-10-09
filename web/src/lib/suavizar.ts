/**
 * Suavização da linha em movimento e Douglas–Peucker (T3 do plano parada-jitter).
 *
 * Cada fix em movimento recebe o valor de um ajuste linear robusto (posição ×
 * tempo, por eixo, em metros locais) sobre os vizinhos em ±`janelaMs`. Centros de
 * parada são âncoras: não se movem, a janela não as atravessa e o Douglas–Peucker
 * sempre as mantém. Lacunas > `gapMs` quebram a janela e o segmento.
 * Funções puras, sem dependências de mapa/DOM.
 */

import type { LngLat } from "./dwell.js";

export interface ItemLinha {
  pos: LngLat;
  /** Horário do primeiro fix do item, ms (NaN se desconhecido). */
  ini: number;
  /** Horário do último fix do item, ms (igual a `ini` para fix em movimento). */
  fim: number;
  /** Centro de parada: âncora fixa. */
  ancora: boolean;
}

export const SUAVIZAR_PADRAO = {
  janelaMs: 60_000,
  minFixes: 5,
  toleranciaM: 30,
} as const;

const R_TERRA = 6_371_008.8;
const RAD = Math.PI / 180;
/** Resíduos acima deste múltiplo da mediana são descartados no passe robusto. */
const MULT_RESIDUO = 3;

interface Xy {
  x: number;
  y: number;
}

/** Amostra da janela: tempo relativo ao fix (s) e posição em metros locais. */
interface Amostra extends Xy {
  t: number;
}

/** Reta `valor = a + b * t` (t em s). */
interface Reta {
  a: number;
  b: number;
}

interface Ajuste {
  rx: Reta;
  ry: Reta;
}

/** Metros locais de `p` em relação a `origem` (mesma aproximação de distanciaM). */
function paraMetros(p: LngLat, origem: LngLat): Xy {
  const cosLat = Math.cos(origem[1] * RAD);
  return {
    x: (p[0] - origem[0]) * RAD * cosLat * R_TERRA,
    y: (p[1] - origem[1]) * RAD * R_TERRA,
  };
}

/** Inversa de `paraMetros`. */
function deMetros(v: Xy, origem: LngLat): LngLat {
  const cosLat = Math.cos(origem[1] * RAD);
  return [
    origem[0] + v.x / (RAD * cosLat * R_TERRA),
    origem[1] + v.y / (RAD * R_TERRA),
  ];
}

/** Mediana de uma lista não vazia (média dos dois centrais se par). */
export function mediana(valores: readonly number[]): number {
  const s = [...valores].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Mínimos quadrados de `v` sobre `t`; sem variância de `t`, reta horizontal na média. */
function reta(am: readonly Amostra[], eixo: "x" | "y"): Reta {
  const n = am.length;
  const mt = am.reduce((s, a) => s + a.t, 0) / n;
  const mv = am.reduce((s, a) => s + a[eixo], 0) / n;
  let sxx = 0;
  let sxv = 0;
  for (const a of am) {
    sxx += (a.t - mt) ** 2;
    sxv += (a.t - mt) * (a[eixo] - mv);
  }
  const b = sxx > 0 ? sxv / sxx : 0;
  return { a: mv - b * mt, b };
}

function ajustar(am: readonly Amostra[]): Ajuste {
  return { rx: reta(am, "x"), ry: reta(am, "y") };
}

function residuo(s: Amostra, aj: Ajuste): number {
  return Math.hypot(
    s.x - (aj.rx.a + aj.rx.b * s.t),
    s.y - (aj.ry.a + aj.ry.b * s.t),
  );
}

/**
 * Um passe de mínimos quadrados, descarta resíduos > 3× a mediana, reajusta uma vez.
 * Devolve o valor ajustado em t = 0 (o próprio fix).
 */
function valorAjustado(am: readonly Amostra[]): Xy {
  const primeiro = ajustar(am);
  const res = am.map((s) => residuo(s, primeiro));
  const limite = MULT_RESIDUO * mediana(res);
  const boas = am.filter((_, i) => limite <= 0 || res[i] <= limite);
  const fim = boas.length >= 2 ? ajustar(boas) : primeiro;
  return { x: fim.rx.a, y: fim.ry.a };
}

/** Vizinho `j` entra na janela de `vizinho` se não é âncora, está em ±janelaMs e não há lacuna. */
function podeEntrar(
  itens: readonly ItemLinha[],
  j: number,
  vizinho: number,
  t0: number,
  janelaMs: number,
  gapMs: number,
): boolean {
  const it = itens[j];
  return (
    !it.ancora &&
    Math.abs(it.ini - t0) <= janelaMs &&
    !quebraEntre(itens[vizinho], it, gapMs)
  );
}

/** Índices [a, b] (inclusive) da janela de `k`, sem atravessar âncora nem lacuna. */
function janelaDe(
  itens: readonly ItemLinha[],
  k: number,
  janelaMs: number,
  gapMs: number,
): [number, number] {
  const t0 = itens[k].ini;
  let a = k;
  while (a > 0 && podeEntrar(itens, a - 1, a, t0, janelaMs, gapMs)) {
    a--;
  }
  let b = k;
  while (
    b < itens.length - 1 &&
    podeEntrar(itens, b + 1, b, t0, janelaMs, gapMs)
  ) {
    b++;
  }
  return [a, b];
}

function suavizarItem(
  itens: readonly ItemLinha[],
  k: number,
  janelaMs: number,
  minFixes: number,
  gapMs: number,
): LngLat {
  const [a, b] = janelaDe(itens, k, janelaMs, gapMs);
  if (b - a + 1 < minFixes) {
    return itens[k].pos;
  }
  const origem = itens[k].pos;
  const t0 = itens[k].ini;
  const am: Amostra[] = itens.slice(a, b + 1).map((it) => ({
    t: (it.ini - t0) / 1000,
    ...paraMetros(it.pos, origem),
  }));
  return deMetros(valorAjustado(am), origem);
}

/**
 * Suaviza os fixes em movimento. Âncoras e itens sem horário ficam como estão.
 * `janelaMs` e `minFixes` seguem SUAVIZAR_PADRAO; `gapMs` <= 0 desliga a quebra.
 */
export function suavizarLocal(
  itens: readonly ItemLinha[],
  janelaMs: number = SUAVIZAR_PADRAO.janelaMs,
  minFixes: number = SUAVIZAR_PADRAO.minFixes,
  gapMs = 1_800_000,
): ItemLinha[] {
  return itens.map((it, k) =>
    it.ancora || !Number.isFinite(it.ini)
      ? it
      : {
          ...it,
          pos: suavizarItem(itens, k, janelaMs, minFixes, gapMs),
        },
  );
}

/** Lacuna > gapMs entre dois itens consecutivos (gapMs <= 0 desliga). */
export function quebraEntre(
  antes: ItemLinha,
  depois: ItemLinha,
  gapMs: number,
): boolean {
  return gapMs > 0 && depois.ini - antes.fim > gapMs;
}

/** Divide a lista em segmentos separados por lacunas > gapMs. */
export function segmentar(
  itens: readonly ItemLinha[],
  gapMs: number,
): ItemLinha[][] {
  const segmentos: ItemLinha[][] = [];
  let atual: ItemLinha[] = [];
  for (const [k, it] of itens.entries()) {
    if (k > 0 && quebraEntre(itens[k - 1], it, gapMs)) {
      segmentos.push(atual);
      atual = [];
    }
    atual.push(it);
  }
  if (atual.length > 0) {
    segmentos.push(atual);
  }
  return segmentos;
}

/** Distância do ponto `p` ao segmento `a`–`b` (extremos incluídos), metros. */
function distanciaAoSegmento(p: Xy, a: Xy, b: Xy): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) {
    return Math.hypot(p.x - a.x, p.y - a.y);
  }
  const t = Math.min(
    1,
    Math.max(0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2),
  );
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/** Índice do ponto interior de [a, b] mais distante da corda, e a distância. */
function maiorDesvio(
  xy: readonly Xy[],
  a: number,
  b: number,
): [number, number] {
  let idx = a;
  let max = -1;
  for (let i = a + 1; i < b; i++) {
    const d = distanciaAoSegmento(xy[i], xy[a], xy[b]);
    if (d > max) {
      max = d;
      idx = i;
    }
  }
  return [idx, max];
}

/**
 * Douglas–Peucker em metros locais. Sempre mantém o primeiro e o último vértice e
 * as âncoras (centros de parada). Devolve novo array; a entrada não muda.
 */
export function douglasPeucker(
  linha: readonly ItemLinha[],
  toleranciaM: number = SUAVIZAR_PADRAO.toleranciaM,
): ItemLinha[] {
  const n = linha.length;
  if (n <= 2) {
    return [...linha];
  }
  const xy = linha.map((it) => paraMetros(it.pos, linha[0].pos));
  const manter = linha.map((it, i) => it.ancora || i === 0 || i === n - 1);
  const chaves = manter.flatMap((m, i) => (m ? [i] : []));
  const pilha: [number, number][] = [];
  for (let c = 0; c + 1 < chaves.length; c++) {
    pilha.push([chaves[c], chaves[c + 1]]);
  }
  while (pilha.length > 0) {
    const [a, b] = pilha.pop() as [number, number];
    const [idx, desvio] = maiorDesvio(xy, a, b);
    if (desvio > toleranciaM) {
      manter[idx] = true;
      pilha.push([a, idx], [idx, b]);
    }
  }
  return linha.filter((_, i) => manter[i]);
}
