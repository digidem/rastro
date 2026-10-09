import { cleanup, render } from "@solidjs/testing-library";
import type { Component } from "solid-js";
import { createComponent, onMount } from "solid-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InitializeMap } from "../../src/InitializeMap.jsx";
import { useMap } from "../../src/hooks/useMap.jsx";
import {
  type FixDwell,
  type LngLat,
  simplifyTrackDwells,
} from "../../src/lib/dwell.js";
import {
  DataContext,
  type DataValue,
} from "../../src/providers/DataProvider.jsx";
import { LocalState } from "../../src/store.js";
import {
  concat,
  deslocar,
  navegacao,
  parada,
} from "./fixtures/trilhaSintetica.js";

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
    images = new Set<string>();
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
      for (const nome of [
        "nodes",
        "track",
        "track-points",
        "boat-tracks",
        "dwell-points",
        "dwell-spread",
      ]) {
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
    // Pins sob o cursor no clique (vazio = área livre do mapa).
    renderizados: unknown[] = [];
    queryRenderedFeatures = vi.fn(() => this.renderizados);
    setLayoutProperty = vi.fn();
    setPaintProperty = vi.fn();
    hasImage = vi.fn((nome: string) => this.images.has(nome));
    addImage = vi.fn((nome: string) => this.images.add(nome));
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

// A paginação por janela tem teste próprio (trilha-janela.test.ts). Aqui a busca
// vira uma chamada única de api.track: as fixtures têm horário fixo, fora da
// janela relativa a Date.now(), e os testes contam chamadas de fetch.
const { janelasPedidas } = vi.hoisted(() => ({
  janelasPedidas: [] as number[],
}));
vi.mock("../../src/lib/trilhaJanela.js", async (original) => ({
  ...(await original<typeof import("../../src/lib/trilhaJanela.js")>()),
  buscarTrilhaJanela: (
    api: { track: (n: string) => Promise<unknown> },
    node: string,
    horas: number,
  ) => {
    janelasPedidas.push(horas);
    return api.track(node);
  },
}));

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
  it("trocar o período da trilha busca de novo com a janela nova", async () => {
    const api = {
      track: vi.fn().mockResolvedValue({ line: null, lines: [], points: [] }),
    } as unknown as DataValue["api"];
    LocalState.setJanelaTrilhaH(336);
    montar(api);
    await new Promise((r) => setTimeout(r, 0));
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    janelasPedidas.length = 0;

    LocalState.setJanelaTrilhaH(72);

    expect(janelasPedidas).toEqual([72]);
    LocalState.setJanelaTrilhaH(336);
  });

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
    mapa().renderizados = [{}];
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
    expect(popupAberto().html).toContain("Heltec V4");

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
    vi.restoreAllMocks();
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

  it("mantém a camada de barcos oculta até o ícone SVG estar registrado", () => {
    const m = prepara(false);
    const estilo = (
      m.opts as {
        style: { layers: { id: string; layout?: Record<string, unknown> }[] };
      }
    ).style;
    const camadaBarco = estilo.layers.find(
      (camada) => camada.id === "nodes-boat",
    );
    expect(camadaBarco?.layout?.visibility).toBe("none");
  });

  it("exibe a camada de barcos só depois de registrar a imagem", () => {
    const imagens: {
      onload: (() => void) | null;
      onerror: (() => void) | null;
      crossOrigin: string;
      src: string;
    }[] = [];
    vi.stubGlobal(
      "Image",
      class {
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        crossOrigin = "";
        set src(_value: string) {
          imagens.push(this);
        }
      },
    );
    const contexto = {
      drawImage: vi.fn(),
      getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(64 * 64 * 4) })),
    };
    const criarElementoOriginal = document.createElement.bind(document);
    const criarElemento = vi
      .spyOn(document, "createElement")
      .mockImplementation((nome: string) => {
        if (nome === "canvas") {
          return {
            width: 0,
            height: 0,
            getContext: () => contexto,
          } as unknown as HTMLCanvasElement;
        }
        return criarElementoOriginal(nome);
      });

    const m = prepara(false);
    expect(imagens).toHaveLength(1);
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "nodes-boat",
      "visibility",
      "none",
    );
    expect(m.setLayoutProperty).not.toHaveBeenCalledWith(
      "nodes-boat",
      "visibility",
      "visible",
    );

    imagens[0].onload?.();

    expect(m.addImage).toHaveBeenCalledWith("boat-icon", expect.anything());
    expect(m.setLayoutProperty).toHaveBeenLastCalledWith(
      "nodes-boat",
      "visibility",
      "visible",
    );
    expect(criarElemento).toHaveBeenCalledWith("canvas");
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

  it("style.load e load juntos inicializam uma vez só (pin + parada + clique livre)", () => {
    const m = prepara(false);
    expect(m.handlers.get("click")?.length).toBe(3);
  });

  it("clique em área livre desseleciona e fecha o popup", async () => {
    const api = { track: vi.fn() } as unknown as DataValue["api"];
    montar(api);
    await new Promise((r) => setTimeout(r, 0));
    mapa().renderizados = [{}];
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: [-30.02, -4.22] },
          properties: { nome: "Nó A" },
        },
      ],
    });
    const popup = popupAberto();
    mapa().renderizados = [];
    mapa().emit("click", { point: { x: 1, y: 1 } });
    expect(popup.remove).toHaveBeenCalled();
    expect(LocalState.localState.selected).toBeNull();
  });

  it("popup exibe SVG ampliado do dispositivo e infere o modelo quando hwModel estiver ausente", async () => {
    const api = {
      track: vi
        .fn()
        .mockReturnValue(
          Promise.resolve({ line: null, lines: [], points: [] }),
        ),
    } as unknown as DataValue["api"];
    montar(api);
    await new Promise((r) => setTimeout(r, 0));

    // Nó barco sem hwModel explícito infere Heltec V4
    LocalState.setNodes([
      {
        ...no(42),
        nome: "univaja-itui-barco-1",
        hwModel: null,
      },
    ]);

    // Testa tanto ausência quanto string vazia nas properties da feature
    mapa().renderizados = [{}];
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: [-30.02, -4.22] },
          properties: {
            nodeNum: 42,
            nome: "univaja-itui-barco-1",
            hwModel: "",
          },
        },
      ],
    });

    const popup = popupAberto();
    expect(popup.html).toContain("/devices/heltec_v4.svg");
    expect(popup.html).toContain("Heltec V4");
    expect(popup.html).not.toContain("Modelo não informado");
  });

  it("camada boat-tracks-line é tracejada indicando trilha coletiva", () => {
    const m = prepara(false);
    const estilo = (
      m.opts as {
        style: {
          layers: Array<{ id: string; paint?: Record<string, unknown> }>;
        };
      }
    ).style;
    const boatTracks = estilo.layers.find((l) => l.id === "boat-tracks-line");
    expect(boatTracks).toBeDefined();
    expect(boatTracks?.paint?.["line-dasharray"]).toEqual([3, 2]);
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

    const n1Stale = {
      ...noEm(1, -71.5, -4.5),
      posTime: new Date(t0 - 20 * 3600 * 1000).toISOString(),
    };
    const n2Recente = {
      ...noEm(2, -70.0, -5.5),
      posTime: new Date(t0 - 2 * 3600 * 1000).toISOString(),
    };
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

  it("alterna visibilidade das camadas de basemap conforme localState.basemapMode", () => {
    montar({} as DataValue["api"]);
    const m = mapa();

    // Default é satellite
    LocalState.setBasemapMode("osm");
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "satellite-base",
      "visibility",
      "none",
    );
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "osm-base",
      "visibility",
      "visible",
    );

    LocalState.setBasemapMode("satellite");
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "satellite-base",
      "visibility",
      "visible",
    );
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "osm-base",
      "visibility",
      "none",
    );
  });

  it("esconde trilhas de barcos coletivas quando um nó é selecionado (regra 14)", async () => {
    const trackMock = vi.fn().mockResolvedValue({
      nodeId: "!00000001",
      lines: [
        [
          [-70.0, -4.0],
          [-70.1, -4.1],
        ],
      ],
      points: [],
    });
    montar({ track: trackMock } as unknown as DataValue["api"]);
    const m = mapa();

    // Adiciona um nó barco
    LocalState.setNodes([
      {
        ...noEm(1, -70.0, -4.0),
        kind: "boat" as const,
        nome: "Barco Solimões",
      },
    ]);

    // Sem seleção: boat-tracks visível
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "boat-tracks-line",
      "visibility",
      "visible",
    );

    // Seleciona um nó: esconde boat-tracks-line
    LocalState.select(1);
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "boat-tracks-line",
      "visibility",
      "none",
    );

    // Desseleciona: volta a exibir boat-tracks-line
    LocalState.select(null);
    expect(m.setLayoutProperty).toHaveBeenCalledWith(
      "boat-tracks-line",
      "visibility",
      "visible",
    );
  });
});

