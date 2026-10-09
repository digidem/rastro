// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, render, screen } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LocalStateContext } from "../../providers/index.js";
import { LocalState, type NodeAlert, type NodeInfo } from "../../store.js";
import { NodeList } from "./NodeList.jsx";

const NODE_OK: NodeInfo = {
  nodeNum: 7,
  nodeId: "!00000007",
  nome: "Barco Ok",
  posTime: null,
  battery: null,
  lon: 0,
  lat: 0,
};

const NODE_MUDO: NodeInfo = {
  nodeNum: 9,
  nodeId: "!00000009",
  nome: "Barco Mudo",
  posTime: null,
  battery: null,
  lon: 0,
  lat: 0,
};

const BADGE_BATERIA_RE = /Bateria crítica · leitura há/;

const ALERTAS: NodeAlert[] = [
  {
    nodeNum: 9,
    alertId: "gateway_mudo:9",
    nodeId: "!00000009",
    nodeNome: "Barco Mudo",
    alertType: "gateway_mudo",
    severity: "critical",
    triggeredAt: "2026-10-08T00:30:00Z",
    details: null,
  },
  {
    nodeNum: 7,
    alertId: "bateria_critica:7",
    nodeId: "!00000007",
    nodeNome: "Barco Ok",
    alertType: "bateria_critica",
    severity: "high",
    triggeredAt: "2026-10-08T01:00:00Z",
    details: {
      battery_level: 15,
      voltage: 3.4,
      last_seen: new Date(Date.now() - 8 * 3_600_000).toISOString(),
    },
  },
];

const setup = () => {
  LocalState.setNodes([NODE_OK, NODE_MUDO]);
  LocalState.setAlerts(ALERTAS);
  return render(() => (
    <LocalStateContext.Provider value={LocalState}>
      <NodeList
        nodes={() => [NODE_OK, NODE_MUDO]}
        totalCount={() => 2}
        filteredCount={() => 2}
        nowMs={() => Date.now()}
      />
    </LocalStateContext.Provider>
  ));
};

afterEach(cleanup);

describe("NodeList badges de alerta", () => {
  beforeEach(() => {
    LocalState.resetViewerState();
  });

  it("mostra badges com idade da última leitura", () => {
    setup();
    expect(screen.getByText("📡 Sem sinal (>1h)")).toBeTruthy();
    const badge = screen.getByText(BADGE_BATERIA_RE);
    expect(badge).toBeTruthy();
  });

  it("remove o badge quando o alerta é limpo", () => {
    setup();
    LocalState.setAlerts([]);
    expect(screen.queryByText("📡 Sem sinal (>1h)")).toBeNull();
    expect(screen.queryByText(BADGE_BATERIA_RE)).toBeNull();
  });

  it("sem alertas: nenhum badge", () => {
    LocalState.setNodes([NODE_OK]);
    LocalState.setAlerts([]);
    render(() => (
      <LocalStateContext.Provider value={LocalState}>
        <NodeList
          nodes={() => [NODE_OK]}
          totalCount={() => 1}
          filteredCount={() => 1}
          nowMs={() => Date.now()}
        />
      </LocalStateContext.Provider>
    ));
    expect(screen.queryByText(/Sem sinal/)).toBeNull();
    expect(screen.queryByText(/Bateria crítica/)).toBeNull();
  });
});

describe("NodeList parada (há X) pelo relógio reativo", () => {
  const minuto = 60_000;
  const agoraBase = Date.UTC(2026, 9, 9, 12, 0, 0);

  beforeEach(() => {
    LocalState.resetViewerState();
    LocalState.setAlerts([]);
    LocalState.setNodes([NODE_OK]);
  });

  // Renderiza com relógio controlável: o teste avança `agora` sem nova busca.
  const montarParada = (movimento: {
    desdeMs: number | null;
    ultimoFixMs: number | null;
  }) => {
    const [agora, setAgora] = createSignal(agoraBase);
    LocalState.setNodeMovimento(NODE_OK.nodeNum, {
      parado: true,
      velocidadeKmh: 0,
      ...movimento,
    });
    render(() => (
      <LocalStateContext.Provider value={LocalState}>
        <NodeList
          nodes={() => [NODE_OK]}
          totalCount={() => 1}
          filteredCount={() => 1}
          nowMs={agora}
        />
      </LocalStateContext.Provider>
    ));
    return setAgora;
  };

  it("mostra '(há X)' com último fix recente", () => {
    montarParada({
      desdeMs: agoraBase - 60 * minuto,
      ultimoFixMs: agoraBase - 5 * minuto,
    });
    expect(screen.getByText("⚓ Ancorado / Parado (há 1h 0m)")).toBeTruthy();
  });

  it("some o '(há X)' quando o relógio passa de 30 min sem fix novo", () => {
    const setAgora = montarParada({
      desdeMs: agoraBase - 60 * minuto,
      ultimoFixMs: agoraBase - 5 * minuto,
    });
    setAgora(agoraBase + 26 * minuto); // último fix há 31 min
    expect(screen.getByText("⚓ Ancorado / Parado")).toBeTruthy();
    expect(document.body.textContent).not.toContain("(há ");
  });

  it("sem último fix conhecido: só '⚓ Ancorado / Parado'", () => {
    montarParada({ desdeMs: agoraBase - 60 * minuto, ultimoFixMs: null });
    expect(screen.getByText("⚓ Ancorado / Parado")).toBeTruthy();
  });
});
