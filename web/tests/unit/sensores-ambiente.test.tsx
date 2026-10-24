// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, render, screen } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SensoresAmbiente } from "../../src/components/viewer/SensoresAmbiente.jsx";
import { LocalStateContext } from "../../src/providers/index.js";
import { type LeituraAmbiente, LocalState } from "../../src/store.js";

const AGORA = Date.parse("2026-10-24T12:00:00Z");

const leitura = (
  nodeNum: number,
  nome: string,
  telemTime: string,
): LeituraAmbiente => ({
  nodeNum,
  nodeId: `!0000000${nodeNum}`,
  nome,
  temperaturaC: 27.5,
  umidade: 80,
  pressaoHpa: null,
  telemTime,
});

const montar = () =>
  render(() => (
    <LocalStateContext.Provider value={LocalState}>
      <SensoresAmbiente nowMs={() => AGORA} />
    </LocalStateContext.Provider>
  ));

beforeEach(() => LocalState.resetViewerState());
afterEach(cleanup);

describe("SensoresAmbiente", () => {
  it("sem leituras não renderiza nada", () => {
    montar();
    expect(screen.queryByLabelText("Sensores")).toBeNull();
  });

  it("lista o sensor (mesmo sem nó no mapa) com medidas e idade", () => {
    LocalState.setAmbiente([
      leitura(1, "univaja-sensor-1", "2026-10-24T11:55:00Z"),
    ]);
    montar();
    expect(screen.getByText("univaja-sensor-1")).toBeTruthy();
    expect(screen.getByText("27,5 °C · umidade 80 %")).toBeTruthy();
    expect(screen.getByText("há 5 min")).toBeTruthy();
    expect(screen.queryByText("desatualizada")).toBeNull();
  });

  it("leitura com mais de 1 h aparece como desatualizada", () => {
    LocalState.setAmbiente([
      leitura(1, "univaja-sensor-1", "2026-10-24T08:00:00Z"),
    ]);
    montar();
    expect(screen.getByText("desatualizada")).toBeTruthy();
  });

  it("logout limpa as leituras", () => {
    LocalState.setAmbiente([leitura(1, "s", "2026-10-24T11:55:00Z")]);
    LocalState.resetViewerState();
    expect(LocalState.localState.ambiente).toEqual({});
  });
});
