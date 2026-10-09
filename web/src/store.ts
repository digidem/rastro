import { createStore, reconcile } from "solid-js/store";

/** Categoria operacional do nó; "unknown" quando o contrato não informa. */
export type NodeKind = "boat" | "fixed_station" | "handheld" | "unknown";

/** Modo do mapa base de fundo (default: satellite). */
export type BasemapMode = "google" | "satellite" | "osm" | "local";

/** Filtro de categoria: "all" inclui os nós sem categoria conhecida. */
export type KindFilter = "all" | NodeKind;

/** Filtro de condição: independe da categoria e da busca (combinação AND). */
export type ConditionFilter = "all" | "no-position" | "stale";

/** Estado da última rodada de /api/nodes/latest (distingue carga de vazio). */
export type LatestStatus = "idle" | "loading" | "ready" | "error";

/** Tipo de alerta de campo (GET /api/alerts). */
export type NodeAlertType = "gateway_mudo" | "bateria_critica";

/** Alerta ativo de um nó (GET /api/alerts), normalizado do snake_case cru. */
export interface NodeAlert {
  nodeNum: number | null;
  alertId: string;
  nodeId: string | null;
  nodeNome: string | null;
  alertType: NodeAlertType;
  severity: string;
  triggeredAt: string;
  details: Record<string, unknown> | null;
}

/** Nó visto pela última rodada de /api/nodes/latest. */
export interface NodeInfo {
  nodeNum: number;
  nodeId: string;
  nome: string;
  posTime: string | null;
  battery: number | null;
  lon: number;
  lat: number;
  /**
   * Metadados ampliados do contrato. Todos opcionais: payload antigo (ou
   * resposta sem o campo) simplesmente não os traz — o shape anterior é
   * preservado.
   */
  shortName?: string | null;
  kind?: NodeKind;
  hwModel?: string | null;
  altitudeM?: number | null;
  sats?: number | null;
  timeSource?: string | null;
  receivedAt?: string | null;
  bearing?: number | null;
  ageS?: number | null;
  age_s?: number | null;
  timeFlag?: string | null;
  time_flag?: string | null;
}

/**
 * Estado da sessão no viewer. Os campos de filtro/seleção vivem aqui para que
 * lista, inspector e mapa derivem tudo dos mesmos dados (fonte única).
 */
export type AuthEstado = "verificando" | "login" | "ok";

/** Estado de movimento derivado da trilha (ST-DAH); vive fora de `nodes` para sobreviver ao reconcile do polling. */
export interface Movimento {
  parado: boolean;
  /** Início da parada em curso (ms); null se navegando. */
  desdeMs: number | null;
  /** Último fix da parada em curso (ms); a lista só mostra "há X" se for recente. */
  ultimoFixMs: number | null;
  velocidadeKmh: number | null;
}

interface LocalState {
  nodes: Record<number, NodeInfo>;
  /** Alertas ativos por nodeNum (GET /api/alerts, polling do DataProvider). */
  alerts: Record<number, NodeAlert[]>;
  movimento: Record<number, Movimento>;
  selected: number | null;
  /** Busca textual: nome, nome curto, nodeId ou hex do nodeNum. */
  query: string;
  /** Categoria exclusiva; "all" inclui "unknown". */
  kindFilter: KindFilter;
  /** Condição independente da categoria. */
  conditionFilter: ConditionFilter;
  /** Relógio reativo do visualizador (atualizado a cada minuto). */
  nowMs: number;
  /** Estado da última rodada de latest() — distingue carga, erro e vazio. */
  latestStatus: LatestStatus;
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
  chatOpen: boolean;
  unreadChatCount: number;
  hasAlertUnread: boolean;
  /** Exibir nós inativos com mais de 7 dias (default: false, nós > 7d ficam ocultos). */
  showInactive: boolean;
  /** Camada do mapa base ativa: google (padrão), satellite (Esri), osm ou local. */
  basemapMode: BasemapMode;
  /** Mostra todos os fixes brutos da trilha, inclusive paradas e spikes (default: false). */
  mostrarFixesBrutos: boolean;
  /** A trilha do nó selecionado já foi analisada e está no mapa (ativa o toggle de fixes). */
  trilhaCarregada: boolean;
}

const carregarFixesBrutosPadrao = (): boolean => {
  try {
    return window.localStorage.getItem("rastro_fixes_brutos") === "1";
  } catch {
    // Storage indisponível (modo privado estrito, bloqueado): usa o padrão
    return false;
  }
};

const carregarBasemapPadrao = (): BasemapMode => {
  if (typeof window !== "undefined" && window.localStorage) {
    const salvo = window.localStorage.getItem("rastro_basemap");
    if (
      salvo === "google" ||
      salvo === "satellite" ||
      salvo === "osm" ||
      salvo === "local"
    ) {
      return salvo;
    }
  }
  return "google";
};

