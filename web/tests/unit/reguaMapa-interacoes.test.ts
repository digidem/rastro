import type { Map as maplibregl } from "maplibre-gl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CAMADA_REGUA_VERTICES,
  type DepsInteracoesRegua,
  definirCursorRegua,
  definirZoomDuploRegua,
  instalarInteracoesRegua,
} from "../../src/lib/reguaMapa.js";

type Handler = (e: unknown) => void;
interface Registro {
  ev: string;
  camada: string | undefined;
  fn: Handler;
}

// Mapa falso: guarda handlers por evento (e camada, quando há) e expõe emit.
const criarMapaFalso = () => {
  const registros: Registro[] = [];
  const canvas = { style: { cursor: "" } };
  const dragPan = { enable: vi.fn(), disable: vi.fn() };
  const camadas = new Set(["nodes-circle", "nodes-boat"]);
  const feicoes: unknown[] = [];

  const separar = (a: unknown, b: unknown) =>
    typeof a === "string" && b !== undefined
      ? { camada: a, fn: b as Handler }
      : { camada: undefined, fn: a as Handler };

  const mapa = {
    dragPan,
    getCanvas: () => canvas,
    getLayer: vi.fn((id: string) => (camadas.has(id) ? {} : undefined)),
    queryRenderedFeatures: vi.fn(() => feicoes),
    on: vi.fn((ev: string, a: unknown, b?: unknown) => {
      const { camada, fn } = separar(a, b);
      registros.push({ ev, camada, fn });
    }),
    once: vi.fn(),
    off: vi.fn((ev: string, a: unknown, b?: unknown) => {
      const { camada, fn } = separar(a, b);
      const i = registros.findIndex(
        (r) => r.ev === ev && r.camada === camada && r.fn === fn,
      );
      if (i >= 0) {
        registros.splice(i, 1);
      }
    }),
  };

  // Dispara handlers de um evento; `camada` escolhe os de camada específica.
  const emit = (ev: string, e: unknown, camada?: string) => {
    for (const r of [...registros]) {
      if (r.ev === ev && r.camada === camada) {
        r.fn(e);
      }
    }
  };

  return {
    mapa: mapa as unknown as maplibregl,
    raw: mapa,
    registros,
    canvas,
    feicoes,
    camadas,
    emit,
  };
};

const evento = (x = 100, y = 120, lng = -70.0, lat = -5.0) => ({
  point: { x, y },
  lngLat: { lng, lat },
  preventDefault: vi.fn(),
});

const nodeNumDe = (props: Record<string, unknown>) =>
  typeof props.nodeNum === "number" ? props.nodeNum : null;

let ativa: boolean;
let depsPadrao: DepsInteracoesRegua;
let limpar: (() => void) | undefined;
let fake: ReturnType<typeof criarMapaFalso>;

const instalar = (overrides: Partial<DepsInteracoesRegua> = {}) => {
  limpar = instalarInteracoesRegua(fake.mapa, {
    ...depsPadrao,
    ...overrides,
  });
};

beforeEach(() => {
  ativa = true;
  fake = criarMapaFalso();
  depsPadrao = {
    ativa: () => ativa,
    camadasNos: ["nodes-circle", "nodes-boat"],
    nodeNumDe,
    adicionar: vi.fn(),
    mover: vi.fn(),
    concluir: vi.fn(),
    sair: vi.fn(),
  };
});

afterEach(() => {
  limpar?.();
  limpar = undefined;
});

