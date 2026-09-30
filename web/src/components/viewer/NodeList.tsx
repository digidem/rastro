import type { Component } from "solid-js";
import { For, Match, Show, Switch, createEffect } from "solid-js";
import { useStore } from "../../hooks/useStore.jsx";
import {
  type BatteryLevel,
  batteryLabel,
  batteryLevel,
  deviceModelSvgUrl,
  fixAgeLabel,
  hasConfirmedPosition,
  isFixStale,
  nodeKind,
} from "../../lib/nodes.js";
import type { NodeInfo, NodeKind } from "../../store.js";
import { Button } from "../ui/button.jsx";
import { Text } from "../ui/text.jsx";

const ROTULO_CATEGORIA = new Map<NodeKind, string>([
  ["boat", "Barco"],
  ["fixed_station", "Base"],
  ["handheld", "Portátil"],
  ["unknown", "Não informado"],
]);

const COR_BATERIA = new Map<BatteryLevel, string>([
  ["none", "text-slate-500"],
  ["out-of-scale", "text-amber-400 font-semibold"],
  ["critical", "text-red-400 font-bold"],
  ["low", "text-amber-300 font-bold"],
  ["ok", "text-emerald-400 font-medium"],
]);

export interface NodeListProps {
  /** Nós que passaram pelos filtros (busca + categoria + condição). */
  nodes: () => NodeInfo[];
  totalCount: () => number;
  filteredCount: () => number;
  nowMs: () => number;
}

