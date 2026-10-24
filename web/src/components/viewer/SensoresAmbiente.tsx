import type { Component } from "solid-js";
import { For, Show } from "solid-js";
import { useStore } from "../../hooks/useStore.jsx";
import { LeituraAmbienteLinha } from "./LeituraAmbienteLinha.jsx";

/**
 * Sensores com leitura ambiental. Lista todos, inclusive os sem posição:
 * /api/nodes/latest só traz nós com fix, então sem este bloco o sensor
 * (sem GPS) não teria como aparecer na tela.
 */
export const SensoresAmbiente: Component<{ nowMs: () => number }> = (props) => {
  const { localState } = useStore();
  const leituras = () =>
    Object.values(localState.ambiente).sort((a, b) =>
      a.nome.localeCompare(b.nome, "pt-BR"),
    );
  return (
    <Show when={leituras().length > 0}>
      <section
        class="shrink-0 border-t border-slate-700/80 px-3 py-2 text-xs"
        aria-label="Sensores"
      >
        <h3 class="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1">
          Sensores
        </h3>
        <ul class="flex flex-col gap-1">
          <For each={leituras()}>
            {(l) => (
              <li class="flex flex-col">
                <span class="font-medium text-slate-200 truncate">
                  {l.nome}
                </span>
                <LeituraAmbienteLinha leitura={l} nowMs={props.nowMs()} />
              </li>
            )}
          </For>
        </ul>
      </section>
    </Show>
  );
};
