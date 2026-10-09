import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buscarTrilhaJanela,
  fatiasDaJanela,
  juntarTrilhas,
  limparCacheTrilhas,
} from "../../src/lib/trilhaJanela.js";
import {
  type JanelaTrack,
  TRACK_LIMITE,
  type TrackPoint,
} from "../../src/providers/api.js";

const HORA = 3_600_000;
const DIA = 24 * HORA;
// 2026-10-09T15:00:00Z
const AGORA = Date.UTC(2026, 9, 9, 15);

const ponto = (ms: number, lon = -70): TrackPoint => ({
  pos: [lon, -5],
  posTime: new Date(ms).toISOString(),
  sats: null,
});

/** API falsa: devolve os fixes da janela pedida, no máximo `teto` (os mais novos). */
const apiFalsa = (fixes: TrackPoint[], teto = TRACK_LIMITE) => {
  const track = vi.fn(async (_node: string, j?: JanelaTrack) => {
    const dentro = fixes.filter((p) => {
      const t = Date.parse(p.posTime as string);
      return j === undefined || (t >= j.fromMs && t <= j.toMs);
    });
    return { line: null, lines: [], points: dentro.slice(-teto) };
  });
  return { track };
};

beforeEach(() => {
  limparCacheTrilhas();
});

describe("fatiasDaJanela", () => {
  it("cobre a janela com dias UTC inteiros e termina em agora", () => {
    const f = fatiasDaJanela(AGORA, 72);
    expect(f[0][0]).toBe(Date.UTC(2026, 9, 6));
    expect(f.at(-1)?.[1]).toBe(AGORA);
    for (let i = 1; i < f.length; i++) {
      expect(f[i][0]).toBe(f[i - 1][1]);
    }
    expect(f.slice(0, -1).every(([a, b]) => b - a === DIA)).toBe(true);
  });
});

describe("juntarTrilhas", () => {
  it("remove o fix repetido na borda e ordena por horário", () => {
    const a = ponto(AGORA - 2 * HORA);
    const b = ponto(AGORA - HORA);
    const t = juntarTrilhas([
      { line: null, lines: [], points: [b] },
      { line: null, lines: [], points: [a, b] },
    ]);
    expect(t.points).toEqual([a, b]);
  });
});

describe("buscarTrilhaJanela", () => {
  it("parado há 24 h ainda mostra os dias anteriores da janela", async () => {
    const fixes = [
      ponto(AGORA - 5 * DIA, -70.1),
      ponto(AGORA - 3 * DIA, -70.05),
      ponto(AGORA - 2 * HORA),
      ponto(AGORA - HORA),
    ];
    const api = apiFalsa(fixes);
    const t = await buscarTrilhaJanela(api, "!n", 336, AGORA);
    expect(t.points).toEqual(fixes);
  });

  it("corta o que fica antes do início da janela", async () => {
    const velho = ponto(AGORA - 30 * HORA);
    const novo = ponto(AGORA - HORA);
    const t = await buscarTrilhaJanela(
      apiFalsa([velho, novo]),
      "!n",
      24,
      AGORA,
    );
    expect(t.points).toEqual([novo]);
  });

  it("fatia no teto da API é dividida até trazer todos os fixes", async () => {
    // 2500 fixes em 14 h de hoje: uma chamada só traria os 2000 mais novos.
    const fixes = Array.from({ length: 2500 }, (_, k) =>
      ponto(AGORA - 14 * HORA + k * 20_000),
    );
    const t = await buscarTrilhaJanela(apiFalsa(fixes), "!n", 24, AGORA);
    expect(t.points).toHaveLength(2500);
    expect(t.points[0]).toEqual(fixes[0]);
  });

  it("dias fechados vêm do cache; só o dia corrente é buscado de novo", async () => {
    const api = apiFalsa([ponto(AGORA - 3 * DIA), ponto(AGORA - HORA)]);
    await buscarTrilhaJanela(api, "!n", 168, AGORA);
    const primeira = api.track.mock.calls.length;
    await buscarTrilhaJanela(api, "!n", 168, AGORA + 60_000);
    expect(api.track.mock.calls.length - primeira).toBe(1);
  });

  it("limparCacheTrilhas força nova busca de todos os dias", async () => {
    const api = apiFalsa([ponto(AGORA - HORA)]);
    await buscarTrilhaJanela(api, "!n", 72, AGORA);
    const primeira = api.track.mock.calls.length;
    limparCacheTrilhas();
    await buscarTrilhaJanela(api, "!n", 72, AGORA);
    expect(api.track.mock.calls.length).toBe(2 * primeira);
  });

  it("falha numa fatia não fica guardada no cache", async () => {
    let falhar = true;
    const track = vi.fn(async (_n: string, j?: JanelaTrack) => {
      if (falhar && j !== undefined && j.toMs < AGORA) {
        throw new Error("503");
      }
      return { line: null, lines: [], points: [] };
    });
    await expect(
      buscarTrilhaJanela({ track }, "!n", 24, AGORA),
    ).rejects.toThrow("503");
    falhar = false;
    await expect(
      buscarTrilhaJanela({ track }, "!n", 24, AGORA),
    ).resolves.toBeDefined();
  });

  it("fixes no mesmo segundo acima do teto: divisão termina e não repete fatias", async () => {
    // 2500 fixes num único segundo: nenhuma divisão fica abaixo do teto.
    const t0 = AGORA - 2 * HORA;
    const fixes = Array.from({ length: 2500 }, (_, k) =>
      ponto(t0, -70 + k * 1e-6),
    );
    const api = apiFalsa(fixes);
    await buscarTrilhaJanela(api, "!n", 24, AGORA);
    const janelas = api.track.mock.calls.map(([, j]) => j as JanelaTrack);
    // Metades disjuntas: fora da meia-noite, nenhuma janela começa onde outra termina.
    const bordas = new Set(janelas.map((j) => j.toMs));
    expect(
      janelas.filter((j) => j.fromMs % DIA !== 0 && bordas.has(j.fromMs)),
    ).toEqual([]);
    expect(api.track.mock.calls.length).toBeLessThan(80);
  });

  it("linhas cruas da primeira fatia (antes da janela) não entram", async () => {
    const track = vi.fn(async (_n: string, j?: JanelaTrack) => ({
      line: null,
      lines:
        j !== undefined && j.fromMs < AGORA - 24 * HORA
          ? [
              [
                [-70, -5],
                [-70.1, -5.1],
              ] as [number, number][],
            ]
          : [],
      points: [],
    }));
    const t = await buscarTrilhaJanela({ track }, "!n", 24, AGORA);
    expect(t.lines).toEqual([]);
  });

  it("dia fechado vence no cache e é buscado de novo", async () => {
    const api = apiFalsa([ponto(AGORA - 30 * HORA)]);
    await buscarTrilhaJanela(api, "!n", 72, AGORA);
    const primeira = api.track.mock.calls.length;
    await buscarTrilhaJanela(api, "!n", 72, AGORA + 21 * 60_000);
    expect(api.track.mock.calls.length).toBe(2 * primeira);
  });
});
