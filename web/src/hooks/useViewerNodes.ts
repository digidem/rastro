import { createMemo, onCleanup } from "solid-js";
import {
  hasConfirmedPosition,
  isNodeOlderThan7Days,
  matchesFilters,
} from "../lib/nodes.js";
import type { NodeInfo } from "../store.js";
import { LocalState } from "../store.js";

/**
 * Relógio do viewer: rótulos de idade e o filtro "fix antigo" avançam sozinhos
 * sem re-requisitar nada. Nenhum tick move a câmera.
 */
const TICK_MS = 60_000;

function compareLastSeenDesc(a: NodeInfo, b: NodeInfo): number {
  const timeA = a.posTime
    ? Date.parse(a.posTime)
    : a.age_s ?? a.ageS
      ? -((a.age_s ?? a.ageS) as number)
      : 0;
  const timeB = b.posTime
    ? Date.parse(b.posTime)
    : b.age_s ?? b.ageS
      ? -((b.age_s ?? b.ageS) as number)
      : 0;
  if (timeA !== timeB) {
    return timeB - timeA;
  }
  return a.nome.localeCompare(b.nome);
}

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

  const filteredNodes = createMemo(() => {
    const list = allNodes().filter((n) =>
      matchesFilters(
        n,
        {
          query: LocalState.localState.query,
          kindFilter: LocalState.localState.kindFilter,
          conditionFilter: LocalState.localState.conditionFilter,
        },
        nowMs(),
      ),
    );
    const ativos = LocalState.localState.showInactive
      ? list
      : list.filter((n) => !isNodeOlderThan7Days(n, nowMs()));
    return [...ativos].sort(compareLastSeenDesc);
  });

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
  const inactiveCount = createMemo(
    () => allNodes().filter((n) => isNodeOlderThan7Days(n, nowMs())).length,
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
    inactiveCount,
  };
}
