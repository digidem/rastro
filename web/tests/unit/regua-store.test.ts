import { afterEach, describe, expect, it } from "vitest";
import { resolverPontosRegua } from "../../src/lib/regua.js";
import { LocalState, type NodeInfo, type PontoRegua } from "../../src/store.js";

const no = (nodeNum: number, lon: number, lat: number): NodeInfo => ({
  nodeNum,
  nodeId: `!${nodeNum.toString(16)}`,
  nome: `nó ${nodeNum}`,
  posTime: null,
  battery: null,
  lon,
  lat,
});

const livre = (lon: number, lat: number): PontoRegua => ({
  tipo: "livre",
  lon,
  lat,
});

const pontos = () => LocalState.localState.regua.pontos;

afterEach(() => {
  LocalState.desativarRegua();
  LocalState.setNodes([]);
});

describe("estado da régua no store", () => {
  it("começa desligada, sem pontos e sem concluir", () => {
    const r = LocalState.localState.regua;
    expect(r.ativa).toBe(false);
    expect(r.concluida).toBe(false);
    expect(r.pontos).toEqual([]);
  });

  it("ativarRegua() liga sem pontos", () => {
    LocalState.ativarRegua();
    const r = LocalState.localState.regua;
    expect(r.ativa).toBe(true);
    expect(r.concluida).toBe(false);
    expect(r.pontos).toEqual([]);
  });

  it("ativarRegua(nodeNum) liga com o vértice 0 ancorado no nó", () => {
    LocalState.ativarRegua(7);
    expect(pontos()).toEqual([{ tipo: "no", nodeNum: 7 }]);
    expect(LocalState.localState.regua.ativa).toBe(true);
  });

  it("desativarRegua() volta ao valor inicial", () => {
    LocalState.ativarRegua(7);
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.concluirRegua();
    LocalState.desativarRegua();
    const r = LocalState.localState.regua;
    expect(r).toEqual({ ativa: false, concluida: false, pontos: [] });
  });

  it("adicionarPontoRegua não faz nada com a régua desligada", () => {
    LocalState.adicionarPontoRegua(livre(-70, -5));
    expect(pontos()).toEqual([]);
  });

  it("adicionarPontoRegua acrescenta vértices na ordem", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    LocalState.adicionarPontoRegua(livre(-70.1, -5.1));
    expect(pontos()).toEqual([
      { tipo: "livre", lon: -70, lat: -5 },
      { tipo: "no", nodeNum: 3 },
      { tipo: "livre", lon: -70.1, lat: -5.1 },
    ]);
  });

  it("ignora clique igual ao último vértice (nó repetido)", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    expect(pontos()).toEqual([{ tipo: "no", nodeNum: 3 }]);
  });

  it("ignora clique livre com lon/lat idênticos ao último", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua(livre(-70, -5));
    expect(pontos()).toEqual([livre(-70, -5)]);
  });

  it("aceita repetição que não é a último vértice", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    expect(pontos()).toHaveLength(3);
  });

  it("clique depois de concluída começa caminho novo", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua(livre(-70.1, -5));
    LocalState.concluirRegua();
    expect(LocalState.localState.regua.concluida).toBe(true);

    LocalState.adicionarPontoRegua(livre(-70.2, -5.2));
    const r = LocalState.localState.regua;
    expect(r.concluida).toBe(false);
    expect(r.pontos).toEqual([livre(-70.2, -5.2)]);
  });

  it("moverPontoRegua troca o vértice por ponto livre (desancora nó)", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "no", nodeNum: 3 });
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.moverPontoRegua(0, -70.5, -5.5);
    expect(pontos()).toEqual([
      { tipo: "livre", lon: -70.5, lat: -5.5 },
      { tipo: "livre", lon: -70, lat: -5 },
    ]);
  });

  it("moverPontoRegua com índice inválido não altera nada", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.moverPontoRegua(5, -71, -6);
    LocalState.moverPontoRegua(-1, -71, -6);
    expect(pontos()).toEqual([livre(-70, -5)]);
  });

  it("desfazerPontoRegua remove o último e reabre o caminho", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua(livre(-70.1, -5));
    LocalState.concluirRegua();
    LocalState.desfazerPontoRegua();
    const r = LocalState.localState.regua;
    expect(r.pontos).toEqual([livre(-70, -5)]);
    expect(r.concluida).toBe(false);
  });

  it("desfazerPontoRegua sem pontos não quebra", () => {
    LocalState.ativarRegua();
    LocalState.desfazerPontoRegua();
    expect(pontos()).toEqual([]);
  });

  it("concluirRegua só fecha com pelo menos dois pontos", () => {
    LocalState.ativarRegua();
    LocalState.concluirRegua();
    expect(LocalState.localState.regua.concluida).toBe(false);

    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.concluirRegua();
    expect(LocalState.localState.regua.concluida).toBe(false);

    LocalState.adicionarPontoRegua(livre(-70.1, -5));
    LocalState.concluirRegua();
    expect(LocalState.localState.regua.concluida).toBe(true);
  });

  it("limparRegua apaga os pontos e segue ligada", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.adicionarPontoRegua(livre(-70.1, -5));
    LocalState.concluirRegua();
    LocalState.limparRegua();
    const r = LocalState.localState.regua;
    expect(r.ativa).toBe(true);
    expect(r.concluida).toBe(false);
    expect(r.pontos).toEqual([]);
  });

  it("resetViewerState desliga a régua e apaga a medição", () => {
    LocalState.ativarRegua(7);
    LocalState.adicionarPontoRegua(livre(-70, -5));
    LocalState.resetViewerState();
    const r = LocalState.localState.regua;
    expect(r).toEqual({ ativa: false, concluida: false, pontos: [] });
  });
});

