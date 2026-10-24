import { cleanup, render } from "@solidjs/testing-library";
import { createComponent, onCleanup, onMount } from "solid-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DataProvider,
  type DataValue,
  useData,
} from "../../src/providers/DataProvider.jsx";
import { ErrTokenInvalid } from "../../src/providers/api.js";
import { LocalState } from "../../src/store.js";

// O DataProvider importa "./api.js"; mockamos o módulo inteiro para
// controlar o que createApiClient().latest() devolve, sem rede.
const { latestMock, ambienteMock } = vi.hoisted(() => ({
  latestMock: vi.fn(),
  ambienteMock: vi.fn(),
}));

vi.mock("../../src/providers/api.js", () => ({
  // biome-ignore lint/style/useNamingConvention: nomes devem casar com os exports originais
  ErrTokenInvalid: class ErrTokenInvalid extends Error {},
  // biome-ignore lint/style/useNamingConvention: nomes devem casar com os exports originais
  ErrOffline: class ErrOffline extends Error {},
  createApiClient: () => ({
    latest: latestMock,
    environmentLatest: ambienteMock,
  }),
}));

// Intervalo curto para fake timers; VITE_API_POLL_SECS não entra nos testes.
const POLL_MS = 1000;

// DataValue capturado pelo Consumidor montado por montar().
let provider: DataValue | undefined;

const dados = (): DataValue => {
  if (provider === undefined) {
    throw new Error("provider não montado");
  }
  return provider;
};

// Consumer montado dentro do provider: inicia o polling e o encerra ao desmontar.
const Consumidor = () => {
  provider = useData();
  onMount(() => dados().startPolling(POLL_MS));
  onCleanup(() => dados().stopPolling());
  return "";
};

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

const montar = () =>
  render(() =>
    createComponent(DataProvider, {
      get children() {
        return createComponent(Consumidor, {});
      },
    }),
  );

beforeEach(() => {
  latestMock.mockReset();
  ambienteMock.mockReset();
  ambienteMock.mockResolvedValue([]);
  vi.useFakeTimers();
  // Sessão "logada" de partida, como após o entrar() bem-sucedido.
  LocalState.setAuth("ok");
  LocalState.setNodes([]);
  LocalState.setOnline(false);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("DataProvider — 401 (ErrTokenInvalid)", () => {
  it("avança a geração, limpa nodes, fica offline, vai ao login e para o polling", async () => {
    const gen0 = LocalState.localState.pollingGeracao;
    latestMock.mockRejectedValue(new ErrTokenInvalid("401"));
    montar();
    // Flush da primeira rodada: poll síncrono + rejeição assíncrona.
    await vi.advanceTimersByTimeAsync(0);

    expect(LocalState.localState.auth).toBe("login");
    expect(LocalState.localState.nodes).toEqual({});
    expect(LocalState.localState.online).toBe(false);
    expect(LocalState.localState.pollingGeracao).toBe(gen0 + 1);

    // Polling parado: avançar vários intervalos não dispara latest() de novo.
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    expect(latestMock).toHaveBeenCalledTimes(1);
  });

  it("401 com latest() pendente: resposta atrasada não reintroduz nós", async () => {
    const { promise: p1, resolve: r1 } = Promise.withResolvers<unknown>();
    const { promise: p2, reject: r2 } = Promise.withResolvers<unknown>();
    latestMock.mockImplementation(() =>
      latestMock.mock.calls.length === 1 ? p1 : p2,
    );
    montar();
    await vi.advanceTimersByTimeAsync(0); // poll 1 em voo
    await vi.advanceTimersByTimeAsync(POLL_MS); // poll 2 em voo
    expect(latestMock).toHaveBeenCalledTimes(2);

    // O 401 chega na rodada 2: geração avança, sessão vai ao login.
    r2(new ErrTokenInvalid("401"));
    await vi.advanceTimersByTimeAsync(0);
    expect(LocalState.localState.auth).toBe("login");
    expect(LocalState.localState.nodes).toEqual({});

    // A resposta atrasada da rodada 1 (geração velha) resolve: descartada.
    r1([no(7)]);
    await vi.advanceTimersByTimeAsync(0);
    expect(LocalState.localState.nodes).toEqual({});
    expect(LocalState.localState.online).toBe(false);
  });
});

describe("DataProvider — sessão trocou durante o fetch", () => {
  it("descarta resposta atrasada da sessão antiga (logout)", async () => {
    // Resposta fica em voo até o teste liberá-la.
    const { promise, resolve } = Promise.withResolvers<unknown>();
    latestMock.mockReturnValue(promise);
    montar();
    await vi.advanceTimersByTimeAsync(0);
    expect(latestMock).toHaveBeenCalledTimes(1);

    // Logout no meio do fetch (MapWindow.sair → clearSessionData do provider):
    // para o polling, avança a geração e limpa o store.
    dados().clearSessionData();

    // A resposta chega — mas de uma geração antiga: reintroduzir dados não pode.
    resolve([no(7)]);
    await vi.advanceTimersByTimeAsync(0);
    expect(LocalState.localState.nodes).toEqual({});
    expect(LocalState.localState.online).toBe(false);
    // Logout para o polling; a sessão nova o retoma (startSession).
    expect(LocalState.localState.auth).toBe("ok");
    await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    expect(latestMock).toHaveBeenCalledTimes(1);
  });
});

describe("DataProvider — leituras ambientais", () => {
  const leitura = {
    nodeNum: 9,
    nodeId: "!00000009",
    nome: "Sensor",
    temperaturaC: 25,
    umidade: 70,
    pressaoHpa: 1010,
    telemTime: "2026-10-24T12:00:00Z",
  };

  it("grava no store no mesmo tick do latest", async () => {
    latestMock.mockResolvedValue([no(7)]);
    ambienteMock.mockResolvedValue([leitura]);
    montar();
    await vi.advanceTimersByTimeAsync(0);
    expect(LocalState.localState.ambiente[9]?.temperaturaC).toBe(25);
  });

  it("erro transitório mantém as leituras anteriores", async () => {
    latestMock.mockResolvedValue([no(7)]);
    ambienteMock
      .mockResolvedValueOnce([leitura])
      .mockRejectedValue(new Error("x"));
    montar();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(LocalState.localState.ambiente[9]).toBeDefined();
  });

  it("resposta atrasada de sessão antiga (logout) não reintroduz leituras", async () => {
    const { promise, resolve } = Promise.withResolvers<unknown>();
    latestMock.mockResolvedValue([no(7)]);
    ambienteMock.mockReturnValue(promise);
    montar();
    await vi.advanceTimersByTimeAsync(0);
    dados().clearSessionData();
    resolve([leitura]);
    await vi.advanceTimersByTimeAsync(0);
    expect(LocalState.localState.ambiente).toEqual({});
  });
});
