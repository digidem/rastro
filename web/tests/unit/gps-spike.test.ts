import { describe, expect, it } from "vitest";
import type { FixDwell, LngLat } from "../../src/lib/dwell.js";
import { SPIKE_PADRAO, marcarSpikes } from "../../src/lib/gpsSpike.js";
import {
  comLacuna,
  concat,
  deriva,
  deslocar,
  navegacao,
  parada,
} from "./fixtures/trilhaSintetica.js";

const T0 = Date.parse("2026-10-01T12:00:00Z");
const CENTRO: LngLat = [-70.0, -5.0];

/** Fix em `s` segundos após T0, com deslocamento (m) leste/norte de CENTRO. */
const fixM = (s: number, dxM: number, dyM: number): FixDwell => ({
  pos: deslocar(CENTRO, dxM, dyM),
  posTime: new Date(T0 + s * 1000).toISOString(),
});

describe("marcarSpikes", () => {
  it("spike isolado numa parada é marcado e só ele", () => {
    const pts = Array.from({ length: 20 }, (_, k) =>
      fixM(k * 30, 10 * Math.sin(k * 1.7), 10 * Math.cos(k * 2.3)),
    );
    pts[10] = fixM(300, 200, 0);
    const marcas = marcarSpikes(pts);
    expect(marcas[10]).toBe(true);
    expect(marcas.filter(Boolean)).toHaveLength(1);
  });

  it("partida rápida real a 40 km/h em frente não é marcada", () => {
    const pts = navegacao({
      de: CENTRO,
      para: deslocar(CENTRO, 20_000, 0),
      inicioMs: T0,
      kmh: 40,
      ruidoM: 10,
      semente: 3,
    });
    expect(marcarSpikes(pts).some(Boolean)).toBe(false);
  });

  it("ida-e-volta com A–C > 100 m não é marcada", () => {
    // B tem resíduo (300 m) e excesso (400 m) altos; só a corda A–C (200 m) o salva.
    const pts = [fixM(0, 0, 0), fixM(30, 0, 400), fixM(60, 0, 200)];
    expect(marcarSpikes(pts)).toEqual([false, false, false]);
  });

  it("lacuna > 120 s entre vizinhos desliga o teste", () => {
    const comSinal = [fixM(0, 0, 0), fixM(60, 0, 200), fixM(120, 5, 0)];
    expect(marcarSpikes(comSinal)[1]).toBe(true);

    const comLacunaTemporal = [
      fixM(0, 0, 0),
      fixM(150, 0, 200),
      fixM(180, 5, 0),
    ];
    expect(marcarSpikes(comLacunaTemporal)[1]).toBe(false);
  });

  it("gerador é determinístico: mesma semente, mesma saída", () => {
    const geradores = (semente: number) => [
      parada({
        centro: CENTRO,
        inicioMs: T0,
        duracaoMs: 2 * 3_600_000,
        semente,
      }),
      deriva({
        de: CENTRO,
        rumoGraus: 45,
        kmh: 2.5,
        inicioMs: T0,
        duracaoMs: 3_600_000,
        semente,
      }),
      navegacao({
        de: CENTRO,
        para: deslocar(CENTRO, 3000, 0),
        inicioMs: T0,
        kmh: 20,
        semente,
      }),
    ];
    expect(geradores(42)).toEqual(geradores(42));
    expect(geradores(42)[0]).not.toEqual(geradores(43)[0]);
  });

  it("primeiro e último fix nunca são spike", () => {
    const pts = [fixM(0, 250, 0), fixM(30, 0, 0), fixM(60, 0, 0)];
    const marcas = marcarSpikes(pts);
    expect(marcas[0]).toBe(false);
    expect(marcas[2]).toBe(false);
  });

  it("opções sobrescrevem SPIKE_PADRAO", () => {
    const pts = [fixM(0, 0, 0), fixM(30, 0, 200), fixM(60, 5, 0)];
    expect(marcarSpikes(pts)[1]).toBe(true);
    expect(marcarSpikes(pts, { residualM: 500 })[1]).toBe(false);
    expect(SPIKE_PADRAO.residualM).toBe(100);
  });

  it("concat e comLacuna mantêm a ordem e removem só a janela sem sinal", () => {
    const a = [fixM(0, 0, 0), fixM(30, 0, 0)];
    const b = [fixM(60, 0, 0), fixM(90, 0, 0)];
    const juntos: FixDwell[] = concat(a, b);
    expect(juntos).toHaveLength(4);
    const com = comLacuna(juntos, T0 + 30_000, 45_000);
    expect(com.map((p) => p.posTime)).toEqual([
      juntos[0].posTime,
      juntos[1].posTime,
      juntos[3].posTime,
    ]);
  });
});