describe("resolverPontosRegua", () => {
  it("resolve vértice livre e de nó com nome", () => {
    LocalState.setNodes([no(1, -70.01, -5.01)]);
    const resolvidos = resolverPontosRegua(
      [livre(-70, -5), { tipo: "no", nodeNum: 1 }],
      LocalState.localState.nodes,
    );
    expect(resolvidos).toEqual([
      { pos: [-70, -5], nome: null, nodeNum: null, indice: 0 },
      { pos: [-70.01, -5.01], nome: "nó 1", nodeNum: 1, indice: 1 },
    ]);
  });

  it("descarta vértice de nó ausente e mantém o índice original", () => {
    LocalState.setNodes([no(1, -70.01, -5.01)]);
    const resolvidos = resolverPontosRegua(
      [{ tipo: "no", nodeNum: 99 }, livre(-70, -5), { tipo: "no", nodeNum: 1 }],
      LocalState.localState.nodes,
    );
    expect(resolvidos.map((v) => v.indice)).toEqual([1, 2]);
  });

  it("acompanha um nó que muda de posição entre duas rodadas de polling", () => {
    const pontosRegua: PontoRegua[] = [{ tipo: "no", nodeNum: 1 }];

    LocalState.setNodes([no(1, -70.01, -5.01)]);
    const antes = resolverPontosRegua(pontosRegua, LocalState.localState.nodes);
    expect(antes[0]?.pos).toEqual([-70.01, -5.01]);

    LocalState.setNodes([no(1, -70.02, -5.03)]);
    const depois = resolverPontosRegua(
      pontosRegua,
      LocalState.localState.nodes,
    );
    expect(depois[0]?.pos).toEqual([-70.02, -5.03]);
  });

  it("descarta nó com lon/lat não finitos", () => {
    const resolvidos = resolverPontosRegua([{ tipo: "no", nodeNum: 2 }], {
      2: { lon: Number.NaN, lat: -5, nome: "x" },
    });
    expect(resolvidos).toEqual([]);
  });
});
