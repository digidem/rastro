import { cleanup, render } from "@solidjs/testing-library";
import { createComponent } from "solid-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DataProvider,
  type DataValue,
  useData,
} from "../../src/providers/DataProvider.jsx";
import { LocalState, type NodeInfo } from "../../src/store.js";

// Filtros/seleção vivem no store; o clearSessionData (sessão) vive no provider.
// Mockamos a API para montar o provider sem rede.
const { latestMock } = vi.hoisted(() => ({ latestMock: vi.fn() }));

vi.mock("../../src/providers/api.js", () => ({
  // biome-ignore lint/style/useNamingConvention: nomes devem casar com os exports originais
  ErrTokenInvalid: class ErrTokenInvalid extends Error {},
  // biome-ignore lint/style/useNamingConvention: nomes devem casar com os exports originais
  ErrOffline: class ErrOffline extends Error {},
  createApiClient: () => ({ latest: latestMock }),
}));

const no = (nodeNum: number): NodeInfo => ({
  nodeNum,
  nodeId: `!abcdef${nodeNum}`,
  nome: `Nó ${nodeNum}`,
  posTime: new Date(0).toISOString(),
  battery: 90,
  lon: -70.3,
  lat: -4.5,
});

let provider: DataValue | undefined;

const Consumidor = () => {
  provider = useData();
  return "";
};

const montarProvider = () =>
  render(() =>
    createComponent(DataProvider, {
      get children() {
        return createComponent(Consumidor, {});
      },
    }),
  );

beforeEach(() => {
  latestMock.mockReset();
  LocalState.setNodes([]);
  LocalState.setOnline(false);
  LocalState.setAuth("ok");
  LocalState.resetViewerState();
});

afterEach(() => {
  cleanup();
});

describe("LocalState — filtros", () => {
  it("setQuery/setKindFilter/setConditionFilter atualizam o store", () => {
    LocalState.setQuery("itq1");
    LocalState.setKindFilter("boat");
    LocalState.setConditionFilter("stale");

    expect(LocalState.localState.query).toBe("itq1");
    expect(LocalState.localState.kindFilter).toBe("boat");
    expect(LocalState.localState.conditionFilter).toBe("stale");
  });

  it("resetFilters limpa só os filtros e preserva seleção e status", () => {
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    LocalState.setLatestStatus("ready");
    LocalState.setQuery("base");
    LocalState.setKindFilter("fixed_station");
    LocalState.setConditionFilter("no-position");

    LocalState.resetFilters();

    expect(LocalState.localState.query).toBe("");
    expect(LocalState.localState.kindFilter).toBe("all");
    expect(LocalState.localState.conditionFilter).toBe("all");
    expect(LocalState.localState.selected).toBe(1);
    expect(LocalState.localState.latestStatus).toBe("ready");
  });
});

describe("LocalState — reset de sessão", () => {
  it("resetViewerState limpa filtros, status e seleção", () => {
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    LocalState.setLatestStatus("ready");
    LocalState.setQuery("base");
    LocalState.setKindFilter("boat");
    LocalState.setConditionFilter("stale");

    LocalState.resetViewerState();

    expect(LocalState.localState.query).toBe("");
    expect(LocalState.localState.kindFilter).toBe("all");
    expect(LocalState.localState.conditionFilter).toBe("all");
    expect(LocalState.localState.latestStatus).toBe("idle");
    expect(LocalState.localState.selected).toBeNull();
  });

  it("resetViewerState esquece o estado de parada da sessão antiga", () => {
    LocalState.setNodeMovimento(1, {
      parado: true,
      desdeMs: 0,
      velocidadeKmh: 0,
      ultimoFixMs: Date.now(),
    });
    LocalState.resetViewerState();
    expect(LocalState.localState.movimento[1]).toBeUndefined();
  });

  it("clearSessionData do provider zera a visualização da sessão antiga", () => {
    montarProvider();
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    LocalState.setLatestStatus("ready");
    LocalState.setQuery("barco");
    LocalState.setKindFilter("boat");
    LocalState.setConditionFilter("stale");

    provider?.clearSessionData();

    expect(LocalState.localState.nodes).toEqual({});
    expect(LocalState.localState.selected).toBeNull();
    expect(LocalState.localState.query).toBe("");
    expect(LocalState.localState.kindFilter).toBe("all");
    expect(LocalState.localState.conditionFilter).toBe("all");
    expect(LocalState.localState.latestStatus).toBe("idle");
  });
});

describe("LocalState — seleção e inativos", () => {
  it("select alterna entre nó e null", () => {
    LocalState.select(42);
    expect(LocalState.localState.selected).toBe(42);

    LocalState.select(null);
    expect(LocalState.localState.selected).toBeNull();
  });

  it("setShowInactive e toggleShowInactive alternam flag de nós inativos", () => {
    expect(LocalState.localState.showInactive).toBe(false);

    LocalState.toggleShowInactive();
    expect(LocalState.localState.showInactive).toBe(true);

    LocalState.setShowInactive(false);
    expect(LocalState.localState.showInactive).toBe(false);
  });
});

describe("LocalState — fixes brutos (T5)", () => {
  const chaveFixes = "rastro_fixes_brutos";

  // Store recarregado do zero: o valor inicial vem do localStorage do momento.
  const carregarStore = async () => {
    vi.resetModules();
    return (await import("../../src/store.js")).LocalState;
  };

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("padrão é false quando não há nada salvo", async () => {
    window.localStorage.clear();
    const store = await carregarStore();
    expect(store.localState.mostrarFixesBrutos).toBe(false);
    expect(store.localState.trilhaCarregada).toBe(false);
  });

  it("setMostrarFixesBrutos persiste e relê o valor ao carregar", async () => {
    const store = await carregarStore();
    store.setMostrarFixesBrutos(true);
    expect(store.localState.mostrarFixesBrutos).toBe(true);
    expect(window.localStorage.getItem(chaveFixes)).toBe("1");

    const outro = await carregarStore();
    expect(outro.localState.mostrarFixesBrutos).toBe(true);

    outro.setMostrarFixesBrutos(false);
    expect(window.localStorage.getItem(chaveFixes)).toBe("0");
  });

  it("leitura do localStorage que lança cai no padrão false", async () => {
    // Só a chave do toggle falha: o basemap (outro loader) não tem try/catch.
    const lerOriginal = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
      this: Storage,
      chave: string,
    ) {
      if (chave === chaveFixes) {
        throw new Error("SecurityError");
      }
      return lerOriginal.call(this, chave);
    });
    const store = await carregarStore();
    expect(store.localState.mostrarFixesBrutos).toBe(false);
  });

  it("escrita no localStorage que lança não quebra o estado", async () => {
    const store = await carregarStore();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => store.setMostrarFixesBrutos(true)).not.toThrow();
    expect(store.localState.mostrarFixesBrutos).toBe(true);
  });

  it("resetViewerState desliga trilhaCarregada e mantém a preferência", () => {
    LocalState.setMostrarFixesBrutos(true);
    LocalState.setTrilhaCarregada(true);

    LocalState.resetViewerState();

    expect(LocalState.localState.trilhaCarregada).toBe(false);
    expect(LocalState.localState.mostrarFixesBrutos).toBe(true);
    LocalState.setMostrarFixesBrutos(false);
  });
});
