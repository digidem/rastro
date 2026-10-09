import type { Component, JSXElement } from "solid-js";
import { createContext, onCleanup, useContext } from "solid-js";
import { calcularBearing } from "../lib/bearing.js";
import { ANCORADO_MAX_MS, DWELL_PADRAO, distanciaM } from "../lib/dwell.js";
import { limparCacheTrilhas } from "../lib/trilhaJanela.js";
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

// Posição onde o rumo de cada nó foi definido pela última vez (âncora do deslocamento).
const ancorasDeRumo = new Map<number, [number, number]>();

/** Esquece as âncoras da sessão corrente (logout/401): não herdam posição antiga. */
export function limparAncorasDeRumo(): void {
  ancorasDeRumo.clear();
}

/** Parado só vale com fix recente pelo relógio do store (mesma regra da lista de nós). */
function paradoRecente(nodeNum: number): boolean {
  const m = LocalState.localState.movimento[nodeNum];
  if (m?.parado !== true || m.ultimoFixMs === null) {
    return false;
  }
  const agoraMs = LocalState.localState.nowMs || Date.now();
  return agoraMs - m.ultimoFixMs < ANCORADO_MAX_MS;
}

/** Rumo de `node` desde a âncora; mantém o anterior em jitter ou com o nó parado. */
function rumoPorDeslocamento(node: NodeInfo, prev: NodeInfo): number | null {
  const ancora = ancorasDeRumo.get(node.nodeNum) ?? [prev.lon, prev.lat];
  // Parado, ou deslocamento desde a âncora dentro do raio de saída de parada:
  // é ruído de GPS (multipath de 20–100 m), não rumo. Medir desde a âncora (e não
  // do poll anterior) deixa o barco lento acumular deslocamento até definir rumo.
  if (
    paradoRecente(node.nodeNum) ||
    distanciaM(ancora, [node.lon, node.lat]) <= DWELL_PADRAO.raioSaidaM
  ) {
    ancorasDeRumo.set(node.nodeNum, ancora);
    return prev.bearing ?? null;
  }
  ancorasDeRumo.set(node.nodeNum, [node.lon, node.lat]);
  const b = calcularBearing(ancora[0], ancora[1], node.lon, node.lat);
  return b !== null ? b : (prev.bearing ?? null);
}

export function atualizarBearingsDosNos(
  novosNos: NodeInfo[],
  nosAnteriores: Record<number, NodeInfo>,
): void {
  for (const node of novosNos) {
    const prev = nosAnteriores[node.nodeNum];
    if ((node.bearing === undefined || node.bearing === null) && prev) {
      node.bearing = rumoPorDeslocamento(node, prev);
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
    // Âncoras de rumo são da sessão velha: não podem definir rumo na nova.
    limparAncorasDeRumo();
    // Fatias de trilha guardadas também são da sessão velha.
    limparCacheTrilhas();
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
