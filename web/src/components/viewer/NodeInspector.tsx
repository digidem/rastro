import type { Component } from "solid-js";
import { Show } from "solid-js";
import { CrosshairSimpleIcon, XIcon } from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";
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
import type { NodeInfo, NodeKind } from "../../store.js";

const ROTULO_CATEGORIA = new Map<NodeKind, string>([
  ["boat", "Barco"],
  ["fixed_station", "Base"],
  ["handheld", "Portátil"],
  ["unknown", "Não informado"],
]);

const COR_BARRA = new Map<BatteryLevel, string>([
  ["none", "bg-slate-600"],
  ["out-of-scale", "bg-amber-500"],
  ["critical", "bg-red-500"],
  ["low", "bg-amber-400"],
  ["ok", "bg-emerald-400"],
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

export interface NodeInspectorProps {
  node: () => NodeInfo | undefined;
  nowMs: () => number;
}

/** Detalhe do nó selecionado: compacto, ergonômico e de alto contraste. */
export const NodeInspector: Component<NodeInspectorProps> = (props) => {
  const { select } = useStore();
  const { centerOnNode } = useMap();

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
                    <div class="w-7 h-1.5 rounded-full bg-slate-800 overflow-hidden shrink-0">
                      <div
                        class={`h-full rounded-full ${COR_BARRA.get(nivel()) ?? ""}`}
                        style={{ width: `${percentual()}%` }}
                      />
                    </div>
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
