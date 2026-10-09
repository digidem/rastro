import type { Component } from "solid-js";
import { Show } from "solid-js";
import {
  ClockCounterClockwiseIcon,
  CrosshairSimpleIcon,
  RulerIcon,
  XIcon,
} from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";
import {
  sufixoLeitura,
  tituloBateriaCritica,
  tituloGatewayMudo,
} from "../../lib/alertas.js";
import {
  type BatteryLevel,
  batteryLevel,
  formatDateTimeJavari,
  hardwareModelLabel,
  hasConfirmedPosition,
  isAgeWarning,
  nodeAgeLabel,
  nodeKind,
  nodeSidebarSvgUrl,
} from "../../lib/nodes.js";
import type { NodeAlertType, NodeInfo, NodeKind } from "../../store.js";
import { Switch } from "../ui/switch.jsx";

const ROTULO_CATEGORIA = new Map<NodeKind, string>([
  ["boat", "Barco"],
  ["fixed_station", "Base"],
  ["handheld", "Portátil"],
  ["unknown", "Não informado"],
]);

const COR_BARRA = new Map<BatteryLevel, string>([
  ["none", "battery-progress--none"],
  ["out-of-scale", "battery-progress--out-of-scale"],
  ["critical", "battery-progress--critical"],
  ["low", "battery-progress--low"],
  ["ok", "battery-progress--ok"],
]);

const COR_TEXTO = new Map<BatteryLevel, string>([
  ["none", "text-slate-400"],
  ["out-of-scale", "text-amber-400"],
  ["critical", "text-red-400"],
  ["low", "text-amber-300"],
  ["ok", "text-emerald-400"],
]);

const hexDoNo = (n: NodeInfo): string =>
  n.nodeId !== "" ? n.nodeId : `!${n.nodeNum.toString(16)}`;

const textoOuNaoInformado = (v: string | number | null | undefined): string =>
  v === null || v === undefined || v === "" ? "—" : String(v);

/** Liga os fixes brutos (paradas e spikes) no mapa; só aparece com trilha carregada. */
const ToggleFixesBrutos: Component = () => {
  const { localState, setMostrarFixesBrutos } = useStore();
  return (
    <Show when={localState.trilhaCarregada}>
      <Switch
        size="sm"
        checked={localState.mostrarFixesBrutos}
        onCheckedChange={(d) => setMostrarFixesBrutos(d.checked)}
      >
        Fixes brutos
      </Switch>
    </Show>
  );
};

export interface NodeInspectorProps {
  node: () => NodeInfo | undefined;
  nowMs: () => number;
  /** Abre a visão de registros do nó (substitui a lista na sidebar). */
  onOpenLog?: () => void;
}

