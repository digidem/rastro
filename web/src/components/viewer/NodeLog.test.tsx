// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataContext, type DataValue } from "../../providers/DataProvider.jsx";
import { MapContext } from "../../providers/MapProvider.jsx";
import type { ApiClient, NodeEvent } from "../../providers/api.js";
import { LocalStateContext } from "../../providers/index.js";
import { LocalState, type NodeInfo } from "../../store.js";
import { NodeLog } from "./NodeLog.jsx";

const NODE: NodeInfo = {
  nodeNum: 7,
  nodeId: "!00000007",
  nome: "Barco Ituí",
  posTime: null,
  battery: null,
  lon: 0,
  lat: 0,
};

const POS: NodeEvent = {
  kind: "pos",
  id: 1,
  ts: "2026-10-01T12:00:00Z",
  lat: -3.1,
  lon: -60.0,
  snr: 6.5,
  gateway_num: 99,
  gateway_name: "Base Ituí",
};
const MSG: NodeEvent = {
  kind: "msg",
  id: 2,
  ts: "2026-10-01T11:00:00Z",
  text: "SOCORRO",
  direction: "in",
  is_alert: true,
};

const setup = (events: ReturnType<typeof vi.fn>) => {
  const centerOnPoint = vi.fn();
  const onBack = vi.fn();
  const data = {
    api: { events } as unknown as ApiClient,
    clearSessionData: vi.fn(),
  };
  render(() => (
    <LocalStateContext.Provider value={LocalState}>
      <DataContext.Provider value={data as unknown as DataValue}>
        <MapContext.Provider
          value={
            { centerOnPoint } as unknown as Parameters<
              typeof MapContext.Provider
            >[0]["value"]
          }
        >
          <NodeLog node={() => NODE} onBack={onBack} />
        </MapContext.Provider>
      </DataContext.Provider>
    </LocalStateContext.Provider>
  ));
  return { centerOnPoint, onBack };
};

afterEach(cleanup);

describe("NodeLog", () => {
  it("lista registros com gateway e alerta; clique em posição centraliza", async () => {
    const events = vi
      .fn()
      .mockResolvedValue({ events: [POS, MSG], nextCursor: null });
    const { centerOnPoint } = setup(events);
    await screen.findByText("-3.10000, -60.00000");
    expect(screen.getByText("via Base Ituí")).toBeTruthy();
    expect(screen.getByText("SOCORRO")).toBeTruthy();
    expect(screen.getByText("alerta")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Centralizar no mapa neste ponto"));
    expect(centerOnPoint).toHaveBeenCalledWith(-60.0, -3.1);
    expect(events).toHaveBeenCalledWith("!00000007", {
      kinds: undefined,
      before: null,
      limit: 50,
    });
  });

  it("pagina com cursor e volta", async () => {
    const events = vi
      .fn()
      .mockResolvedValueOnce({ events: [POS], nextCursor: "CUR1" })
      .mockResolvedValueOnce({ events: [MSG], nextCursor: null })
      .mockResolvedValue({ events: [POS], nextCursor: "CUR1" });
    setup(events);
    await screen.findByText("-3.10000, -60.00000");
    fireEvent.click(screen.getByText(/Mais antigos/));
    await screen.findByText("SOCORRO");
    expect(events.mock.calls[1][1].before).toBe("CUR1");
    expect(screen.getByText(/Página 2/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Mais novos/));
    await screen.findByText("-3.10000, -60.00000");
    expect(screen.getByText(/Página 1/)).toBeTruthy();
  });

  it("filtro envia kinds e volta à primeira página", async () => {
    const events = vi.fn().mockResolvedValue({ events: [], nextCursor: null });
    setup(events);
    await screen.findByText("Sem registros.");
    fireEvent.click(screen.getByText("Telemetria"));
    await waitFor(() =>
      expect(events).toHaveBeenLastCalledWith("!00000007", {
        kinds: ["telem"],
        before: null,
        limit: 50,
      }),
    );
  });

  it("erro mostra alerta; voltar chama onBack", async () => {
    const events = vi.fn().mockRejectedValue(new Error("x"));
    const { onBack } = setup(events);
    await screen.findByRole("alert");
    fireEvent.click(screen.getByLabelText("Voltar para a lista de nós"));
    expect(onBack).toHaveBeenCalled();
  });
});