describe("InitializeMap — paradas com dispersão (T4)", () => {
  const C1: LngLat = [-70.0, -5.0];
  const C2: LngLat = [-69.95, -5.0];
  const T0 = Date.parse("2026-10-01T12:00:00Z");
  const min = 60_000;

  // Duas paradas de 1 h separadas por deslocamento de ~5 km.
  const trilhaComDuasParadas = (): FixDwell[] =>
    concat(
      navegacao({
        de: deslocar(C1, -6000, 0),
        para: C1,
        inicioMs: T0,
        kmh: 20,
      }),
      parada({
        centro: C1,
        inicioMs: T0 + 20 * min,
        duracaoMs: 60 * min,
        semente: 3,
      }),
      navegacao({ de: C1, para: C2, inicioMs: T0 + 80 * min, kmh: 20 }),
      parada({
        centro: C2,
        inicioMs: T0 + 100 * min,
        duracaoMs: 60 * min,
        semente: 4,
      }),
    );

  const respostaDe = (fixes: FixDwell[]) => ({
    line: null,
    lines: [] as [number, number][][],
    points: fixes.map((f) => ({ pos: f.pos, posTime: f.posTime, sats: null })),
  });

  // Seleciona o nó e responde a trilha com os pontos dados.
  const selecionarComResposta = async (resposta: unknown) => {
    const api = {
      track: vi.fn().mockResolvedValue(resposta),
    } as unknown as DataValue["api"];
    montar(api);
    await new Promise((r) => setTimeout(r, 0));
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
  };

  const ultimoSetData = (fonte: string) =>
    mapa().getSource(fonte).setData.mock.lastCall?.[0] as {
      features: Array<{
        properties: Record<string, unknown>;
        geometry: { type: string; coordinates: unknown };
      }>;
    };

  it("track-points por padrão não recebe fixes de parada nem spikes", async () => {
    const fixes = trilhaComDuasParadas();
    await selecionarComResposta(respostaDe(fixes));

    const simp = simplifyTrackDwells(fixes);
    const movimento = simp.papel.filter((p) => p === "movimento").length;
    const features = ultimoSetData("track-points").features;
    expect(features).toHaveLength(movimento);
    expect(features.length).toBeLessThan(fixes.length);
    expect(features.every((f) => f.properties.papel === "movimento")).toBe(
      true,
    );
  });

  it("dwell-spread tem um polígono fechado por parada", async () => {
    const fixes = trilhaComDuasParadas();
    await selecionarComResposta(respostaDe(fixes));

    const paradas = simplifyTrackDwells(fixes).dwells;
    expect(paradas).toHaveLength(2);
    const features = ultimoSetData("dwell-spread").features;
    expect(features).toHaveLength(paradas.length);
    for (const f of features) {
      expect(f.geometry.type).toBe("Polygon");
      const anel = (f.geometry.coordinates as LngLat[][])[0];
      expect(anel).toHaveLength(49);
      expect(anel[0]).toEqual(anel[48]);
    }
  });

  it("camadas de dispersão ficam abaixo de track-line", () => {
    montar({} as DataValue["api"]);
    const ids = (
      mapa().opts as { style: { layers: Array<{ id: string }> } }
    ).style.layers.map((l) => l.id);
    const fundo = ids.indexOf("dwell-spread-fill");
    expect(fundo).toBeGreaterThanOrEqual(0);
    expect(ids.indexOf("dwell-spread-line")).toBeGreaterThan(fundo);
    expect(fundo).toBeLessThan(ids.indexOf("track-line"));
  });

  it("halo mínimo de parada fica abaixo de track-line, logo após dwell-spread-line", () => {
    montar({} as DataValue["api"]);
    const camadas = (
      mapa().opts as {
        style: { layers: Array<{ id: string; source?: string }> };
      }
    ).style.layers;
    const ids = camadas.map((l) => l.id);
    const halo = ids.indexOf("dwell-points-halo");
    expect(halo).toBeGreaterThanOrEqual(0);
    expect(halo).toBeLessThan(ids.indexOf("track-line"));
    expect(halo).toBe(ids.indexOf("dwell-spread-line") + 1);
    expect(camadas[halo].source).toBe("dwell-points");
  });

  it("linhas cruas da API só entram sem simplificação (menos de 2 fixes)", async () => {
    const cruas: [number, number][][] = [
      [
        [-70, -5],
        [-69.9, -5],
      ],
    ];
    await selecionarComResposta({
      line: null,
      lines: cruas,
      points: [{ pos: [-70, -5], posTime: null, sats: null }],
    });
    expect(ultimoSetData("track").features).toEqual([
      expect.objectContaining({
        geometry: { type: "LineString", coordinates: cruas[0] },
      }),
    ]);
  });

  it("com simplificação e 0 linhas, a trilha fica vazia (não usa linhas cruas)", async () => {
    // Só parada: a simplificação devolve nenhuma linha de movimento.
    const pontos = parada({
      centro: C1,
      inicioMs: T0,
      duracaoMs: 2 * 60 * min,
      semente: 5,
    });
    expect(simplifyTrackDwells(pontos).linhas).toHaveLength(0);
    await selecionarComResposta({
      line: null,
      lines: [
        [
          [-70, -5],
          [-69.9, -5],
        ],
      ],
      points: respostaDe(pontos).points,
    });
    expect(ultimoSetData("track").features).toEqual([]);
  });

  it("popup da parada mostra duração, fixes, dispersão e ruído descartado", () => {
    montar({} as DataValue["api"]);
    const propriedades = {
      rotulo: "Parada de 1h 0m",
      chegadaMs: T0,
      duracaoMs: 60 * min,
      fixes: 120,
      dispersaoP50M: 14.4,
      dispersaoP90M: 52.6,
      excluidos: 3,
    };
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: C1 },
          properties: propriedades,
        },
      ],
    });
    const html = (popups.at(-1) as FakePopupLike).html;
    expect(html).toContain("Duração: 1h 0m");
    expect(html).toContain("120 fixes");
    expect(html).toContain("50% dos fixes em 14 m · 90% em 53 m");
    expect(html).toContain("3 fixes descartados (ruído)");
  });

  it("popup omite a linha de ruído quando nada foi descartado", () => {
    montar({} as DataValue["api"]);
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: C1 },
          properties: {
            rotulo: "Parada",
            duracaoMs: min,
            fixes: 5,
            excluidos: 0,
          },
        },
      ],
    });
    expect((popups.at(-1) as FakePopupLike).html).not.toContain("descartados");
  });
  it("rótulo 'Ancorado' envelhece com o relógio do store, sem nova busca da trilha", async () => {
    const pontos = [
      ...navegacao({
        de: deslocar(C1, -6000, 0),
        para: C1,
        inicioMs: T0,
        kmh: 20,
      }),
      ...parada({
        centro: C1,
        inicioMs: T0 + 20 * min,
        duracaoMs: 60 * min,
        semente: 5,
      }),
    ];
    const fimMs = T0 + 80 * min; // último fix da parada em curso
    const track = vi.fn().mockResolvedValue(respostaDe(pontos));
    montar({ track } as unknown as DataValue["api"]);
    await new Promise((r) => setTimeout(r, 0));
    LocalState.tickNow(fimMs + 5 * min);
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
    const rotulo = () =>
      ultimoSetData("dwell-points").features[0].properties.rotulo as string;
    expect(rotulo().startsWith("Ancorado há ")).toBe(true);

    LocalState.tickNow(fimMs + 40 * min); // passou de 30 min sem fix novo
    expect(rotulo().startsWith("Parado 1h ")).toBe(true);
    expect(rotulo()).toContain("· último fix há 40m");
    expect(track).toHaveBeenCalledTimes(1);
  });

  it("parada mesclada após lacuna sem sinal: rótulo cita o tempo sem sinal", async () => {
    const pontos = [
      ...parada({ centro: C1, inicioMs: T0, duracaoMs: 60 * min, semente: 3 }),
      ...parada({
        centro: C1,
        inicioMs: T0 + 60 * min + 7 * 60 * min,
        duracaoMs: 60 * min,
        semente: 4,
      }),
    ];
    const fimMs = T0 + 60 * min + 7 * 60 * min + 60 * min;
    const track = vi.fn().mockResolvedValue(respostaDe(pontos));
    montar({ track } as unknown as DataValue["api"]);
    await new Promise((r) => setTimeout(r, 0));
    LocalState.tickNow(fimMs + 5 * min);
    LocalState.setNodes([no(1)]);
    LocalState.select(1);
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
    const rotulo = ultimoSetData("dwell-points").features[0].properties
      .rotulo as string;
    expect(rotulo).toContain("sem sinal");
    expect(rotulo.endsWith(" sem sinal")).toBe(true);
    expect(rotulo).toContain(" · 7h ");
  });

  it("popup de parada com lacuna mostra a linha 'Sem sinal por ... durante a parada'", () => {
    montar({} as DataValue["api"]);
    mapa().emit("click", {
      features: [
        {
          geometry: { type: "Point", coordinates: C1 },
          properties: {
            rotulo: "Parada de 1h 0m · 7h 0m sem sinal",
            duracaoMs: 8 * 60 * min,
            lacunaMs: 7 * 60 * min,
            fixes: 240,
            excluidos: 0,
          },
        },
      ],
    });
    const html = (popups.at(-1) as FakePopupLike).html;
    expect(html).toContain("Sem sinal por 7h 0m durante a parada");
  });

  it("parada após lacuna zera o rumo antigo do nó (sem rumo novo)", async () => {
    // Navegação para leste, lacuna de 118 min e depois só fixes parados ao sul.
    // Parada sem ruído (mesmo posição): o rumo congelado não tem aproximação observada.
    const nav = navegacao({
      de: C1,
      para: deslocar(C1, 3000, 0),
      inicioMs: T0,
      kmh: 20,
    });
    const centroSul = deslocar(C1, 0, -4000);
    const pontos = concat(
      nav,
      parada({
        centro: centroSul,
        inicioMs: T0 + 127 * min,
        duracaoMs: 60 * min,
        semente: 7,
      }).map((f) => ({ ...f, pos: centroSul })),
    );
    expect(simplifyTrackDwells(pontos).parado).toBe(true);
    const track = vi.fn().mockResolvedValue(respostaDe(pontos));
    montar({ track } as unknown as DataValue["api"]);
    await new Promise((r) => setTimeout(r, 0));
    LocalState.setNodes([{ ...no(1), bearing: 90 }]);
    LocalState.select(1);
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
    expect(track).toHaveBeenCalledTimes(1);
    expect(LocalState.localState.nodes[1]?.bearing).toBeNull();
  });

  describe("fixes brutos (T5)", () => {
    // Trilha com um spike: o fix 200 sai 400 m do centro da primeira parada.
    const trilhaComSpike = (): FixDwell[] => {
      const fixes = trilhaComDuasParadas();
      fixes[200] = { ...fixes[200], pos: deslocar(C1, 400, 0) };
      return fixes;
    };

    const papeis = (fonte: string) =>
      ultimoSetData(fonte).features.map((f) => f.properties.papel);

    beforeEach(() => LocalState.setMostrarFixesBrutos(false));
    afterEach(() => LocalState.setMostrarFixesBrutos(false));

    it("ligado: track-points recebe todos os fixes, com paradas e spikes", async () => {
      const fixes = trilhaComSpike();
      expect(simplifyTrackDwells(fixes).papel[200]).toBe("spike");
      await selecionarComResposta(respostaDe(fixes));

      LocalState.setMostrarFixesBrutos(true);

      const lista = papeis("track-points");
      expect(lista).toHaveLength(fixes.length);
      expect(lista).toContain("parada");
      expect(lista).toContain("spike");
    });

    it("desligado: track-points volta a ter só movimento", async () => {
      const fixes = trilhaComSpike();
      await selecionarComResposta(respostaDe(fixes));
      LocalState.setMostrarFixesBrutos(true);

      LocalState.setMostrarFixesBrutos(false);

      const lista = papeis("track-points");
      expect(lista.every((p) => p === "movimento")).toBe(true);
      expect(lista).toHaveLength(
        simplifyTrackDwells(fixes).papel.filter((p) => p === "movimento")
          .length,
      );
    });

    it("alternar o toggle não refaz o fetch da trilha", async () => {
      const api = {
        track: vi.fn().mockResolvedValue(respostaDe(trilhaComSpike())),
      } as unknown as DataValue["api"];
      montar(api);
      await new Promise((r) => setTimeout(r, 0));
      LocalState.setNodes([no(1)]);
      LocalState.select(1);
      for (let i = 0; i < 10; i++) {
        await Promise.resolve();
      }

      LocalState.setMostrarFixesBrutos(true);
      LocalState.setMostrarFixesBrutos(false);
      LocalState.setMostrarFixesBrutos(true);

      expect(api.track).toHaveBeenCalledTimes(1);
      expect(papeis("track-points")).toContain("spike");
    });

    it("sem trilha analisada, ligar o toggle não envia pontos", async () => {
      montar({} as DataValue["api"]);
      await new Promise((r) => setTimeout(r, 0));
      const antes = mapa().getSource("track-points").setData.mock.calls.length;

      LocalState.setMostrarFixesBrutos(true);

      expect(mapa().getSource("track-points").setData.mock.calls.length).toBe(
        antes,
      );
    });

    it("desselecionar esquece a análise: ligar o toggle depois não reaparece com os pontos antigos", async () => {
      await selecionarComResposta(respostaDe(trilhaComSpike()));
      LocalState.select(null);
      const antes = mapa().getSource("track-points").setData.mock.calls.length;

      LocalState.setMostrarFixesBrutos(true);

      expect(mapa().getSource("track-points").setData.mock.calls.length).toBe(
        antes,
      );
      expect(LocalState.localState.trilhaCarregada).toBe(false);
    });
  });
});