describe("instalarInteracoesRegua — clique", () => {
  it("clique em área livre adiciona ponto livre", () => {
    instalar();
    fake.emit("click", evento(100, 120, -70.1, -5.2));
    expect(depsPadrao.adicionar).toHaveBeenCalledWith({
      tipo: "livre",
      lon: -70.1,
      lat: -5.2,
    });
  });

  it("clique perto de nó adiciona vértice ancorado e consulta caixa de ±10 px", () => {
    fake.feicoes.push({ properties: { nodeNum: 7 } });
    instalar();
    fake.emit("click", evento(100, 120));
    expect(depsPadrao.adicionar).toHaveBeenCalledWith({
      tipo: "no",
      nodeNum: 7,
    });
    expect(fake.raw.queryRenderedFeatures).toHaveBeenCalledWith(
      [
        [90, 110],
        [110, 130],
      ],
      { layers: ["nodes-circle", "nodes-boat"] },
    );
  });

  it("feição sem nodeNum válido cai no ponto livre", () => {
    fake.feicoes.push({ properties: { nome: "sem número" } });
    instalar();
    fake.emit("click", evento());
    expect(depsPadrao.adicionar).toHaveBeenCalledWith(
      expect.objectContaining({ tipo: "livre" }),
    );
  });

  it("sem camadas de nó no estilo, não consulta e adiciona ponto livre", () => {
    fake.camadas.clear();
    instalar();
    fake.emit("click", evento());
    expect(fake.raw.queryRenderedFeatures).not.toHaveBeenCalled();
    expect(depsPadrao.adicionar).toHaveBeenCalledWith(
      expect.objectContaining({ tipo: "livre" }),
    );
  });

  it("com régua inativa, clique não faz nada", () => {
    ativa = false;
    instalar();
    fake.emit("click", evento());
    expect(depsPadrao.adicionar).not.toHaveBeenCalled();
  });
});

describe("instalarInteracoesRegua — duplo clique", () => {
  it("dblclick conclui e cancela o zoom", () => {
    instalar();
    const e = evento();
    fake.emit("dblclick", e);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(depsPadrao.concluir).toHaveBeenCalledTimes(1);
  });

  it("dblclick com régua inativa não conclui nem cancela zoom", () => {
    ativa = false;
    instalar();
    const e = evento();
    fake.emit("dblclick", e);
    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(depsPadrao.concluir).not.toHaveBeenCalled();
  });
});

describe("instalarInteracoesRegua — arrasto", () => {
  const pressionar = (indice: number) => {
    const e = { ...evento(), features: [{ properties: { indice } }] };
    fake.emit("mousedown", e, CAMADA_REGUA_VERTICES);
    fake.emit("mousedown", e);
    return e;
  };

  it("arrastar chama mover, reabilita dragPan e o clique seguinte é ignorado", () => {
    instalar();
    const e = pressionar(1);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(fake.raw.dragPan.disable).toHaveBeenCalled();

    fake.emit("mousemove", evento(0, 0, -70.2, -5.3));
    expect(depsPadrao.mover).toHaveBeenCalledWith(1, -70.2, -5.3);

    fake.emit("mouseup", evento());
    expect(fake.raw.dragPan.enable).toHaveBeenCalled();

    fake.emit("click", evento());
    expect(depsPadrao.adicionar).not.toHaveBeenCalled();

    // Depois do clique ignorado, o próximo volta a adicionar vértice
    fake.emit("click", evento());
    expect(depsPadrao.adicionar).toHaveBeenCalledTimes(1);
  });

  it("soltar fora do canvas (mouseup na janela) encerra o arraste", () => {
    instalar();
    pressionar(1);
    window.dispatchEvent(new MouseEvent("mouseup"));
    expect(fake.raw.dragPan.enable).toHaveBeenCalledTimes(1);
    fake.emit("mousemove", evento(0, 0, -70.2, -5.3));
    expect(depsPadrao.mover).not.toHaveBeenCalled();
  });

  it("touchcancel encerra o arraste", () => {
    instalar();
    pressionar(0);
    fake.emit("touchcancel", {});
    expect(fake.raw.dragPan.enable).toHaveBeenCalledTimes(1);
  });

  it("Escape no meio do arraste devolve o dragPan", () => {
    instalar();
    pressionar(0);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(depsPadrao.sair).toHaveBeenCalled();
    expect(fake.raw.dragPan.enable).toHaveBeenCalledTimes(1);
  });

  it("régua desligada no meio do arraste: o próximo movimento encerra", () => {
    instalar();
    pressionar(0);
    ativa = false;
    fake.emit("mousemove", evento(0, 0, -70.2, -5.3));
    expect(depsPadrao.mover).not.toHaveBeenCalled();
    expect(fake.raw.dragPan.enable).toHaveBeenCalledTimes(1);
  });

  it("mousemove sem arraste em curso não chama mover", () => {
    instalar();
    fake.emit("mousemove", evento());
    expect(depsPadrao.mover).not.toHaveBeenCalled();
  });

  it("arrasto sem movimento não impede o clique seguinte", () => {
    instalar();
    pressionar(0);
    fake.emit("mouseup", evento());
    fake.emit("click", evento());
    expect(depsPadrao.adicionar).toHaveBeenCalledTimes(1);
  });

  it("touch: touchstart + touchmove chama mover com o índice do vértice", () => {
    instalar();
    const e = { ...evento(), features: [{ properties: { indice: 2 } }] };
    fake.emit("touchstart", e, CAMADA_REGUA_VERTICES);
    fake.emit("touchmove", evento(0, 0, -70.4, -5.4));
    expect(depsPadrao.mover).toHaveBeenCalledWith(2, -70.4, -5.4);
    fake.emit("touchend", evento());
    expect(fake.raw.dragPan.enable).toHaveBeenCalled();
  });

  it("pressionar vértice com régua inativa não arrasta", () => {
    ativa = false;
    instalar();
    const e = pressionar(1);
    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(fake.raw.dragPan.disable).not.toHaveBeenCalled();
  });
});

