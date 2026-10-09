// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MapContext } from "../../providers/MapProvider.jsx";
import { LocalStateContext } from "../../providers/index.js";
import { LocalState } from "../../store.js";
import { MapControls } from "./MapControls.jsx";

afterEach(() => {
  cleanup();
  LocalState.setJanelaTrilhaH(336);
  LocalState.desativarRegua();
});

const montar = () =>
  render(() => (
    <MapContext.Provider
      value={
        { fitAllNodes: vi.fn(), zoomIn: vi.fn(), zoomOut: vi.fn() } as never
      }
    >
      <LocalStateContext.Provider value={LocalState}>
        <MapControls sidebarOpen={() => false} onToggleSidebar={vi.fn()} />
      </LocalStateContext.Provider>
    </MapContext.Provider>
  ));

const esperarFoco = () => new Promise((r) => setTimeout(r, 0));

describe("MapControls — período da trilha", () => {
  it("abre com foco na opção marcada; setas navegam; escolher troca a janela e devolve o foco", async () => {
    LocalState.setJanelaTrilhaH(336);
    montar();
    const botao = screen.getByLabelText("Período da trilha: 14 dias");
    fireEvent.click(botao);
    await esperarFoco();
    expect(document.activeElement?.textContent).toContain("14 dias");

    const menu = screen.getByRole("menu", { name: "Período da trilha" });
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // volta ao início
    expect(document.activeElement?.textContent).toContain("24 h");

    fireEvent.click(document.activeElement as HTMLElement);
    expect(LocalState.localState.janelaTrilhaH).toBe(24);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByLabelText("Período da trilha: 24 h"),
    );
  });

  it("setas fora do menu não são capturadas", async () => {
    montar();
    fireEvent.click(screen.getByLabelText("Período da trilha: 14 dias"));
    await esperarFoco();
    const fora = document.createElement("input");
    document.body.append(fora);
    fora.focus();
    const ev = new KeyboardEvent("keydown", {
      key: "ArrowDown",
      bubbles: true,
      cancelable: true,
    });
    fora.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(fora);
    fora.remove();
  });
});

describe("MapControls — régua", () => {
  it("alterna aria-pressed e liga/desliga a régua no store", () => {
    montar();
    const botao = screen.getByRole("button", {
      name: "Régua: medir distâncias",
    });
    expect(botao.getAttribute("aria-pressed")).toBe("false");
    expect(LocalState.localState.regua.ativa).toBe(false);

    fireEvent.click(botao);
    expect(botao.getAttribute("aria-pressed")).toBe("true");
    expect(LocalState.localState.regua.ativa).toBe(true);

    fireEvent.click(botao);
    expect(botao.getAttribute("aria-pressed")).toBe("false");
    expect(LocalState.localState.regua.ativa).toBe(false);
  });
});
