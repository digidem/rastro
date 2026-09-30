import { createMemo, onCleanup } from "solid-js";
import { hasConfirmedPosition, matchesFilters } from "../lib/nodes.js";
import { LocalState } from "../store.js";

/**
 * Relógio do viewer: rótulos de idade e o filtro "fix antigo" avançam sozinhos
 * sem re-requisitar nada. Nenhum tick move a câmera.
 */
const TICK_MS = 60_000;

/**
 * Deriva a lista de nós (total, filtrada, selecionada e posicionada) a partir
 * do store. Criado UMA vez por sessão de tela (MapWindow) e repassado aos
 * componentes por accessors — nada aqui faz requisição.
 */
export function useViewerNodes() {
  const timer = setInterval(() => LocalState.tickNow(), TICK_MS);
  onCleanup(() => clearInterval(timer));

  const nowMs = () => LocalState.localState.nowMs;
  const allNodes = createMemo(() => Object.values(LocalState.localState.nodes));

  const filteredNodes = createMemo(() =>
    allNodes().filter((n) =>
      matchesFilters(
        n,
        {
          query: LocalState.localState.query,
          kindFilter: LocalState.localState.kindFilter,
          conditionFilter: LocalState.localState.conditionFilter,
        },
        nowMs(),
      ),
    ),
  );

  const selectedNode = createMemo(() => {
    const sel = LocalState.localState.selected;
    return sel === null ? undefined : LocalState.localState.nodes[sel];
  });

  const mapNodes = createMemo(() =>
    filteredNodes().filter(hasConfirmedPosition),
  );

  const totalCount = createMemo(() => allNodes().length);
  const filteredCount = createMemo(() => filteredNodes().length);
  const positionedCount = createMemo(
    () => allNodes().filter(hasConfirmedPosition).length,
  );

  return {
    nowMs,
    allNodes,
    filteredNodes,
    selectedNode,
    mapNodes,
    totalCount,
    filteredCount,
    positionedCount,
  };
}
