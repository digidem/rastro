import { describe, expect, it } from "vitest";
import { bearingComParada } from "../../src/lib/bearing.js";
import {
  type FixDwell,
  distanciaM,
  duracaoLabel,
  simplifyTrackDwells,
} from "../../src/lib/dwell.js";

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
    expect(r.linhas[0]).toHaveLength(10);
    expect(r.velocidadeKmh).toBeCloseTo(18, 0);
  });

  it("colapsa o novelo de ancoragem em chegada → centróide e some com linhas internas", () => {
    const pts = [
      ...navegar(0, 5), // termina em x=2400
      ...fundear(10, 30, 3000), // 58 min parado
      ...navegar(70, 5, 3600),
    ];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    const d = r.dwells[0];
    expect(d.duracaoMs).toBeGreaterThanOrEqual(15 * M);
    expect(d.fixes).toBe(30);
    expect(d.partidaMs).not.toBeNull();
    // 5 antes + chegada + centróide + 5 depois
    expect(r.linhas).toHaveLength(1);
    expect(r.linhas[0]).toHaveLength(5 + 2 + 5);
    expect(r.parado).toBe(false);
    expect(r.aproximacao).toBeNull();
    // odômetro não infla com o jitter: ~ trajeto de 3600+ m, não dezenas de km
    expect(r.distanciaM).toBeLessThan(6200);
  });

  it("parada curta (<15 min) não colapsa", () => {
    const pts = [
      ...navegar(0, 3),
      ...fundear(6, 4, 1200),
      ...navegar(14, 3, 2000),
    ];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toEqual([]);
  });

  it("histerese: 1 spike isolado fora do raio não encerra a parada", () => {
    const base = fundear(0, 20, 0);
    base[10] = fix(20, 400, 0); // spike de 400 m, mas só 1 fix e sem velocidade de navegação... (retorna logo)
    const r = simplifyTrackDwells(base, { saidaImediataM: 10_000 });
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].fixes).toBe(20); // spike absorvido: parada intacta
    expect(r.parado).toBe(true);
  });

  it("2 fixes consecutivos fora do raio encerram a parada", () => {
    const pts = [
      ...fundear(0, 12, 0),
      fix(24, 90, 0),
      fix(26, 180, 0),
      fix(28, 270, 0),
    ];
    const r = simplifyTrackDwells(pts, { saidaImediataM: 10_000 });
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].fixes).toBe(12);
    expect(r.dwells[0].partidaMs).toBe(T0 + 24 * M);
  });

  it("partida franca (>100 m e ≥4 km/h) encerra com 1 fix", () => {
    const pts = [...fundear(0, 12, 0), fix(24, 500, 0), fix(26, 1100, 0)];
    const r = simplifyTrackDwells(pts);
    expect(r.dwells).toHaveLength(1);
    expect(r.dwells[0].fixes).toBe(12);
    expect(r.dwells[0].partidaMs).toBe(T0 + 24 * M);
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
    // o rumo não muda ao adicionar mais fixes do novelo
    const r2 = simplifyTrackDwells([...pts, ...fundear(70, 10, 3000)]);
    expect(bearingComParada(r2)).toBeCloseTo(rumo as number, 5);
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
    expect(r.linhas[0]).toHaveLength(4);
    expect(r.linhas[0][0][0]).toBeLessThan(r.linhas[0][3][0]);
  });

  it("trilha vazia ou de 1 ponto", () => {
    expect(simplifyTrackDwells([]).linhas).toEqual([]);
    expect(simplifyTrackDwells([fix(0, 0, 0)]).parado).toBe(false);
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
