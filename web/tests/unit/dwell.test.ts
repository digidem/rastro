import { describe, expect, it } from "vitest";
import { bearingComParada } from "../../src/lib/bearing.js";
import {
  type FixDwell,
  type LngLat,
  distanciaM,
  duracaoLabel,
  simplifyTrackDwells,
} from "../../src/lib/dwell.js";
import {
  comLacuna,
  concat,
  deriva,
  deslocar,
  navegacao,
  parada,
} from "./fixtures/trilhaSintetica.js";

const T0 = Date.parse("2026-10-01T12:00:00Z");
const M = 60_000;
const LAT = -4.0;
const GRAU_M = 111_195; // metros por grau (aprox.)

const fix = (min: number, dxM: number, dyM: number): FixDwell => ({
  pos: [-70 + dxM / GRAU_M, LAT + dyM / GRAU_M],
  posTime: new Date(T0 + min * M).toISOString(),
});

/** Navegando para leste: 600 m a cada 2 min (18 km/h). */
const navegar = (iniMin: number, n: number, x0 = 0): FixDwell[] =>
  Array.from({ length: n }, (_, k) => fix(iniMin + 2 * k, x0 + 600 * k, 0));

/** Fundeado em (cx,0): jitter determinístico ≤ 25 m. */
const fundear = (iniMin: number, n: number, cx: number): FixDwell[] =>
  Array.from({ length: n }, (_, k) =>
    fix(iniMin + 2 * k, cx + 25 * Math.sin(k * 1.7), 25 * Math.cos(k * 2.3)),
  );

