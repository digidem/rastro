import { cleanup, render } from "@solidjs/testing-library";
import type { Component } from "solid-js";
import { createComponent, onMount } from "solid-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InitializeMap } from "../../src/InitializeMap.jsx";
import { useMap } from "../../src/hooks/useMap.jsx";
import {
  DataContext,
  type DataValue,
} from "../../src/providers/DataProvider.jsx";
import { LocalState } from "../../src/store.js";

// InitializeMap importa maplibre-gl (Map/Popup/addProtocol) e pmtiles
// (Protocol) no escopo do módulo; mockamos tudo — o teste cobre a lógica de
// reatividade (geração de sessão × trilha/popup), não o MapLibre de verdade.
const { instancias, popups } = vi.hoisted(() => ({
  instancias: [] as unknown[],
  popups: [] as unknown[],
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    handlers = new Map<string, Array<(e: unknown) => void>>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    flyTo = vi.fn();
    getZoom = vi.fn(() => 7);
    remove = vi.fn();

    constructor(_opts: unknown) {
      instancias.push(this);
      for (const nome of ["nodes", "track", "track-points"]) {
        this.sources.set(nome, { setData: vi.fn() });
      }
    }

    on(ev: string, camadaOuCb: unknown, cb?: (e: unknown) => void) {
      // map.on pode vir com layer ("click", "nodes-circle", fn) ou sem.
      const handler = (cb ?? camadaOuCb) as (e: unknown) => void;
      const lista = this.handlers.get(ev) ?? [];
      lista.push(handler);
      this.handlers.set(ev, lista);
      // Carrega no mesmo instante: efeitos dos sources ficam testáveis.
      if (ev === "load") {
        handler(undefined);
      }
    }

    emit(ev: string, e: unknown) {
      for (const cb of this.handlers.get(ev) ?? []) {
        cb(e);
      }
    }

    getSource(nome: string) {
      return this.sources.get(nome);
    }

    addSource = vi.fn((nome: string, _def: unknown) => {
      this.sources.set(nome, { setData: vi.fn() });
    });
    addLayer = vi.fn();
    getLayer = vi.fn(() => ({}));
    setLayoutProperty = vi.fn();
    once(ev: string, cb: (e: unknown) => void) {
      this.on(ev, cb);
    }
  }

  class FakePopup {
    remove = vi.fn();
    setLngLat() {
      return this;
    }
    // biome-ignore lint/style/useNamingConvention: nome do método da API do MapLibre
    setHTML() {
      return this;
    }
    addTo() {
      return this;
    }
    constructor(_opts: unknown) {
      popups.push(this);
    }
  }

  return {
    // biome-ignore lint/style/useNamingConvention: nome do export do maplibre-gl
    Map: FakeMap,
    // biome-ignore lint/style/useNamingConvention: nome do export do maplibre-gl
    Popup: FakePopup,
    addProtocol: vi.fn(),
  };
});

vi.mock("pmtiles", () => ({
  // biome-ignore lint/style/useNamingConvention: nome do export do pmtiles
  Protocol: class {
    tile = vi.fn();
  },
}));

type FakeSource = { setData: ReturnType<typeof vi.fn> };

interface FakeMapLike {
  addSource: ReturnType<typeof vi.fn>;
  addLayer: ReturnType<typeof vi.fn>;
  setLayoutProperty: ReturnType<typeof vi.fn>;
  handlers: Map<string, Array<(e: unknown) => void>>;
  sources: Map<string, FakeSource>;
  flyTo: ReturnType<typeof vi.fn>;
  emit(ev: string, e: unknown): void;
  getSource(nome: string): FakeSource;
}

interface FakePopupLike {
  remove: ReturnType<typeof vi.fn>;
}

const mapa = () => instancias.at(-1) as FakeMapLike;
const popupAberto = () => popups[0] as FakePopupLike;

// nó de teste: shape do NodeInfo do contrato do store.
const no = (nodeNum: number) => ({
  nodeNum,
  nodeId: `!abcdef${nodeNum}`,
  nome: `Nó ${nodeNum}`,
  posTime: new Date(0).toISOString(),
  battery: 90,
  lon: -30.02,
  lat: -4.22,
});

// Consumidor mínimo que reproduce o papel do MapWindow: seta o container e
// chama initializeMap ao montar. (Sem JSX — o arquivo é .ts; o ref é setado
// programaticamente porque o Map do teste ignora o container.)
const Prova: Component = () => {
  const { setMapRef, initializeMap } = useMap();
  onMount(() => {
    setMapRef(document.createElement("div"));
    initializeMap();
  });
  return "prova";
};

const montar = (api: DataValue["api"]) =>
  render(() =>
    createComponent(DataContext.Provider, {
      value: {
        api,
        startPolling: vi.fn(),
        stopPolling: vi.fn(),
        clearSessionData: vi.fn(),
        startSession: vi.fn(),
      },
      get children() {
        return createComponent(InitializeMap, {
          get children() {
            return createComponent(Prova, {});
          },
        });
      },
    }),
  );

