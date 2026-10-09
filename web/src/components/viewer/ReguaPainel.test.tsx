// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocalStateContext } from "../../providers/index.js";
import { LocalState } from "../../store.js";
import { ReguaPainel } from "./ReguaPainel.jsx";

const RE_EM_LINHA_RETA = /Em linha reta:/;
const RE_SEGMENTO_1 = /1→2 · 1,1 km · S 180°/;
const RE_SEGMENTO_2 = /2→3 · 1,1 km · O 270°/;
const RE_CHEGADA = /Chegada estimada: ~\d+m a 12 km\/h/;
const RE_SEM_CHEGADA = /Chegada estimada/;

afterEach(() => {
  cleanup();
  LocalState.desativarRegua();
  LocalState.setNodes([]);
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
});

const montar = () =>
  render(() => (
    <LocalStateContext.Provider value={LocalState}>
      <ReguaPainel />
    </LocalStateContext.Provider>
  ));

const noFictizio = (nodeNum: number, lon: number, lat: number) =>
  ({
    nodeNum,
    nome: "Barco",
    lon,
    lat,
    nodeId: "",
  }) as never;

describe("ReguaPainel", () => {
  it("mostra dica com 0 pontos e pede o próximo com 1 ponto", () => {
    LocalState.ativarRegua();
    montar();
    expect(
      screen.getByText("Toque no mapa ou num nó para começar"),
    ).toBeDefined();

    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    expect(screen.getByText("Toque no próximo ponto")).toBeDefined();
  });

  it("desfazer fica desabilitado sem pontos", () => {
    LocalState.ativarRegua();
    montar();
    const desfazer = screen.getByRole("button", {
      name: "Desfazer último ponto",
    });
    expect((desfazer as HTMLButtonElement).disabled).toBe(true);
  });

  it("com 3 pontos livres mostra o total e os segmentos", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5.01 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70.01, lat: -5.01 });
    montar();

    expect(screen.getByTestId("regua-total").textContent).toBe("2,2 km");
    expect(screen.getByText(RE_EM_LINHA_RETA)).toBeDefined();
    expect(screen.getByText(RE_SEGMENTO_1)).toBeDefined();
    expect(screen.getByText(RE_SEGMENTO_2)).toBeDefined();
  });

  it("concluir fecha a medição e some o botão", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5.01 });
    montar();

    fireEvent.click(screen.getByRole("button", { name: "Concluir medição" }));
    expect(LocalState.localState.regua.concluida).toBe(true);
    expect(
      screen.queryByRole("button", { name: "Concluir medição" }),
    ).toBeNull();
  });

  it("chegada estimada aparece só para nó em movimento com velocidade", () => {
    LocalState.setNodes([noFictizio(7, -70, -5)]);
    LocalState.setNodeMovimento(7, {
      parado: false,
      desdeMs: null,
      ultimoFixMs: null,
      velocidadeKmh: 12,
    });
    LocalState.ativarRegua(7);
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70.01, lat: -5 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70.01, lat: -5.01 });
    montar();

    expect(screen.getByText(RE_CHEGADA)).toBeDefined();
  });

  it("sem chegada estimada quando o nó está parado", () => {
    LocalState.setNodes([noFictizio(7, -70, -5)]);
    LocalState.setNodeMovimento(7, {
      parado: true,
      desdeMs: 1,
      ultimoFixMs: 1,
      velocidadeKmh: 12,
    });
    LocalState.ativarRegua(7);
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70.01, lat: -5 });
    montar();

    expect(screen.queryByText(RE_SEM_CHEGADA)).toBeNull();
  });

  it("sem chegada estimada quando a velocidade é desconhecida", () => {
    LocalState.setNodes([noFictizio(7, -70, -5)]);
    LocalState.setNodeMovimento(7, {
      parado: false,
      desdeMs: null,
      ultimoFixMs: null,
      velocidadeKmh: null,
    });
    LocalState.ativarRegua(7);
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70.01, lat: -5 });
    montar();

    expect(screen.queryByText(RE_SEM_CHEGADA)).toBeNull();
  });

  it("Copiar envia coordenadas e distância e mostra Copiado", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5.01 });
    montar();

    fireEvent.click(
      screen.getByRole("button", { name: "Copiar coordenadas e distância" }),
    );
    expect(writeText).toHaveBeenCalledWith(
      "1. -5.00000, -70.00000\n2. -5.01000, -70.00000\nTotal: 1,1 km",
    );
    expect(await screen.findByText("Copiado")).toBeDefined();
  });

  it("Copiar sem clipboard mostra falha", async () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5.01 });
    montar();

    fireEvent.click(
      screen.getByRole("button", { name: "Copiar coordenadas e distância" }),
    );
    expect(await screen.findByText("Não foi possível copiar")).toBeDefined();
  });

  it("Fechar desliga a régua e apaga a medição", () => {
    LocalState.ativarRegua();
    LocalState.adicionarPontoRegua({ tipo: "livre", lon: -70, lat: -5 });
    montar();

    fireEvent.click(screen.getByRole("button", { name: "Fechar régua" }));
    expect(LocalState.localState.regua.ativa).toBe(false);
    expect(LocalState.localState.regua.pontos).toEqual([]);
  });
});
