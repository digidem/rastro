// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MapContext } from "../../providers/MapProvider.jsx";
import { LocalStateContext } from "../../providers/index.js";
import { LocalState, type NodeAlert, type NodeInfo } from "../../store.js";
import { NodeInspector } from "./NodeInspector.jsx";

const BADGE_RE = /Bateria crítica/;
const TITLE_RE = /última leitura há 8h/;

const NODE: NodeInfo = {
  nodeNum: 7,
  nodeId: "!00000007",
  nome: "Base Teste",
  posTime: null,
  battery: null,
  lon: 0,
  lat: 0,
};

const ALERTA: NodeAlert = {
  nodeNum: 7,
  alertId: "bateria_critica:7",
  nodeId: "!00000007",
  nodeNome: "Base Teste",
  alertType: "bateria_critica",
  severity: "high",
  triggeredAt: "2026-10-08T01:00:00Z",
  details: {
    battery_level: 15,
    voltage: 3.4,
    last_seen: new Date(Date.now() - 8 * 3_600_000).toISOString(),
  },
};

afterEach(cleanup);

describe("NodeInspector badges de alerta", () => {
  beforeEach(() => LocalState.resetViewerState());

  it("mostra badge de bateria crítica", () => {
    LocalState.setNodes([NODE]);
    LocalState.setAlerts([ALERTA]);
    render(() => (
      <MapContext.Provider value={{ centerOnNode: vi.fn() } as never}>
        <LocalStateContext.Provider value={LocalState}>
          <NodeInspector node={() => NODE} nowMs={() => Date.now()} />
        </LocalStateContext.Provider>
      </MapContext.Provider>
    ));
    expect(screen.getByText(BADGE_RE)).toBeTruthy();
    expect(screen.getByTitle(TITLE_RE)).toBeTruthy();
  });

  it("sem alerta: nenhum badge", () => {
    LocalState.setNodes([NODE]);
    render(() => (
      <MapContext.Provider value={{ centerOnNode: vi.fn() } as never}>
        <LocalStateContext.Provider value={LocalState}>
          <NodeInspector node={() => NODE} nowMs={() => Date.now()} />
        </LocalStateContext.Provider>
      </MapContext.Provider>
    ));
    expect(screen.queryByText(BADGE_RE)).toBeNull();
    expect(screen.queryByText("📡 Sem sinal (>1h)")).toBeNull();
  });
});

describe("NodeInspector — Fixes brutos (T5)", () => {
  beforeEach(() => {
    LocalState.resetViewerState();
    LocalState.setMostrarFixesBrutos(false);
    LocalState.setNodes([NODE]);
  });

  const montar = () =>
    render(() => (
      <MapContext.Provider value={{ centerOnNode: vi.fn() } as never}>
        <LocalStateContext.Provider value={LocalState}>
          <NodeInspector node={() => NODE} nowMs={() => Date.now()} />
        </LocalStateContext.Provider>
      </MapContext.Provider>
    ));

  it("sem trilha carregada, o switch não aparece", () => {
    montar();
    expect(screen.queryByText("Fixes brutos")).toBeNull();
  });

  it("com trilha carregada, mostra o switch e reflete o estado", () => {
    LocalState.setTrilhaCarregada(true);
    montar();
    const chave = screen.getByRole("checkbox", { name: "Fixes brutos" });
    expect((chave as HTMLInputElement).checked).toBe(false);
  });

  it("alternar o switch atualiza o store", () => {
    LocalState.setTrilhaCarregada(true);
    montar();
    fireEvent.click(screen.getByRole("checkbox", { name: "Fixes brutos" }));
    expect(LocalState.localState.mostrarFixesBrutos).toBe(true);
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Fixes brutos",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    LocalState.setMostrarFixesBrutos(false);
  });
});

describe("NodeInspector — Medir daqui (T6)", () => {
  beforeEach(() => {
    LocalState.resetViewerState();
    LocalState.setNodes([
      { ...NODE, posTime: "2026-10-08T12:00:00Z", lon: -70.0, lat: -5.0 },
    ]);
  });

  afterEach(() => LocalState.desativarRegua());

  it("clicar em Medir daqui liga a régua com o vértice 0 ancorado no nó", () => {
    render(() => (
      <MapContext.Provider value={{ centerOnNode: vi.fn() } as never}>
        <LocalStateContext.Provider value={LocalState}>
          <NodeInspector node={() => NODE} nowMs={() => Date.now()} />
        </LocalStateContext.Provider>
      </MapContext.Provider>
    ));
    fireEvent.click(
      screen.getByRole("button", { name: "Medir distância a partir deste nó" }),
    );
    expect(LocalState.localState.regua).toEqual({
      ativa: true,
      concluida: false,
      pontos: [{ tipo: "no", nodeNum: NODE.nodeNum }],
    });
  });
});
