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
