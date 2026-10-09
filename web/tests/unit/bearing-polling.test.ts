import { describe, expect, it } from "vitest";
import { atualizarBearingsDosNos } from "../../src/providers/DataProvider.js";
import { LocalState, type NodeInfo } from "../../src/store.js";

const GRAU_M = 111_195; // metros por grau (aprox.)
const LAT = -5;

const no = (nodeNum: number, dxM: number, dyM: number): NodeInfo => ({
  nodeNum,
  nodeId: `!${nodeNum.toString(16)}`,
  nome: "barco",
  posTime: null,
  battery: null,
  lon: -70 + dxM / GRAU_M,
  lat: LAT + dyM / GRAU_M,
});

/** Aplica uma sequência de posições como polls sucessivos; devolve o rumo final. */
const polls = (nodeNum: number, posicoes: [number, number][]) => {
  let anterior: Record<number, NodeInfo> = {};
  let atual: NodeInfo | undefined;
  for (const [dx, dy] of posicoes) {
    atual = no(nodeNum, dx, dy);
    atualizarBearingsDosNos([atual], anterior);
    anterior = { [nodeNum]: atual };
  }
  return atual?.bearing ?? null;
};

describe("rumo pelo polling de latest()", () => {
  it("jitter de GPS (até 60 m) entre polls não define rumo", () => {
    const jitter: [number, number][] = [
      [0, 0],
      [40, -30],
      [-50, 20],
      [30, 45],
      [-20, -55],
    ];
    expect(polls(9001, jitter)).toBeNull();
  });

  it("barco lento acumula deslocamento desde a âncora e ganha rumo", () => {
    // 40 m por poll para leste: nenhum passo isolado passa de 150 m
    const lento: [number, number][] = Array.from({ length: 6 }, (_, k) => [
      40 * k,
      0,
    ]);
    const rumo = polls(9002, lento);
    expect(rumo).not.toBeNull();
    expect(rumo as number).toBeCloseTo(90, 0);
  });

  it("nó parado mantém o rumo anterior mesmo com salto grande", () => {
    LocalState.setNodeMovimento(9003, {
      parado: true,
      desdeMs: null,
      velocidadeKmh: 0,
      ultimoFixMs: null,
    });
    const anterior = { ...no(9003, 0, 0), bearing: 45 };
    const atual = no(9003, 0, 400); // salto de 400 m para o norte
    atualizarBearingsDosNos([atual], { 9003: anterior });
    expect(atual.bearing).toBe(45);
  });
});
