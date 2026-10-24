import type { Component } from "solid-js";
import { Show } from "solid-js";
import {
  ambienteDesatualizado,
  formatarAmbiente,
  rotuloIdadeAmbiente,
} from "../../lib/ambiente.js";
import type { LeituraAmbiente } from "../../store.js";

/** Medidas do BME280 + idade; usado no inspetor e na lista de sensores. */
export const LeituraAmbienteLinha: Component<{
  leitura: LeituraAmbiente;
  nowMs: number;
}> = (props) => (
  <span class="inline-flex items-center gap-1.5 flex-wrap">
    <span class="font-mono tabular-nums text-slate-200">
      {formatarAmbiente(props.leitura)}
    </span>
    <span class="text-[11px] text-slate-400">
      {rotuloIdadeAmbiente(props.leitura, props.nowMs)}
    </span>
    <Show when={ambienteDesatualizado(props.leitura, props.nowMs)}>
      <span class="rounded border border-amber-600/80 bg-amber-950/40 px-1 text-[10px] text-amber-300 font-semibold">
        desatualizada
      </span>
    </Show>
  </span>
);
