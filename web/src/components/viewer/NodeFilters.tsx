import type { Component } from "solid-js";
import { For, Show } from "solid-js";
import {
  CaretDownIcon,
  CheckIcon,
  MagnifyingGlassIcon,
  XIcon,
} from "solid-phosphor/regular";
import { useStore } from "../../hooks/useStore.jsx";
import type { ConditionFilter, KindFilter } from "../../store.js";
import { Input } from "../ui/input.jsx";
import {
  Content,
  Control,
  Indicator,
  Item,
  ItemIndicator,
  ItemText,
  Label,
  Positioner,
  Root,
  Trigger,
  ValueText,
} from "../ui/select.jsx";

interface OpcaoCondicao {
  label: string;
  value: ConditionFilter;
}

const CATEGORIAS: { rotulo: string; valor: KindFilter }[] = [
  { rotulo: "Todos", valor: "all" },
  { rotulo: "Barcos", valor: "boat" },
  { rotulo: "Bases", valor: "fixed_station" },
  { rotulo: "Portáteis", valor: "handheld" },
];

const CONDICOES: OpcaoCondicao[] = [
  { label: "Todas as condições", value: "all" },
  { label: "Sem posição", value: "no-position" },
  { label: "Fix antigo (>12h)", value: "stale" },
];

// O detalhe do Select traz string crua; só valores conhecidos entram no store.
const condicaoValida = (v: string | undefined): ConditionFilter => {
  switch (v) {
    case "no-position":
    case "stale":
      return v;
    default:
      return "all";
  }
};

/** Busca textual + chips de categoria + condição do fix (AND entre os três). */
export const NodeFilters: Component = () => {
  const { localState, setQuery, setKindFilter, setConditionFilter } =
    useStore();

  return (
    <div class="flex flex-col gap-3 border-b border-slate-700/80 bg-slate-900/60 px-3.5 py-3">
      {/* Campo de busca */}
      <div class="relative flex items-center">
        <MagnifyingGlassIcon
          class="pointer-events-none absolute left-3 h-4 w-4 text-slate-400"
          aria-hidden="true"
        />
        <Input
          type="search"
          aria-label="Buscar nó por nome ou hex"
          placeholder="Buscar nome, curto ou !hex"
          class="w-full h-9 pl-9 pr-8 bg-slate-950/80 border-slate-700 text-slate-100 placeholder:text-slate-500 focus:border-emerald-500 text-xs rounded-lg"
          value={localState.query}
          onInput={(e) => setQuery(e.currentTarget.value)}
        />
        <Show when={localState.query !== ""}>
          <button
            type="button"
            aria-label="Limpar busca"
            title="Limpar busca"
            class="absolute right-2 rounded p-1 text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            onClick={() => setQuery("")}
          >
            <XIcon class="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </Show>
      </div>

      {/* Chips de categoria */}
      <fieldset class="flex flex-wrap gap-1.5">
        <legend class="sr-only">Filtrar por categoria</legend>
        <For each={CATEGORIAS}>
          {(c) => (
            <button
              type="button"
              aria-pressed={localState.kindFilter === c.valor}
              class={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                localState.kindFilter === c.valor
                  ? "border-emerald-500 bg-emerald-500/20 text-emerald-300 font-semibold shadow-sm"
                  : "border-slate-700 bg-slate-800/80 text-slate-300 hover:bg-slate-700 hover:text-white"
              }`}
              onClick={() => setKindFilter(c.valor)}
            >
              {c.rotulo}
            </button>
          )}
        </For>
      </fieldset>

      {/* Select de Condição */}
      <Root
        items={CONDICOES}
        value={[localState.conditionFilter]}
        onValueChange={(detalhe) =>
          setConditionFilter(condicaoValida(detalhe.value[0]))
        }
      >
        <div class="flex items-center justify-between">
          <Label class="text-[11px] font-medium text-slate-400 uppercase tracking-wide">
            Condição
          </Label>
        </div>
        <Control>
          <Trigger class="w-full h-9 bg-slate-950/80 border-slate-700 text-slate-200 text-xs px-3 rounded-lg flex items-center justify-between">
            <ValueText />
            <Indicator>
              <CaretDownIcon
                class="h-3.5 w-3.5 text-slate-400"
                aria-hidden="true"
              />
            </Indicator>
          </Trigger>
        </Control>
        <Positioner>
          <Content class="bg-slate-900 border-slate-700 text-slate-100 shadow-2xl">
            <For each={CONDICOES}>
              {(opcao) => (
                <Item
                  item={opcao}
                  class="text-xs hover:bg-slate-800 text-slate-200"
                >
                  <ItemText>{opcao.label}</ItemText>
                  <ItemIndicator>
                    <CheckIcon
                      class="h-3.5 w-3.5 text-emerald-400"
                      aria-hidden="true"
                    />
                  </ItemIndicator>
                </Item>
              )}
            </For>
          </Content>
        </Positioner>
      </Root>
    </div>
  );
};
