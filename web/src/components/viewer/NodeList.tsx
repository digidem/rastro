import type { Component } from "solid-js";
import { For, Match, Show, Switch, createEffect } from "solid-js";
import { useStore } from "../../hooks/useStore.jsx";
import {
  sufixoLeitura,
  tituloBateriaCritica,
  tituloGatewayMudo,
} from "../../lib/alertas.js";
import { ANCORADO_MAX_MS, duracaoLabel } from "../../lib/dwell.js";
import {
  type BatteryLevel,
  batteryLabel,
  batteryLevel,
  hardwareModelLabel,
  hasConfirmedPosition,
  isAgeWarning,
  isFixStale,
  nodeAgeLabel,
  nodeKind,
  nodeSidebarSvgUrl,
} from "../../lib/nodes.js";
import type {
  Movimento,
  NodeAlertType,
  NodeInfo,
  NodeKind,
} from "../../store.js";
import { LogoLoader } from "../ui/LogoLoader.jsx";
import { Button } from "../ui/button.jsx";
import { Text } from "../ui/text.jsx";

const ROTULO_CATEGORIA = new Map<NodeKind, string>([
  ["boat", "Barco"],
  ["fixed_station", "Base"],
  ["handheld", "Portátil"],
  ["unknown", "Não informado"],
]);

/** "parado desde" só se o último fix da parada ainda é recente pelo relógio atual. */
const paradaFresca = (m: Movimento, agoraMs: number): boolean =>
  m.desdeMs !== null &&
  m.ultimoFixMs !== null &&
  agoraMs - m.ultimoFixMs < ANCORADO_MAX_MS;

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
  inactiveCount?: () => number;
  nowMs: () => number;
}

