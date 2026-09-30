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
    fitBounds = vi.fn();
    getZoom = vi.fn(() => 7);
    remove = vi.fn();
    canvas = { style: { cursor: "" } };
    getCanvas = vi.fn(() => this.canvas);

    opts: unknown;
    constructor(_opts: unknown) {
      this.opts = _opts;
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
      if (ev === "load" || ev === "style.load") {
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
    html = "";
    remove = vi.fn();
    setLngLat() {
      return this;
    }
    // biome-ignore lint/style/useNamingConvention: nome do método da API do MapLibre
    setHTML(h: string) {
      this.html = h;
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
  fitBounds: ReturnType<typeof vi.fn>;
  emit(ev: string, e: unknown): void;
  getSource(nome: string): FakeSource;
  getCanvas(): { style: { cursor: string } };
}

interface FakePopupLike {
  html: string;
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
          properties: {
            nome: "Base A",
            hwModel: "HELTEC_V4",
            posTime: "2026-09-27T12:00:00Z",
          },
        },
      ],
    });
    expect(popups).toHaveLength(1);
    expect(popupAberto().html).toContain("/devices/heltec_v4.svg");
    expect(popupAberto().html).toContain("Base A");
    expect(popupAberto().html).toContain("HELTEC_V4");

    // Logout: a geração avança e o popup da sessão velha fecha.
    LocalState.bumpPollingGeracao();
    expect(popupAberto().remove).toHaveBeenCalledOnce();
  });

  it("muda cursor para pointer ao passar sobre o pin", async () => {
    const api = { track: vi.fn() } as unknown as DataValue["api"];
    montar(api);
    await new Promise((r) => setTimeout(r, 0));

    mapa().emit("mouseenter", {});
    expect(mapa().getCanvas().style.cursor).toBe("pointer");

    mapa().emit("mouseleave", {});
    expect(mapa().getCanvas().style.cursor).toBe("");
  });
});

describe("InitializeMap — basemap padrão OSM", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const prepara = (headOk: boolean) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: headOk })),
    );
    montar({} as DataValue["api"]);
    return mapa();
  };

  it("o estilo já nasce com o OSM (via API) e SEM source pmtiles que possa falhar", () => {
    const m = prepara(false);
    const estilo = (
      m.opts as {
        style: { sources: Record<string, unknown>; layers: { id: string }[] };
      }
    ).style;
    expect(Object.keys(estilo.sources)).toContain("osm");
    expect(Object.keys(estilo.sources)).not.toContain("basemap");
    expect(estilo.layers.map((l) => l.id)).toContain("osm-base");
    const osm = estilo.sources.osm as { tiles: string[] };
    // mesma origem, pela API (token) — nunca direto no servidor do OSM
    expect(osm.tiles[0]).toBe(
      `${window.location.origin}/api/osm/{z}/{x}/{y}.png`,
    );
  });

  it("sem basemap próprio (HEAD falha) mantém o OSM e não adiciona pmtiles", async () => {
    const m = prepara(false);
    await new Promise((r) => setTimeout(r, 10));
    expect(m.addSource).not.toHaveBeenCalled();
  });

  it("com basemap próprio (HEAD ok) adiciona o pmtiles e esconde o OSM", async () => {
    const m = prepara(true);
    await vi.waitFor(() => expect(m.addSource).toHaveBeenCalledTimes(1));
    const [nome, def] = m.addSource.mock.calls[0] as [string, { type: string }];
    expect(nome).toBe("basemap");
    expect(def.type).toBe("vector");
    expect(m.addLayer).toHaveBeenCalledTimes(4);
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "osm-base",
      "visibility",
      "none",
    );
  });

  it("style.load e load juntos inicializam uma vez só (um handler de clique)", () => {
    const m = prepara(false);
    expect(m.handlers.get("click")?.length).toBe(1);
  });

  it("camada nodes-boat rotaciona os barcos com o rumo e alinha ao mapa", () => {
    const m = prepara(false);
    const estilo = (
      m.opts as {
        style: {
          layers: Array<{ id: string; layout?: Record<string, unknown> }>;
        };
      }
    ).style;
    const boatLayer = estilo.layers.find((l) => l.id === "nodes-boat");
    expect(boatLayer).toBeDefined();
    expect(boatLayer?.layout?.["icon-rotate"]).toEqual(["get", "bearing"]);
    expect(boatLayer?.layout?.["icon-rotation-alignment"]).toBe("map");
    expect(boatLayer?.layout?.["icon-pitch-alignment"]).toBe("map");
  });
});

