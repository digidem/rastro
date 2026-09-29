import { createStore, reconcile } from "solid-js/store";

/** Nó visto pela última rodada de /api/nodes/latest. */
export interface NodeInfo {
  nodeNum: number;
  nodeId: string;
  nome: string;
  posTime: string | null;
  battery: number | null;
  lon: number;
  lat: number;
}

/** Estado da sessão no viewer. */
export type AuthEstado = "verificando" | "login" | "ok";

interface LocalState {
  nodes: Record<number, NodeInfo>;
  selected: number | null;
  /** Sessão: verificando (checa cookie), login (tela de token), ok (mapa). */
  auth: AuthEstado;
  /** O servidor exige token? (GET /api/auth/estado → exigida) */
  authExigida: boolean;
  /** Dias de lembrar que o servidor aceita no "Lembrar neste computador". */
  diasLembrar: number;
  /**
   * Geração de sessão do polling: sobe em 401/logout. Uma resposta em voo
   * (latest/track) captura a geração antes do fetch e só se aplica se ainda
   * for a corrente — dados da sessão velha nunca reintroduzem o store/mapa.
   */
  pollingGeracao: number;
  online: boolean;
}

const [localState, setLocalState] = createStore<LocalState>({
  nodes: {},
  selected: null,
  auth: "verificando",
  authExigida: true,
  diasLembrar: 30,
  pollingGeracao: 0,
  online: false,
});

// Substitui a lista inteira (reconcile remove nós que sumiram do latest).
const setNodes = (list: NodeInfo[]) => {
  setLocalState(
    "nodes",
    reconcile(Object.fromEntries(list.map((n) => [n.nodeNum, n]))),
  );
};

const select = (nodeNum: number | null) => setLocalState("selected", nodeNum);
const setAuth = (a: AuthEstado) => setLocalState("auth", a);
const setAuthExigida = (b: boolean) => setLocalState("authExigida", b);
const setDiasLembrar = (n: number) => setLocalState("diasLembrar", n);
const setOnline = (b: boolean) => setLocalState("online", b);
const bumpPollingGeracao = () => setLocalState("pollingGeracao", (n) => n + 1);

export const LocalState = {
  localState,
  setNodes,
  select,
  setAuth,
  setAuthExigida,
  setDiasLembrar,
  setOnline,
  bumpPollingGeracao,
};
