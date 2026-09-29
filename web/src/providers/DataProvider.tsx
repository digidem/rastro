import type { Component, JSXElement } from "solid-js";
import { createContext, onCleanup, useContext } from "solid-js";
import { LocalState } from "../store.js";
import {
  type ApiClient,
  ErrOffline,
  ErrTokenInvalid,
  createApiClient,
} from "./api.js";

export interface DataValue {
  api: ApiClient;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  /**
   * Encerra a visualização da sessão corrente: para o polling, avança a
   * geração (respostas em voo de latest/track ficam mortas), limpa nós e
   * marca offline. Usado em 401 e no logout ("Sair").
   */
  clearSessionData: () => void;
  /** auth="ok" + polling — após login/verificação bem-sucedida. */
  startSession: () => void;
}

export const DataContext = createContext<DataValue>();

// Intervalo padrão de 15 s; VITE_API_POLL_SECS sobrescreve (em SEGUNDOS).
const intervaloPadrao = () => {
  const s = Number(import.meta.env.VITE_API_POLL_SECS);
  return Number.isFinite(s) && s > 0 ? s * 1000 : 15000;
};

// Token só em DEV (dev/e2e com .env local). Build de produção NUNCA embute:
// import.meta.env.DEV vira false e o valor de VITE_API_TOKEN nunca é lido.
const devToken = (): string | undefined =>
  import.meta.env.DEV
    ? (import.meta.env.VITE_API_TOKEN as string | undefined) || undefined
    : undefined;

/** Provê o ApiClient e o polling de latest() que alimenta o store. */
export const DataProvider: Component<{ children?: JSXElement }> = (props) => {
  const api = createApiClient({ getToken: devToken });
  let timer: ReturnType<typeof setInterval> | undefined;

  const stopPolling = () => {
    if (timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
    }
  };

  const poll = async () => {
    // Geração capturada ANTES do fetch: se a sessão trocar durante o voo,
    // a resposta é descartada — dados da sessão velha não reintroduzem nada.
    const minhaGen = LocalState.localState.pollingGeracao;
    try {
      const nodes = await api.latest();
      if (minhaGen !== LocalState.localState.pollingGeracao) {
        return; // sessão trocou durante o fetch: resposta descartada
      }
      LocalState.setNodes(nodes);
      LocalState.setOnline(true);
    } catch (err) {
      if (err instanceof ErrTokenInvalid) {
        if (minhaGen !== LocalState.localState.pollingGeracao) {
          return; // 401 de outra geração: a sessão nova cuida do store
        }
        // Cookie expirado/revogado (401): encerra sessão e volta à tela de login.
        clearSessionData();
        LocalState.setAuth("login");
      } else if (err instanceof ErrOffline) {
        LocalState.setOnline(false);
      } else {
        // 503, JSON inválido, etc.: qualquer erro desconhecido derruba o indicador
        LocalState.setOnline(false);
      }
    }
  };

  const startPolling = (intervalMs?: number) => {
    stopPolling();
    timer = setInterval(poll, intervalMs ?? intervaloPadrao());
    poll(); // primeira rodada imediata
  };

  const clearSessionData = () => {
    stopPolling();
    // Geração nova mata respostas em voo da sessão anterior.
    LocalState.bumpPollingGeracao();
    LocalState.setNodes([]);
    LocalState.setOnline(false);
  };

  const startSession = () => {
    LocalState.setAuth("ok");
    startPolling();
  };

  onCleanup(stopPolling);

  return (
    <DataContext.Provider
      value={{ api, startPolling, stopPolling, clearSessionData, startSession }}
    >
      {props.children}
    </DataContext.Provider>
  );
};

export function useData(): DataValue {
  // biome-ignore lint/style/noNonNullAssertion: <explanation>
  return useContext(DataContext)!;
}