describe("simplifyTrackDwells", () => {
  it("trilha só navegando: sem paradas, sem alterações", () => {
    const pts = navegar(0, 10);
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toEqual([]);
    expect(r.parado).toBe(false);
    expect(r.linhas).toHaveLength(1);
    // reta com ruído: Douglas–Peucker (30 m) mantém só os extremos
    expect(r.linhas[0]).toHaveLength(2);
    expect(r.velocidadeKmh).toBeCloseTo(18, 0);
  });

  it("colapsa o novelo de ancoragem num único vértice (centro) e some com linhas internas", () => {
    const pts = [
      ...navegar(0, 5), // termina em x=2400
      ...fundear(10, 30, 3000), // 58 min parado
      ...navegar(70, 5, 3600),
    ];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    const d = r.dwells[0];
    expect(d.duracaoMs).toBeGreaterThanOrEqual(15 * M);
    expect(d.partidaMs).not.toBeNull();
    // início + centro da parada + fim: DP tira os colineares; sem vértice de chegada
    expect(r.linhas).toHaveLength(1);
    expect(r.linhas[0]).toHaveLength(3);
    expect(r.parado).toBe(false);
    expect(r.aproximacao).toBeNull();
    // odômetro não infla com o jitter: ~ trajeto de 3600+ m, não dezenas de km
    expect(r.distanciaM).toBeLessThan(6200);
  });

  it("parada curta (<10 min) não colapsa", () => {
    const pts = [
      ...navegar(0, 3),
      ...fundear(6, 4, 1200),
      ...navegar(14, 3, 2000),
    ];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toEqual([]);
  });

  it("histerese: 1 spike isolado fora do raio não encerra a parada e é contado em excluidos", () => {
    const base = fundear(0, 20, 0);
    base[10] = fix(20, 400, 0); // spike de 400 m isolado
    const r = simplifyTrackDwells(base);
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].excluidos).toBe(1);
    expect(r.dwells[0].fixes).toBe(19); // spike fora da contagem de fixes
    expect(r.dwells[0].partidaMs).toBeNull();
    expect(r.parado).toBe(true);
  });

  it("5 de 6 fixes seguidos fora do raio encerram a parada na primeira saída", () => {
    const pts = [
      ...fundear(0, 12, 0),
      ...[24, 26, 28, 30, 32].map((min) => fix(min, 300, 0)),
    ];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].fixes).toBe(12);
    expect(r.dwells[0].partidaMs).toBe(T0 + 24 * M);
    expect(r.parado).toBe(false);
  });

  it("velocidade sozinha não encerra a parada: salto de 1,1 km que volta é spike", () => {
    const pts = [...fundear(0, 12, 0), fix(24, 1100, 0), ...fundear(26, 6, 0)];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].excluidos).toBe(1);
    expect(r.parado).toBe(true);
    expect(r.dwells[0].partidaMs).toBeNull();
  });

  it("parado até o último fix: parado=true, aproximação congelada e rumo estável", () => {
    const pts = [...navegar(0, 5), ...fundear(10, 30, 3000)];
    const r = simplifyTrackDwells(pts);
    expect(r.parado).toBe(true);
    expect(r.dwells[0].partidaMs).toBeNull();
    expect(r.aproximacao).not.toBeNull();
    const rumo = bearingComParada(r);
    expect(rumo).not.toBeNull();
    expect(Math.abs((rumo as number) - 90)).toBeLessThan(5); // aproximação para leste
    // o rumo não muda ao adicionar mais fixes do novelo (mediana move poucos metros)
    const r2 = simplifyTrackDwells([...pts, ...fundear(70, 10, 3000)]);
    expect(bearingComParada(r2)).toBeCloseTo(rumo as number, 0);
  });

  it("navegando: rumo vem da linha simplificada", () => {
    const r = simplifyTrackDwells(navegar(0, 6));
    expect(bearingComParada(r)).toBeCloseTo(90, 0);
  });

  it("quebra segmentos em lacunas > gap (como a API)", () => {
    const pts = [...navegar(0, 3), ...navegar(120, 3, 5000)];
    const r = simplifyTrackDwells(pts);
    expect(r.linhas).toHaveLength(2);
  });

  it("ordena por horário e ignora fixes inválidos; sem horário não detecta parada", () => {
    const sem: FixDwell[] = fundear(0, 20, 0).map((p) => ({
      ...p,
      posTime: null,
    }));
    expect(simplifyTrackDwells(sem).dwells).toEqual([]);
    const bag = [...navegar(0, 4)].reverse();
    bag.push({ pos: [Number.NaN, 0], posTime: null });
    const r = simplifyTrackDwells(bag);
    // reta: DP mantém os extremos, já ordenados por horário
    expect(r.linhas[0]).toHaveLength(2);
    expect(r.linhas[0][0][0]).toBeLessThan(r.linhas[0][1][0]);
  });

  it("fix de cauda dentro do raio mantém a parada em curso", () => {
    const pts = fundear(0, 15, 0);
    pts.push(fix(30, 80, 0));
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    expect(r.parado).toBe(true);
    expect(r.dwells[0].partidaMs).toBeNull();
    expect(r.velocidadeKmh).toBe(0);
  });

  it("cluster não atravessa lacuna de tempo > gapMs", () => {
    // 10 fixes no ponto A (18 min), depois 2 horas sem sinal, depois 10 fixes
    const pts = [...fundear(0, 10, 0), ...fundear(150, 10, 0)];
    const r = simplifyTrackDwells(pts, { gapMs: 60 * M });
    expect(r.dwells).toHaveLength(2);
    expect(r.dwells[0].partidaMs).not.toBeNull();
    expect(r.dwells[1].chegadaMs).toBeGreaterThan(r.dwells[0].chegadaMs);
    expect(r.parado).toBe(true);
  });

  it("trilha vazia ou de 1 ponto", () => {
    expect(simplifyTrackDwells([]).linhas).toEqual([]);
    expect(simplifyTrackDwells([fix(0, 0, 0)]).parado).toBe(false);
  });
});