/** Lista rolável da malha que ocupa todo o espaço vertical disponível. */
export const NodeList: Component<NodeListProps> = (props) => {
  const { localState, select, resetFilters, toggleShowInactive } = useStore();
  let listRef: HTMLDivElement | undefined;

  // Alerta ativo do nó (gateway_mudo/bateria_critica): base dos badges.
  const alertaDe = (nodeNum: number, tipo: NodeAlertType) =>
    (localState.alerts[nodeNum] ?? []).find((a) => a.alertType === tipo);

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
      {/* Header com contagem e toggle de inativos */}
      <div class="flex items-center justify-between px-3.5 py-2.5 border-b border-slate-800 bg-slate-900/80 shrink-0 gap-2">
        <Text class="text-xs uppercase font-bold tracking-wider text-slate-400">
          Nós recebidos
        </Text>
        <div class="flex items-center gap-1.5">
          <Show when={props.inactiveCount && props.inactiveCount() > 0}>
            <button
              type="button"
              aria-pressed={localState.showInactive}
              onClick={() => toggleShowInactive()}
              title={
                localState.showInactive
                  ? "Ocultar nós inativos (> 7 dias)"
                  : "Exibir nós inativos (> 7 dias)"
              }
              class={`text-[11px] font-semibold px-2 py-0.5 rounded-full border transition-colors flex items-center gap-1 ${
                localState.showInactive
                  ? "bg-amber-950/70 border-amber-500/80 text-amber-200"
                  : "bg-slate-800/80 hover:bg-slate-700/80 border-slate-700/80 text-slate-400"
              }`}
            >
              <span>
                {localState.showInactive ? "Ocultar >7d" : "Mostrar >7d"}
              </span>
              <span class="rounded-full bg-slate-900/80 px-1 py-0 text-[10px] tabular-nums font-mono text-slate-300">
                {props.inactiveCount?.() ?? 0}
              </span>
            </button>
          </Show>
          <span class="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs font-semibold text-slate-300 border border-slate-700/80">
            {props.filteredCount() === props.totalCount()
              ? `${props.totalCount()} nós`
              : `${props.filteredCount()} de ${props.totalCount()} nós`}
          </span>
        </div>
      </div>

      <Switch>
        <Match when={props.totalCount() === 0}>
          <Vazio
            status={localState.latestStatus}
            onLimpar={resetFilters}
            podeLimpar={false}
          />
        </Match>
        <Match
          when={
            props.filteredCount() === 0 &&
            (props.inactiveCount?.() ?? 0) > 0 &&
            !localState.showInactive
          }
        >
          <div class="flex flex-col items-start gap-2.5 p-4 text-xs">
            <Text class="text-slate-300">
              {props.inactiveCount?.() === 1
                ? "1 nó recebido está oculto por ter mais de 7 dias sem sinal."
                : `${props.inactiveCount?.()} nós recebidos estão ocultos por terem mais de 7 dias sem sinal.`}
            </Text>
            <button
              type="button"
              class="rounded-lg bg-amber-950/70 hover:bg-amber-900/80 text-amber-200 border border-amber-500/80 px-3 py-1.5 text-xs font-semibold transition-colors flex items-center gap-1.5"
              onClick={() => toggleShowInactive()}
            >
              <span>Mostrar nós inativos</span>
              <span class="rounded-full bg-slate-900/80 px-1.5 py-0.2 text-[10px] font-mono text-slate-300">
                {props.inactiveCount?.()}
              </span>
            </button>
          </div>
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

                  {/* Ícone SVG (placa do rádio/hardware; ícone de barco é exclusivo do mapa) */}
                  <div class="h-9 w-9 shrink-0 rounded-md bg-slate-900 border border-slate-700/70 p-1 flex items-center justify-center">
                    <img
                      src={nodeSidebarSvgUrl(n)}
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
                      <Show when={hardwareModelLabel(n)}>
                        {(hw) => (
                          <>
                            <span aria-hidden="true" class="text-slate-600">
                              ·
                            </span>
                            <span class="text-[11px] font-medium text-slate-300 bg-slate-800/80 px-1 py-0.5 rounded border border-slate-700/60">
                              {hw()}
                            </span>
                          </>
                        )}
                      </Show>
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
                            ? isAgeWarning(n, props.nowMs())
                              ? "text-amber-400 font-semibold"
                              : "text-slate-400"
                            : "text-amber-400 font-medium"
                        }`}
                      >
                        {nodeAgeLabel(n, props.nowMs())}
                      </span>
                      <Show when={localState.movimento[n.nodeNum]}>
                        {(m) => (
                          <span
                            class={`rounded border px-1 text-[10px] font-semibold ${
                              m().parado
                                ? "border-sky-600/80 bg-sky-950/40 text-sky-300"
                                : "border-emerald-600/80 bg-emerald-950/40 text-emerald-300"
                            }`}
                          >
                            {m().parado
                              ? `⚓ Ancorado / Parado${
                                  paradaFresca(m(), props.nowMs())
                                    ? ` (há ${duracaoLabel(props.nowMs() - (m().desdeMs as number))})`
                                    : ""
                                }`
                              : `🟢 Navegando${
                                  m().velocidadeKmh !== null
                                    ? ` (${Math.round(m().velocidadeKmh as number)} km/h)`
                                    : ""
                                }`}
                          </span>
                        )}
                      </Show>
                      <Show when={isAgeWarning(n, props.nowMs())}>
                        <span
                          class="rounded border border-amber-600/80 bg-amber-950/40 px-1 text-[10px] text-amber-300 font-semibold"
                          title={
                            n.timeFlag
                              ? `Alerta de horário: ${n.timeFlag}`
                              : "Fix com mais de 1 h"
                          }
                        >
                          {n.timeFlag ? "Horário suspeito" : "Fix > 1h"}
                        </span>
                      </Show>
                      <Show when={isFixStale(n.posTime, props.nowMs())}>
                        <span
                          class="rounded border border-amber-600/80 bg-amber-950/40 px-1 text-[10px] text-amber-300 font-semibold"
                          title="Fix mais antigo que 12 h"
                        >
                          Fix antigo
                        </span>
                      </Show>
                      <Show when={alertaDe(n.nodeNum, "gateway_mudo")}>
                        {(alerta) => (
                          <span
                            class="rounded border border-sky-600/80 bg-sky-950/40 px-1 text-[10px] text-sky-300 font-semibold"
                            title={tituloGatewayMudo(alerta(), props.nowMs())}
                          >
                            📡 Sem sinal (&gt;1h)
                          </span>
                        )}
                      </Show>
                      <Show when={alertaDe(n.nodeNum, "bateria_critica")}>
                        {(alerta) => (
                          <span
                            class="rounded border border-red-600/80 bg-red-950/40 px-1 text-[10px] text-red-300 font-semibold"
                            title={tituloBateriaCritica(
                              alerta(),
                              props.nowMs(),
                            )}
                          >
                            🪫 Bateria crítica
                            {sufixoLeitura(alerta(), props.nowMs())}
                          </span>
                        )}
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
        <div class="py-2">
          <LogoLoader size="sm" text="Aguardando dados…" />
        </div>
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