/** Detalhe do nó selecionado: compacto, ergonômico e de alto contraste. */
export const NodeInspector: Component<NodeInspectorProps> = (props) => {
  const { localState, select, ativarRegua } = useStore();
  const { centerOnNode } = useMap();

  // Alerta ativo do nó: base dos badges (mesma fonte do NodeList).
  const alertaDe = (nodeNum: number, tipo: NodeAlertType) =>
    (localState.alerts[nodeNum] ?? []).find((a) => a.alertType === tipo);

  return (
    <Show when={props.node()}>
      {(node) => {
        const nivel = () => batteryLevel(node().battery);
        const percentual = () => {
          const b = node().battery;
          return b === null || b < 0 || b > 100 ? 0 : b;
        };
        const textoBateria = () => {
          const b = node().battery;
          if (b === null) {
            return "—";
          }
          if (b < 0 || b > 100) {
            return `${b} (fora)`;
          }
          return `${b}%`;
        };

        return (
          <div class="flex flex-col gap-3 p-3.5 bg-slate-950/90 text-slate-100">
            {/* Grupo 1: Identidade e Condição */}
            <div class="flex items-center gap-3">
              <div class="h-10 w-10 shrink-0 rounded-lg bg-slate-900 border border-slate-700/80 p-1 flex items-center justify-center shadow-inner">
                <img
                  src={nodeSidebarSvgUrl(node())}
                  alt={hardwareModelLabel(node()) ?? "Dispositivo"}
                  class="h-full w-full object-contain filter drop-shadow"
                />
              </div>

              <div class="min-w-0 flex-1">
                <h4 class="truncate font-bold text-white text-sm leading-snug">
                  {node().nome}
                </h4>
                <div class="flex items-center gap-1.5 text-xs text-slate-400 mt-0.5">
                  <Show when={node().shortName}>
                    <span class="font-medium text-slate-300">
                      {node().shortName}
                    </span>
                    <span class="text-slate-600">·</span>
                  </Show>
                  <span class="font-mono text-emerald-400 bg-slate-900 px-1 py-0.5 rounded border border-slate-800 text-[11px] font-medium">
                    {hexDoNo(node())}
                  </span>
                </div>
              </div>

              <button
                type="button"
                aria-label="Desselecionar nó"
                title="Fechar detalhe"
                class="size-9 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                onClick={() => select(null)}
              >
                <XIcon class="h-4.5 w-4.5" aria-hidden="true" />
              </button>
            </div>

            {/* Alertas de campo ativos (gateway_mudo / bateria_critica) */}
            <Show
              when={
                alertaDe(node().nodeNum, "gateway_mudo") ||
                alertaDe(node().nodeNum, "bateria_critica")
              }
            >
              <div class="flex flex-wrap gap-1.5">
                <Show when={alertaDe(node().nodeNum, "gateway_mudo")}>
                  {(alerta) => (
                    <span
                      class="rounded border border-sky-600/80 bg-sky-950/40 px-1 text-[10px] text-sky-300 font-semibold"
                      title={tituloGatewayMudo(alerta(), props.nowMs())}
                    >
                      📡 Sem sinal (&gt;1h)
                    </span>
                  )}
                </Show>
                <Show when={alertaDe(node().nodeNum, "bateria_critica")}>
                  {(alerta) => (
                    <span
                      class="rounded border border-red-600/80 bg-red-950/40 px-1 text-[10px] text-red-300 font-semibold"
                      title={tituloBateriaCritica(alerta(), props.nowMs())}
                    >
                      🪫 Bateria crítica
                      {sufixoLeitura(alerta(), props.nowMs())}
                    </span>
                  )}
                </Show>
              </div>
            </Show>

            {/* Grupo 2: Ação Primária */}
            <button
              type="button"
              class="w-full h-10 rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:bg-slate-800/80 disabled:text-slate-500 text-slate-950 text-xs font-bold flex items-center justify-center gap-2 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
              disabled={!hasConfirmedPosition(node())}
              onClick={() => centerOnNode(node().nodeNum)}
            >
              <CrosshairSimpleIcon
                class="h-4 w-4 text-slate-950 font-bold"
                aria-hidden="true"
              />
              <span>Centralizar no mapa</span>
            </button>

            {/* Liga a régua com o primeiro vértice ancorado neste nó */}
            <button
              type="button"
              class="w-full h-10 rounded-lg border border-slate-600 bg-slate-800/80 hover:bg-slate-700 disabled:border-slate-800 disabled:text-slate-500 text-slate-100 text-xs font-bold flex items-center justify-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
              disabled={!hasConfirmedPosition(node())}
              aria-label="Medir distância a partir deste nó"
              onClick={() => ativarRegua(node().nodeNum)}
            >
              <RulerIcon class="h-4 w-4 text-amber-400" aria-hidden="true" />
              <span>Medir daqui</span>
            </button>

            <Show when={props.onOpenLog}>
              <button
                type="button"
                class="w-full h-9 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-200 text-xs font-semibold flex items-center justify-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                onClick={() => props.onOpenLog?.()}
              >
                <ClockCounterClockwiseIcon class="h-4 w-4" aria-hidden="true" />
                <span>Ver registros</span>
              </button>
            </Show>

            <ToggleFixesBrutos />

            {/* Grupo 3: Telemetria e Especificações */}
            <div class="bg-slate-900/90 p-3 rounded-lg border border-slate-800/90 shadow-inner">
              <div class="grid grid-cols-[auto_1fr_auto_1fr] items-center gap-x-3 gap-y-2 text-xs">
                {/* Linha 1: Tipo e Bateria */}
                <span class="text-slate-400 text-[11px]">Tipo</span>
                <span class="font-medium text-slate-200 truncate">
                  {ROTULO_CATEGORIA.get(nodeKind(node())) ?? ""}
                </span>

                <span class="text-slate-400 text-[11px]">Bateria</span>
                <div class="flex items-center gap-1.5 justify-end">
                  <span
                    class={`font-bold tabular-nums ${COR_TEXTO.get(nivel()) ?? ""}`}
                  >
                    {textoBateria()}
                  </span>
                  <Show when={node().battery !== null}>
                    <progress
                      class={`battery-progress ${COR_BARRA.get(nivel()) ?? ""}`}
                      max="100"
                      value={percentual()}
                      aria-label="Bateria"
                    />
                  </Show>
                </div>

                {/* Linha 2: Modelo */}
                <span class="text-slate-400 text-[11px]">Modelo</span>
                <span class="font-mono text-slate-200 truncate col-span-3">
                  {hardwareModelLabel(node()) ??
                    textoOuNaoInformado(node().hwModel)}
                </span>

                {/* Posição geográfica */}
                <Show
                  when={hasConfirmedPosition(node())}
                  fallback={
                    <div class="col-span-4 text-amber-400 text-[11px] font-medium pt-0.5">
                      Sem posição confirmada
                    </div>
                  }
                >
                  <div class="col-span-4 border-t border-slate-800 my-0.5" />

                  <span class="text-slate-400 text-[11px]">Coord.</span>
                  <span class="font-mono text-slate-200 tabular-nums col-span-3">
                    {node().lat.toFixed(4)}, {node().lon.toFixed(4)}
                  </span>

                  <span class="text-slate-400 text-[11px]">Altitude</span>
                  <span class="font-mono text-slate-200 tabular-nums">
                    {node().altitudeM !== null && node().altitudeM !== undefined
                      ? `${node().altitudeM} m`
                      : "—"}
                  </span>

                  <span class="text-slate-400 text-[11px]">Satélites</span>
                  <span class="font-mono text-slate-200 tabular-nums text-right">
                    {node().sats !== null && node().sats !== undefined
                      ? `${node().sats} sats`
                      : "—"}
                  </span>
                </Show>
              </div>
            </div>

            {/* Frescor & Timestamp */}
            <div class="flex items-center justify-between text-xs px-1 text-slate-400">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span>Visto</span>
                <span
                  class={`font-semibold ${
                    isAgeWarning(node(), props.nowMs())
                      ? "text-amber-400 font-bold"
                      : "text-slate-200"
                  }`}
                >
                  {nodeAgeLabel(node(), props.nowMs())}
                </span>
                <Show when={isAgeWarning(node(), props.nowMs())}>
                  <span
                    class="rounded border border-amber-600/80 bg-amber-950/40 px-1 text-[10px] text-amber-300 font-semibold"
                    title={
                      node().timeFlag
                        ? `Alerta de horário: ${node().timeFlag}`
                        : "Fix com mais de 1 h"
                    }
                  >
                    {node().timeFlag ? "Horário suspeito" : "Fix > 1h"}
                  </span>
                </Show>
              </div>
              <div class="text-[11px] text-slate-500 font-mono">
                {formatDateTimeJavari(node().posTime)}
              </div>
            </div>
          </div>
        );
      }}
    </Show>
  );
};