describe("simplifyTrackDwells com trilha sintética (T2)", () => {
  const C: LngLat = [-70.0, -5.0];
  const origem: LngLat = deslocar(C, -3000, 0);
  const destino: LngLat = deslocar(C, 3000, 0);

  /** Horário do último fix da trilha, em ms. */
  const fimMs = (t: FixDwell[]): number =>
    Date.parse(t[t.length - 1].posTime as string);

  /** Chega de origem a C a 18 km/h e ancora. Devolve a trilha da ida. */
  const ida = (semente: number, intervaloS = 30) =>
    navegacao({
      de: origem,
      para: C,
      inicioMs: T0,
      kmh: 18,
      intervaloS,
      semente,
    });

  it("parada sintética de 15 h vira exatamente 1 dwell e a linha tem ≤ 3 vértices nela", () => {
    const chegada = ida(11);
    const ancora = parada({
      centro: C,
      inicioMs: fimMs(chegada) + 30_000,
      duracaoMs: 15 * 60 * M,
      semente: 12,
    });
    const partida = navegacao({
      de: C,
      para: destino,
      inicioMs: fimMs(ancora) + 30_000,
      kmh: 18,
      semente: 13,
    });
    const r = simplifyTrackDwells(concat(chegada, ancora, partida));
    expect(r.dwells).toHaveLength(1);
    const d = r.dwells[0];
    expect(d.duracaoMs).toBeGreaterThan(14 * 60 * M);
    const naParada = r.linhas
      .flat()
      .filter((p) => distanciaM(p, d.centroide) < 50);
    expect(naParada.length).toBeLessThanOrEqual(3);
    // parada não soma odômetro: ~6 km de ida e volta, com ruído de nav
    expect(r.distanciaM).toBeLessThan(7000);
  });

  it("parada de 4 h com fix a cada 4 min vira 1 dwell", () => {
    const ancora = parada({
      centro: C,
      inicioMs: T0,
      duracaoMs: 4 * 60 * M,
      intervaloS: 240,
      semente: 31,
    });
    const partida = navegacao({
      de: C,
      para: destino,
      inicioMs: fimMs(ancora) + 240_000,
      kmh: 40,
      intervaloS: 240,
      semente: 32,
    });
    const r = simplifyTrackDwells(concat(ancora, partida));
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].duracaoMs).toBeGreaterThan(3.5 * 60 * M);
  });

  it("deriva a 2,5 km/h por 1 h não é parada", () => {
    const r = simplifyTrackDwells(
      deriva({
        de: C,
        rumoGraus: 90,
        kmh: 2.5,
        inicioMs: T0,
        duracaoMs: 60 * M,
        semente: 42,
      }),
    );
    expect(r.dwells).toEqual([]);
  });

  it("pausa de 5 min não é parada", () => {
    const chegada = ida(51);
    const fimChegada = fimMs(chegada);
    const pausa = parada({
      centro: C,
      inicioMs: fimChegada + 30_000,
      duracaoMs: 5 * M,
      semente: 52,
    });
    const partida = navegacao({
      de: C,
      para: destino,
      inicioMs: fimMs(pausa) + 30_000,
      kmh: 18,
      semente: 53,
    });
    const r = simplifyTrackDwells(concat(chegada, pausa, partida));
    expect(r.dwells).toEqual([]);
  });

  it("partida logo após sequência de spikes é detectada até 3 min depois do início real", () => {
    const parte = parada({
      centro: C,
      inicioMs: T0,
      duracaoMs: 60 * M,
      semente: 61,
    });
    const tRef = T0 + 60 * M;
    // três fixes fora do raio (200, 220, 180 m ao norte), antes da saída real
    const rajada: FixDwell[] = [200, 220, 180].map((dy, k) => ({
      pos: deslocar(C, 0, dy),
      posTime: new Date(tRef + (k + 1) * 30_000).toISOString(),
    }));
    const tReal = tRef + 150_000;
    const saida = navegacao({
      de: C,
      para: deslocar(C, 20_000, 0),
      inicioMs: tRef + 120_000,
      kmh: 40,
      intervaloS: 30,
      semente: 62,
    });
    const r = simplifyTrackDwells(concat(parte, rajada, saida));
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].partidaMs).not.toBeNull();
    expect(r.dwells[0].partidaMs as number).toBeLessThanOrEqual(tReal + 3 * M);
  });

  it("parada até o último fix: parado = true e partidaMs = null", () => {
    const trilha = concat(
      ida(71),
      parada({
        centro: C,
        inicioMs: fimMs(ida(71)) + 30_000,
        duracaoMs: 60 * M,
        semente: 72,
      }),
    );
    const r = simplifyTrackDwells(trilha);
    expect(r.parado).toBe(true);
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].partidaMs).toBeNull();
    expect(r.aproximacao).not.toBeNull();
  });

  it("parada atravessando lacuna de 40 min vira dois dwells", () => {
    const base = parada({
      centro: C,
      inicioMs: T0,
      duracaoMs: 120 * M,
      semente: 81,
    });
    const trilha = comLacuna(base, T0 + 30 * M, 40 * M);
    const r = simplifyTrackDwells(trilha);
    expect(r.dwells).toHaveLength(2);
    expect(r.dwells[0].partidaMs).not.toBeNull();
    expect(r.dwells[1].chegadaMs).toBeGreaterThan(r.dwells[0].chegadaMs);
  });

  it("2 a 3 fixes não formam parada", () => {
    const r = simplifyTrackDwells(
      parada({
        centro: C,
        inicioMs: T0,
        duracaoMs: 60 * M,
        intervaloS: 300,
        semente: 91,
      }).slice(0, 3),
    );
    expect(r.dwells).toEqual([]);
  });

  it("papel: alinhado com a entrada; spike, parada e movimento marcados", () => {
    const chegada = ida(101);
    const ancora = parada({
      centro: C,
      inicioMs: fimMs(chegada) + 30_000,
      duracaoMs: 60 * M,
      semente: 102,
    });
    const spikeIdx = 10;
    ancora[spikeIdx] = { ...ancora[spikeIdx], pos: deslocar(C, 300, 0) };
    const partida = navegacao({
      de: C,
      para: destino,
      inicioMs: fimMs(ancora) + 30_000,
      kmh: 18,
      semente: 103,
    });
    const invalido: FixDwell = { pos: [Number.NaN, 0], posTime: null };
    const pontos = [...concat(chegada, ancora, partida), invalido];
    const r = simplifyTrackDwells(pontos);

    expect(r.papel).toHaveLength(pontos.length);
    const base = chegada.length;
    expect(r.papel[base + spikeIdx]).toBe("spike");
    expect(r.papel[base + 20]).toBe("parada");
    expect(r.papel[0]).toBe("movimento");
    expect(r.papel[pontos.length - 2]).toBe("movimento");
    expect(r.papel[pontos.length - 1]).toBe("movimento");
  });

  it("dispersão: p50 ≤ p90 e ambas positivas; último fix e excluidos preenchidos", () => {
    const chegada = ida(111);
    const ancora = parada({
      centro: C,
      inicioMs: fimMs(chegada) + 30_000,
      duracaoMs: 60 * M,
      semente: 112,
    });
    const r = simplifyTrackDwells(concat(chegada, ancora));
    const d = r.dwells[0];
    expect(d.dispersaoP50M).toBeGreaterThan(0);
    expect(d.dispersaoP90M).toBeGreaterThanOrEqual(d.dispersaoP50M);
    expect(d.ultimoFixMs).toBe(fimMs(ancora));
  });
});

describe("utilitários", () => {
  it("distanciaM ≈ 111 km por grau de latitude", () => {
    expect(distanciaM([0, 0], [0, 1])).toBeGreaterThan(111_000);
    expect(distanciaM([0, 0], [0, 1])).toBeLessThan(111_400);
  });
  it("duracaoLabel", () => {
    expect(duracaoLabel(4 * 3_600_000 + 15 * M)).toBe("4h 15m");
    expect(duracaoLabel(12 * M)).toBe("12m");
  });
});