/** Lista rolável da malha que ocupa todo o espaço vertical disponível. */
export const NodeList: Component<NodeListProps> = (props) => {
  const { localState, select, resetFilters } = useStore();
  let listRef: HTMLDivElement | undefined;

  // Ao selecionar um nó, rola suavemente para ele na lista
  createEffect(() => {
    const sel = localState.selected;
    if (sel !== null && listRef) {
      setTimeout(() => {
        const el = listRef?.querySelector(`[data-node-num="${sel}"]`);
        if (el) {
          el.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }, 50);
    }
  });

  return (
    <div class="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Header com contagem */}
      <div class="flex items-center justify-between px-3.5 py-2.5 border-b border-slate-800 bg-slate-900/80 shrink-0">
        <Text class="text-xs uppercase font-bold tracking-wider text-slate-400">
          Nós recebidos
        </Text>
        <span class="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs font-semibold text-slate-300 border border-slate-700/80">
          {props.filteredCount() === props.totalCount()
            ? `${props.totalCount()} nós`
            : `${props.filteredCount()} de ${props.totalCount()} nós`}
        </span>
      </div>

      <Switch>
        <Match when={props.totalCount() === 0}>
          <Vazio
            status={localState.latestStatus}
            onLimpar={resetFilters}
            podeLimpar={false}
          />
        </Match>
        <Match when={props.filteredCount() === 0}>
          <Vazio status="ready" onLimpar={resetFilters} podeLimpar={true} />
        </Match>
        <Match when={props.filteredCount() > 0}>
          <div
            ref={listRef}
            class="min-h-0 flex-1 overflow-y-auto p-2 space-y-1.5 overscroll-contain scroll-py-2"
          >
            <For each={props.nodes()}>
              {(n) => (
                <button
                  type="button"
                  data-node-num={n.nodeNum}
                  aria-pressed={localState.selected === n.nodeNum}
                  aria-selected={localState.selected === n.nodeNum}
                  class={`relative w-full rounded-lg px-3 py-2.5 text-left transition-colors flex items-center gap-3 ${
                    localState.selected === n.nodeNum
                      ? "bg-emerald-950/60 border border-emerald-500/80 text-white shadow-sm ring-1 ring-emerald-500/30 pl-3.5"
                      : "hover:bg-slate-800/70 border border-slate-800/60 bg-slate-900/40 text-slate-200"
                  }`}
                  onClick={() =>
                    select(localState.selected === n.nodeNum ? null : n.nodeNum)
                  }
                >
                  <Show when={localState.selected === n.nodeNum}>
                    <span
                      class="absolute left-0 top-2 bottom-2 w-1 bg-emerald-400 rounded-r shadow-[0_0_6px_rgba(52,211,153,0.6)]"
                      aria-hidden="true"
                    />
                  </Show>

                  {/* Ícone SVG do modelo do dispositivo */}
                  <div class="h-9 w-9 shrink-0 rounded-md bg-slate-900 border border-slate-700/70 p-1 flex items-center justify-center">
                    <img
                      src={deviceModelSvgUrl(n.hwModel)}
                      alt=""
                      class="h-full w-full object-contain filter drop-shadow"
                      aria-hidden="true"
                    />
                  </div>

                  {/* Informações textuais */}
                  <div class="min-w-0 flex-1">
                    <div class="flex items-baseline justify-between gap-1">
                      <span class="truncate font-semibold text-slate-100 text-xs">
                        {n.nome}
                      </span>
                      <Show when={n.shortName}>
                        <span class="text-[11px] font-mono text-slate-400 shrink-0 font-medium">
                          {n.shortName}
                        </span>
                      </Show>
                    </div>

                    <div class="flex flex-wrap items-center gap-1.5 text-xs text-slate-400 mt-0.5">
                      <span class="text-slate-300 font-medium">
                        {ROTULO_CATEGORIA.get(nodeKind(n)) ?? ""}
                      </span>
                      <span aria-hidden="true" class="text-slate-600">
                        ·
                      </span>
                      <span
                        class={`tabular-nums ${COR_BATERIA.get(batteryLevel(n.battery)) ?? ""}`}
                      >
                        {batteryLabel(n.battery)}
                      </span>
                      <span aria-hidden="true" class="text-slate-600">
                        ·
                      </span>
                      <span
                        class={`tabular-nums ${
                          hasConfirmedPosition(n)
                            ? "text-slate-400"
                            : "text-amber-400 font-medium"
                        }`}
                      >
                        {fixAgeLabel(n.posTime, props.nowMs())}
                      </span>
                      <Show when={isFixStale(n.posTime, props.nowMs())}>
                        <span
                          class="rounded border border-amber-600/80 bg-amber-950/40 px-1 text-[10px] text-amber-300 font-semibold"
                          title="Fix mais antigo que 12 h"
                        >
                          Fix antigo
                        </span>
                      </Show>
                    </div>
                  </div>
                </button>
              )}
            </For>
          </div>
        </Match>
      </Switch>
    </div>
  );
};

interface VazioProps {
  status: "idle" | "loading" | "ready" | "error";
  podeLimpar: boolean;
  onLimpar: () => void;
}

/** Estados vazios: nunca confundir "aguardando" com "nada recebido". */
const Vazio: Component<VazioProps> = (props) => (
  <div class="flex flex-col items-start gap-2.5 p-4 text-xs">
    <Switch>
      <Match when={props.status === "idle" || props.status === "loading"}>
        <Text class="text-slate-400 font-medium">Aguardando dados…</Text>
      </Match>
      <Match when={props.status === "error"}>
        <Text class="text-red-400 font-medium">
          Não foi possível carregar os nós da malha.
        </Text>
      </Match>
      <Match when={props.podeLimpar}>
        <Text class="text-slate-300">
          Nenhum nó corresponde aos filtros selecionados.
        </Text>
        <Button
          variant="outline"
          size="sm"
          class="bg-slate-800 hover:bg-slate-700 text-slate-100 border-slate-600 text-xs"
          onClick={() => props.onLimpar()}
        >
          Limpar filtros
        </Button>
      </Match>
      <Match when={true}>
        <Text class="text-slate-400">Nenhum nó recebido.</Text>
      </Match>
    </Switch>
  </div>
);