beforeEach(() => {
  instancias.length = 0;
  popups.length = 0;
  LocalState.setNodes([]);
  LocalState.select(null);
});

afterEach(() => {
  cleanup();
});

describe("InitializeMap — trilha", () => {
  it("seleção busca trilha e desenha LineString + pontos quando a resposta chega", async () => {
    const { promise, resolve } = Promise.withResolvers<unknown>();
    const api = {
      track: vi.fn().mockReturnValue(promise),
    } as unknown as DataValue["api"];
    montar(api);
    // Assenta os efeitos de mount (load/geração) antes de selecionar.
    await new Promise((r) => setTimeout(r, 0));

    LocalState.setNodes([no(1)]);
    LocalState.select(1);

    expect(mapa().flyTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [-30.02, -4.22] }),
    );
    expect(api.track).toHaveBeenCalledWith("!abcdef1");

    resolve({
      line: [
        [-30.02, -4.22],
        [-30.03, -4.23],
      ],
      points: [{ pos: [-30.02, -4.22], posTime: "t", sats: 9 }],
    });
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }

    const trilha = mapa().getSource("track").setData;
    expect(trilha).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "FeatureCollection",
        features: [
          expect.objectContaining({
            geometry: expect.objectContaining({ type: "LineString" }),
          }),
        ],
      }),
    );
    expect(mapa().getSource("track-points").setData).toHaveBeenCalledWith(
      expect.objectContaining({
        features: [expect.anything()],
      }),
    );
  });

  it("resposta de trilha atrasada cai fora quando a geração avança (logout)", async () => {
    const { promise, resolve } = Promise.withResolvers<unknown>();
    const api = {
      track: vi.fn().mockReturnValue(promise),
    } as unknown as DataValue["api"];
    montar(api);
    // Assenta os efeitos de mount (load/geração) antes de selecionar.
    await new Promise((r) => setTimeout(r, 0));

    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    expect(api.track).toHaveBeenCalledOnce();

    // Logout no meio do fetch: clearSessionData avança a geração.
    LocalState.bumpPollingGeracao();
    expect(mapa().getSource("track").setData).toHaveBeenLastCalledWith(
      expect.objectContaining({ features: [] }),
    );

    // A resposta em voo resolve tarde: trilha não pode voltar.
    resolve({
      line: [
        [-30.02, -4.22],
        [-30.03, -4.23],
      ],
      points: [{ pos: [-30.02, -4.22], posTime: "t", sats: 9 }],
    });
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }

    expect(mapa().getSource("track").setData).toHaveBeenLastCalledWith(
      expect.objectContaining({ features: [] }),
    );
    expect(mapa().getSource("track-points").setData).toHaveBeenLastCalledWith(
      expect.objectContaining({ features: [] }),
    );
  });
});

describe("InitializeMap — popup", () => {
  it("abre no clique do pin e fecha quando a geração avança", async () => {
    const api = { track: vi.fn() } as unknown as DataValue["api"];
    montar(api);
    // Assenta os efeitos de mount (load/geração) antes do clique.
    await new Promise((r) => setTimeout(r, 0));

    // Clique num nó (mesma shape que o handler espera do MapLibre).
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: [-30.02, -4.22] },
          properties: { nome: "Base A", posTime: "2026-09-27T12:00:00Z" },
        },
      ],
    });
    expect(popups).toHaveLength(1);

    // Logout: a geração avança e o popup da sessão velha fecha.
    LocalState.bumpPollingGeracao();
    expect(popupAberto().remove).toHaveBeenCalledOnce();
  });
});

describe("InitializeMap — basemap padrão OSM", () => {
  const prepara = () => {
    montar({} as DataValue["api"]);
    return mapa();
  };

  it("sem o basemap próprio (erro na source basemap) troca para OSM via API", () => {
    const m = prepara();
    m.emit("error", { sourceId: "basemap", error: new Error("404") });
    expect(m.addSource).toHaveBeenCalledTimes(1);
    const [nome, def] = m.addSource.mock.calls[0] as [
      string,
      { type: string; tiles: string[] },
    ];
    expect(nome).toBe("osm");
    expect(def.type).toBe("raster");
    // mesma origem, pela API (token) — nunca direto no servidor do OSM
    expect(def.tiles[0]).toBe(
      `${window.location.origin}/api/osm/{z}/{x}/{y}.png`,
    );
    expect(m.addLayer).toHaveBeenCalledWith(
      { id: "osm-base", type: "raster", source: "osm" },
      "track-line",
    );
    for (const id of ["landcover", "water", "waterway", "boundary"]) {
      expect(m.setLayoutProperty).toHaveBeenCalledWith(id, "visibility", "none");
    }
  });

  it("é idempotente e ignora erros de outras sources", () => {
    const m = prepara();
    m.emit("error", { sourceId: "nodes" });
    expect(m.addSource).not.toHaveBeenCalled();
    m.emit("error", { sourceId: "basemap" });
    m.emit("error", { sourceId: "basemap" });
    expect(m.addSource).toHaveBeenCalledTimes(1);
  });
});
