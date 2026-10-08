import type { Component, JSXElement } from "solid-js";
import { createContext, onCleanup, useContext } from "solid-js";
import { calcularBearing } from "../lib/bearing.js";
import { LocalState, type NodeInfo } from "../store.js";
import { type ApiClient, ErrTokenInvalid, createApiClient } from "./api.js";

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

function atualizarBearingsDosNos(
  novosNos: NodeInfo[],
  nosAnteriores: Record<number, NodeInfo>,
): void {
  for (const node of novosNos) {
    if (node.bearing !== undefined && node.bearing !== null) {
      continue;
    }
    const prev = nosAnteriores[node.nodeNum];
    if (!prev) {
      continue;
    }
    const delta = Math.hypot(node.lon - prev.lon, node.lat - prev.lat);
    if (delta > 0.00005) {
      const b = calcularBearing(prev.lon, prev.lat, node.lon, node.lat);
      node.bearing = b !== null ? b : (prev.bearing ?? null);
    } else {
      node.bearing = prev.bearing ?? null;
    }
  }
}

/** Provê o ApiClient e o polling de latest() que alimenta o store. */
export const DataProvider: Component<{ children?: JSXElement }> = (props) => {
  const api = createApiClient({ getToken: devToken });
  let timer: ReturnType<typeof setInterval> | undefined;
  let pollReqSeq = 0;

  const stopPolling = () => {
    if (timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
    }
  };

  /** Alertas (GET /api/alerts): roda em paralelo ao latest; a geração/seq
   * protegem igual; erro transitório mantém os alertas anteriores (badge
   * não pisca); 401 encerra a sessão como no latest. */
  const pollAlertas = async (minhaGen: number, mySeq: number) => {
    try {
      const alerts = await api.alerts();
      if (
        minhaGen !== LocalState.localState.pollingGeracao ||
        mySeq !== pollReqSeq
      ) {
        return;
      }
      LocalState.setAlerts(alerts);
    } catch (err) {
      if (
        minhaGen !== LocalState.localState.pollingGeracao ||
        mySeq !== pollReqSeq
      ) {
        return;
      }
      if (err instanceof ErrTokenInvalid) {
        clearSessionData();
        LocalState.setAuth("login");
      }
      // outro erro: mantém os alertas anteriores até o próximo tick
    }
  };

  const poll = async () => {
    const minhaGen = LocalState.localState.pollingGeracao;
    const mySeq = ++pollReqSeq;
    LocalState.setLatestStatus("loading");
    // Alertas viajam no MESMO tick do polling (15–30 s): badge some/entra
    // junto com o refresh de nós, sem timer extra.
    void pollAlertas(minhaGen, mySeq);
    try {
      const nodes = await api.latest();
      // Descarta se a geração avançou OU se uma requisição mais nova foi emitida
      if (
        minhaGen !== LocalState.localState.pollingGeracao ||
        mySeq !== pollReqSeq
      ) {
        return;
      }
      atualizarBearingsDosNos(nodes, LocalState.localState.nodes);
      LocalState.setNodes(nodes);
      LocalState.setOnline(true);
      LocalState.setLatestStatus("ready");
    } catch (err) {
      // QUALQUER erro de requisição superada ou sessão antiga é descartado sem mutar o store
      if (
        minhaGen !== LocalState.localState.pollingGeracao ||
        mySeq !== pollReqSeq
      ) {
        return;
      }
      if (err instanceof ErrTokenInvalid) {
        // Cookie expirado/revogado (401): encerra sessão e volta à tela de login.
        clearSessionData();
        LocalState.setAuth("login");
      } else {
        LocalState.setLatestStatus("error");
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
    // Zera também a VISUALIZAÇÃO da sessão velha: filtros, status de carga e
    // seleção não sobrevivem ao logout/401.
    LocalState.resetViewerState();
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