const [localState, setLocalState] = createStore<LocalState>({
  nodes: {},
  alerts: {},
  movimento: {},
  selected: null,
  query: "",
  kindFilter: "all",
  conditionFilter: "all",
  nowMs: Date.now(),
  latestStatus: "idle",
  auth: "verificando",
  authExigida: true,
  diasLembrar: 30,
  pollingGeracao: 0,
  online: false,
  chatOpen: false,
  unreadChatCount: 0,
  hasAlertUnread: false,
  showInactive: false,
  basemapMode: carregarBasemapPadrao(),
  mostrarFixesBrutos: carregarFixesBrutosPadrao(),
  trilhaCarregada: false,
});

// Substitui a lista inteira (reconcile remove nós que sumiram do latest).
const setNodes = (list: NodeInfo[]) => {
  setLocalState(
    "nodes",
    reconcile(Object.fromEntries(list.map((n) => [n.nodeNum, n]))),
  );
};

/** Substitui o mapa de alertas por nodeNum (reconcile limpa os resolvidos). */
const setAlerts = (list: NodeAlert[]) => {
  const mapa: Record<number, NodeAlert[]> = {};
  for (const a of list) {
    if (a.nodeNum === null) {
      continue;
    }
    const fila = mapa[a.nodeNum];
    if (fila) {
      fila.push(a);
    } else {
      mapa[a.nodeNum] = [a];
    }
  }
  setLocalState("alerts", reconcile(mapa));
};

const select = (nodeNum: number | null) => setLocalState("selected", nodeNum);
const setQuery = (q: string) => setLocalState("query", q);
const setKindFilter = (f: KindFilter) => setLocalState("kindFilter", f);
const setConditionFilter = (c: ConditionFilter) =>
  setLocalState("conditionFilter", c);
const tickNow = (ms = Date.now()) => setLocalState("nowMs", ms);
const setLatestStatus = (s: LatestStatus) => setLocalState("latestStatus", s);
const setAuth = (a: AuthEstado) => setLocalState("auth", a);
const setAuthExigida = (b: boolean) => setLocalState("authExigida", b);
const setDiasLembrar = (n: number) => setLocalState("diasLembrar", n);
const setOnline = (b: boolean) => setLocalState("online", b);
const bumpPollingGeracao = () => setLocalState("pollingGeracao", (n) => n + 1);

const setChatOpen = (open: boolean) => {
  setLocalState("chatOpen", open);
  if (open) {
    setLocalState("unreadChatCount", 0);
    setLocalState("hasAlertUnread", false);
  }
};
const setUnreadChatCount = (n: number | ((prev: number) => number)) => {
  setLocalState("unreadChatCount", n);
};
const setHasAlertUnread = (b: boolean) => setLocalState("hasAlertUnread", b);
const setShowInactive = (show: boolean) => setLocalState("showInactive", show);
const toggleShowInactive = () => setLocalState("showInactive", (v) => !v);
const setBasemapMode = (mode: BasemapMode) => {
  setLocalState("basemapMode", mode);
  if (typeof window !== "undefined" && window.localStorage) {
    try {
      window.localStorage.setItem("rastro_basemap", mode);
    } catch {
      // Ignora falhas de localStorage (ex: quota ou modo privado estrito)
    }
  }
};

/** Limpa busca e filtros; mantém seleção e status de carga. */
const resetFilters = () => {
  setLocalState("query", "");
  setLocalState("kindFilter", "all");
  setLocalState("conditionFilter", "all");
};

/**
 * Volta o viewer ao estado de sessão nova (logout/401): filtros limpos, sem
 * status de carga pendente e sem nó selecionado.
 */
const resetViewerState = () => {
  resetFilters();
  setLocalState("latestStatus", "idle");
  select(null);
  tickNow();
  setLocalState("chatOpen", false);
  setLocalState("unreadChatCount", 0);
  setLocalState("hasAlertUnread", false);
  setLocalState("showInactive", false);
  setLocalState("alerts", reconcile({}));
  setLocalState("movimento", reconcile({})); // estado de parada da sessão velha não vaza
  setLocalState("trilhaCarregada", false);
};

const setMostrarFixesBrutos = (mostrar: boolean) => {
  setLocalState("mostrarFixesBrutos", mostrar);
  try {
    window.localStorage.setItem("rastro_fixes_brutos", mostrar ? "1" : "0");
  } catch {
    // Ignora falhas de localStorage (ex: quota ou modo privado estrito)
  }
};

const setTrilhaCarregada = (b: boolean) => setLocalState("trilhaCarregada", b);

const setNodeBearing = (nodeNum: number, bearing: number | null) => {
  if (localState.nodes[nodeNum]) {
    setLocalState("nodes", nodeNum, "bearing", bearing);
  }
};

const setNodeMovimento = (nodeNum: number, m: Movimento) =>
  setLocalState("movimento", nodeNum, m);

export const LocalState = {
  localState,
  setNodes,
  setAlerts,
  select,
  setNodeBearing,
  setNodeMovimento,
  setQuery,
  setKindFilter,
  setConditionFilter,
  setShowInactive,
  toggleShowInactive,
  setBasemapMode,
  setMostrarFixesBrutos,
  setTrilhaCarregada,
  tickNow,
  setLatestStatus,
  setAuth,
  setAuthExigida,
  setDiasLembrar,
  setOnline,
  bumpPollingGeracao,
  setChatOpen,
  setUnreadChatCount,
  setHasAlertUnread,
  resetFilters,
  resetViewerState,
};