describe("InitializeMap — enquadramento automático", () => {
  const noEm = (nodeNum: number, lon: number, lat: number) => ({
    ...no(nodeNum),
    lon,
    lat,
  });

  it("enquadra todos os nós ao carregar (caixa cobre todos os pontos)", () => {
    montar({} as DataValue["api"]);
    LocalState.setNodes([noEm(1, -71.5, -4.5), noEm(2, -70.0, -5.5)]);
    const m = mapa();
    expect(m.fitBounds).toHaveBeenCalledTimes(1);
    const [caixa, opts] = m.fitBounds.mock.calls[0] as [
      [[number, number], [number, number]],
      { maxZoom: number },
    ];
    expect(caixa).toEqual([
      [-71.5, -5.5],
      [-70.0, -4.5],
    ]);
    expect(opts.maxZoom).toBeLessThanOrEqual(14); // um nó só não vira zoom absurdo
  });

  it("não recentraliza a cada polling com os mesmos nós", () => {
    montar({} as DataValue["api"]);
    LocalState.setNodes([noEm(1, -71.5, -4.5)]);
    LocalState.setNodes([noEm(1, -71.4, -4.4)]); // mesmo nó, posição nova
    expect(mapa().fitBounds).toHaveBeenCalledTimes(1);
  });

  it("reenquadra quando chega um nó novo", () => {
    montar({} as DataValue["api"]);
    LocalState.setNodes([noEm(1, -71.5, -4.5)]);
    LocalState.setNodes([noEm(1, -71.5, -4.5), noEm(2, -70.0, -5.5)]);
    expect(mapa().fitBounds).toHaveBeenCalledTimes(2);
  });

  it("respeita o usuário: depois de arrastar não recentraliza", () => {
    montar({} as DataValue["api"]);
    LocalState.setNodes([noEm(1, -71.5, -4.5)]);
    mapa().emit("dragstart", undefined);
    LocalState.setNodes([noEm(1, -71.5, -4.5), noEm(2, -70.0, -5.5)]);
    expect(mapa().fitBounds).toHaveBeenCalledTimes(1);
  });

  it("ignora coordenadas inválidas", () => {
    montar({} as DataValue["api"]);
    LocalState.setNodes([noEm(1, Number.NaN, 400)]);
    expect(mapa().fitBounds).not.toHaveBeenCalled();
  });

  it("atualiza source nodes quando os filtros do store mudam", () => {
    montar({} as DataValue["api"]);
    const n1 = { ...no(1), kind: "boat" as const, nome: "barco-1" };
    const n2 = { ...no(2), kind: "fixed_station" as const, nome: "fixo-1" };
    LocalState.setNodes([n1, n2]);
    const m = mapa();
    const nodesSource = m.getSource("nodes");
    expect(nodesSource?.setData).toHaveBeenCalled();

    // Filtra por categoria "boat"
    LocalState.setKindFilter("boat");
    const lastCall = nodesSource?.setData.mock.calls.at(-1)?.[0];
    expect(lastCall?.features.length).toBe(1);
    expect(lastCall?.features[0].properties.nodeNum).toBe(1);

    // Restaura "all"
    LocalState.setKindFilter("all");
    const allCall = nodesSource?.setData.mock.calls.at(-1)?.[0];
    expect(allCall?.features.length).toBe(2);
  });

  it("relógio reativo atualiza pins ao cruzar limite de fix antigo sem mover câmera (dois nós)", () => {
    montar({} as DataValue["api"]);
    const t0 = 1_000_000_000_000;
    LocalState.tickNow(t0);

    const n1Stale = { ...noEm(1, -71.5, -4.5), posTime: new Date(t0 - 20 * 3600 * 1000).toISOString() };
    const n2Recente = { ...noEm(2, -70.0, -5.5), posTime: new Date(t0 - 2 * 3600 * 1000).toISOString() };
    LocalState.setNodes([n1Stale, n2Recente]);
    const m = mapa();
    expect(m.fitBounds).toHaveBeenCalledTimes(1);

    // Filtro ativo: apenas nós com fix antigo (>12h)
    LocalState.setConditionFilter("stale");
    const source = m.getSource("nodes");
    expect(source?.setData.mock.calls.at(-1)?.[0].features.length).toBe(1);

    // Avança relógio: nó 2 cruza 12h
    LocalState.tickNow(t0 + 13 * 3600 * 1000);
    expect(source?.setData.mock.calls.at(-1)?.[0].features.length).toBe(2);
    expect(m.fitBounds).toHaveBeenCalledTimes(1); // Câmera estável
  });
});