describe("instalarInteracoesRegua — Escape", () => {
  it("Escape com régua ativa chama sair; outra tecla não", () => {
    instalar();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(depsPadrao.sair).not.toHaveBeenCalled();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(depsPadrao.sair).toHaveBeenCalledTimes(1);
  });

  it("Escape com régua inativa não chama sair", () => {
    ativa = false;
    instalar();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(depsPadrao.sair).not.toHaveBeenCalled();
  });
});

describe("instalarInteracoesRegua — limpeza", () => {
  it("remove todos os handlers e o listener de teclado", () => {
    instalar();
    expect(fake.registros.length).toBeGreaterThan(0);
    limpar?.();
    limpar = undefined;
    expect(fake.registros).toHaveLength(0);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(depsPadrao.sair).not.toHaveBeenCalled();
  });

  it("limpeza no meio de um arraste devolve o dragPan", () => {
    instalar();
    fake.emit(
      "mousedown",
      { ...evento(), features: [{ properties: { indice: 0 } }] },
      CAMADA_REGUA_VERTICES,
    );
    limpar?.();
    limpar = undefined;
    expect(fake.raw.dragPan.enable).toHaveBeenCalled();
  });
});

describe("cursor", () => {
  it("definirCursorRegua aplica crosshair ou vazio", () => {
    definirCursorRegua(fake.mapa, true);
    expect(fake.canvas.style.cursor).toBe("crosshair");
    definirCursorRegua(fake.mapa, false);
    expect(fake.canvas.style.cursor).toBe("");
  });

  it("definirZoomDuploRegua desliga e religa o zoom de duplo clique/toque", () => {
    const doubleClickZoom = { enable: vi.fn(), disable: vi.fn() };
    const mapa = { doubleClickZoom } as unknown as maplibregl;
    definirZoomDuploRegua(mapa, true);
    expect(doubleClickZoom.disable).toHaveBeenCalledTimes(1);
    definirZoomDuploRegua(mapa, false);
    expect(doubleClickZoom.enable).toHaveBeenCalledTimes(1);
  });

  it("sobre um vértice vira move; ao sair volta a crosshair com régua ativa", () => {
    instalar();
    fake.emit("mouseenter", {}, CAMADA_REGUA_VERTICES);
    expect(fake.canvas.style.cursor).toBe("move");
    fake.emit("mouseleave", {}, CAMADA_REGUA_VERTICES);
    expect(fake.canvas.style.cursor).toBe("crosshair");
  });

  it("com régua inativa, entrar em vértice não muda o cursor", () => {
    ativa = false;
    instalar();
    fake.emit("mouseenter", {}, CAMADA_REGUA_VERTICES);
    expect(fake.canvas.style.cursor).toBe("");
  });
});
