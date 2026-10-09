import { describe, expect, it } from "vitest";
import {
  type FixDwell,
  type LngLat,
  distanciaM,
  simplifyTrackDwells,
} from "../../src/lib/dwell.js";
import {
  type ItemLinha,
  douglasPeucker,
  segmentar,
  suavizarLocal,
} from "../../src/lib/suavizar.js";
import {
  concat,
  deriva,
  deslocar,
  navegacao,
} from "./fixtures/trilhaSintetica.js";

const T0 = Date.parse("2026-10-01T12:00:00Z");
const M = 60_000;
const C: LngLat = [-70.0, -5.0];

const linhaRetaDe = (
  inicioMs: number,
  ruidoM: number,
  semente: number,
  km = 12,
): FixDwell[] =>
  navegacao({
    de: deslocar(C, -km * 500, 0),
    para: deslocar(C, km * 500, 0),
    inicioMs,
    kmh: 18,
    ruidoM,
    semente,
  });

describe("suavizarLocal", () => {
  it("fix isolado com menos de minFixes vizinhos é mantido", () => {
    const itens: ItemLinha[] = [
      { pos: C, ini: T0, fim: T0, ancora: false },
      {
        pos: deslocar(C, 50, 0),
        ini: T0 + 30_000,
        fim: T0 + 30_000,
        ancora: false,
      },
    ];
    expect(suavizarLocal(itens)).toEqual(itens);
  });

  it("âncora nunca se move e a janela não a atravessa", () => {
    // 10 s entre fixes; âncora no k = 7; depois dela, a trilha salta 500 m ao norte
    const pos = (k: number): LngLat =>
      k < 7
        ? deslocar(C, k * 10, 0)
        : k === 7
          ? deslocar(C, 70, 0)
          : deslocar(C, 70 + (k - 7) * 10, 500);
    const itens: ItemLinha[] = Array.from({ length: 15 }, (_, k) => ({
      pos: pos(k),
      ini: T0 + k * 10_000,
      fim: T0 + k * 10_000,
      ancora: k === 7,
    }));
    const s = suavizarLocal(itens);
    expect(s[7].pos).toEqual(itens[7].pos);
    // reta a leste até a âncora: se a janela cruzasse, o fix 6 seria puxado ao norte
    expect(distanciaM(s[6].pos, itens[6].pos)).toBeLessThan(1);
  });

  it("não atravessa lacuna maior que gapMs", () => {
    const a = linhaRetaDe(T0, 10, 1, 3).slice(0, 12);
    const b = linhaRetaDe(T0 + 3 * 3_600_000, 10, 2, 3).slice(0, 12);
    const comoItens = (fixes: FixDwell[]): ItemLinha[] =>
      fixes.map((p) => {
        const t = Date.parse(p.posTime as string);
        return { pos: p.pos, ini: t, fim: t, ancora: false };
      });
    const ia = comoItens(a);
    const ib = comoItens(b);
    const junto = suavizarLocal([...ia, ...ib], 60_000, 5, 1_800_000);
    expect(segmentar(junto, 1_800_000)).toHaveLength(2);
    // cada lado é suavizado como se o outro não existisse
    expect(junto.slice(0, 12)).toEqual(suavizarLocal(ia, 60_000, 5, 1_800_000));
    expect(junto.slice(12)).toEqual(suavizarLocal(ib, 60_000, 5, 1_800_000));
  });
});

describe("douglasPeucker", () => {
  it("mantém extremos e âncoras, remove colineares", () => {
    const itens: ItemLinha[] = Array.from({ length: 7 }, (_, k) => ({
      pos: deslocar(C, k * 100, 0),
      ini: T0 + k,
      fim: T0 + k,
      ancora: k === 3,
    }));
    const r = douglasPeucker(itens, 30);
    // a âncora 3 é colinear, mas fica: é o centro de uma parada
    expect(r).toEqual([itens[0], itens[3], itens[6]]);
  });
});

describe("suavização e odômetro (T3)", () => {
  it("reta com ruído de 10 m: odômetro dentro de ±5% e ≤5% dos vértices", () => {
    for (const semente of [1, 2, 3]) {
      const trilha = linhaRetaDe(T0, 10, semente);
      const r = simplifyTrackDwells(trilha);
      const real = distanciaM(trilha[0].pos, trilha[trilha.length - 1].pos);
      expect(Math.abs(r.distanciaM / real - 1)).toBeLessThan(0.05);
      expect(r.linhas[0].length).toBeLessThanOrEqual(trilha.length * 0.05);
    }
  });

  it("curva de 90° preservada: vértice a menos de 40 m do canto", () => {
    // leste até o canto (9 km/h, 30 s) e depois norte; em 18 km/h o canto fica a ~42 m
    const canto = C;
    const ida = navegacao({
      de: deslocar(C, -3000, 0),
      para: canto,
      inicioMs: T0,
      kmh: 9,
      ruidoM: 10,
      semente: 4,
    });
    const fimIda = Date.parse(ida[ida.length - 1].posTime as string);
    const volta = navegacao({
      de: canto,
      para: deslocar(C, 0, 3000),
      inicioMs: fimIda + 30_000,
      kmh: 9,
      ruidoM: 10,
      semente: 5,
    });
    const r = simplifyTrackDwells(concat(ida, volta));
    const menor = Math.min(...r.linhas.flat().map((p) => distanciaM(p, canto)));
    expect(menor).toBeLessThan(40);
  });

  it("deriva lenta continua visível: deslocamento final > 80% do real", () => {
    const trilha = deriva({
      de: C,
      rumoGraus: 90,
      kmh: 2.5,
      inicioMs: T0,
      duracaoMs: 60 * M,
      semente: 42,
    });
    const r = simplifyTrackDwells(trilha);
    const linha = r.linhas[0];
    const desl = distanciaM(linha[0], linha[linha.length - 1]);
    expect(desl).toBeGreaterThan(0.8 * 2500);
  });

  it("lacuna > 30 min mantém dois segmentos na linha", () => {
    const a = linhaRetaDe(T0, 10, 6, 3);
    const b = navegacao({
      de: deslocar(C, 3000, 0),
      para: deslocar(C, 6000, 0),
      inicioMs: T0 + 60 * M,
      kmh: 18,
      ruidoM: 10,
      semente: 7,
    });
    const r = simplifyTrackDwells(concat(a, b));
    expect(r.linhas).toHaveLength(2);
  });
});
