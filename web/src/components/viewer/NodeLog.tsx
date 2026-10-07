import type { Component } from "solid-js";
import {
  For,
  Match,
  Show,
  Switch,
  createEffect,
  createSignal,
  on,
  onCleanup,
} from "solid-js";
import {
  ArrowLeftIcon,
  BatteryMediumIcon,
  ChatCircleTextIcon,
  MapPinIcon,
  WarningIcon,
} from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";
import {
  ROTULO_EVENTO,
  dayKey,
  eventDetails,
  eventHeadline,
  formatClock,
  formatDay,
} from "../../lib/nodeEvents.js";
import { useData } from "../../providers/DataProvider.jsx";
import {
  ErrTokenInvalid,
  type NodeEvent,
  type NodeEventKind,
} from "../../providers/api.js";
import type { NodeInfo } from "../../store.js";

const PAGE_SIZE = 50;
/** Atualização ao vivo da primeira página (mesma cadência do polling do mapa). */
const LIVE_MS = 15000;

const FILTROS: { id: NodeEventKind | "all"; label: string }[] = [
  { id: "all", label: "Todos" },
  { id: "pos", label: "Posição" },
  { id: "telem", label: "Telemetria" },
  { id: "msg", label: "Mensagens" },
];

export interface NodeLogProps {
  node: () => NodeInfo;
  onBack: () => void;
}

const nodeHex = (n: NodeInfo): string =>
  n.nodeId !== "" ? n.nodeId : `!${n.nodeNum.toString(16)}`;

/** Registros do nó (posição, telemetria, mensagens) com paginação e ao vivo. */
export const NodeLog: Component<NodeLogProps> = (props) => {
  const { api, clearSessionData } = useData();
  const { setAuth } = useStore();
  const { centerOnPoint } = useMap();

  const [filtro, setFiltro] = createSignal<NodeEventKind | "all">("all");
  // Pilha de cursores: [null] = primeira página; cada "Próxima" empilha.
  const [cursores, setCursores] = createSignal<(string | null)[]>([null]);
  const [eventos, setEventos] = createSignal<NodeEvent[]>([]);
  const [proximo, setProximo] = createSignal<string | null>(null);
  const [carregando, setCarregando] = createSignal(true);
  const [erro, setErro] = createSignal("");
  const [atualizadoEm, setAtualizadoEm] = createSignal<number | null>(null);

  const pagina = () => cursores().length;
  let geracao = 0;

  const carregar = async (silencioso: boolean) => {
    const minha = ++geracao;
    if (!silencioso) {
      setCarregando(true);
    }
    const f = filtro();
    try {
      const res = await api.events(nodeHex(props.node()), {
        kinds: f === "all" ? undefined : [f],
        before: cursores()[cursores().length - 1],
        limit: PAGE_SIZE,
      });
      if (minha !== geracao) {
        return;
      }
      setEventos(res.events);
      setProximo(res.nextCursor);
      setErro("");
      setAtualizadoEm(Date.now());
    } catch (err) {
      if (minha !== geracao) {
        return;
      }
      if (err instanceof ErrTokenInvalid) {
        clearSessionData();
        setAuth("login");
        return;
      }
      setErro("Não foi possível carregar os registros.");
    } finally {
      if (minha === geracao) {
        setCarregando(false);
      }
    }
  };

  // Troca de nó ou filtro → volta à primeira página.
  createEffect(
    on(
      () => [props.node().nodeNum, filtro()] as const,
      () => {
        setCursores([null]);
        void carregar(false);
      },
    ),
  );
  // Mudança de página (sem trocar nó/filtro).
  createEffect(
    on(
      () => cursores().length,
      () => void carregar(false),
      { defer: true },
    ),
  );

  // Ao vivo: só a primeira página se renova sozinha (as demais são históricas).
  const timer = setInterval(() => {
    if (pagina() === 1 && !document.hidden) {
      void carregar(true);
    }
  }, LIVE_MS);
  onCleanup(() => {
    clearInterval(timer);
    geracao++;
  });

  const proxima = () => {
    const c = proximo();
    if (c !== null) {
      setCursores([...cursores(), c]);
    }
  };
  const anterior = () => {
    if (pagina() > 1) {
      setCursores(cursores().slice(0, -1));
    }
  };

  const rows = () => {
    let anterior = "";
    return eventos().map((e) => {
      const dia = dayKey(e.ts);
      const novoDia = dia !== anterior;
      anterior = dia;
      return { e, novoDia };
    });
  };

  const temPosicao = (e: NodeEvent) =>
    e.kind === "pos" && e.lat !== undefined && e.lon !== undefined;

  return (
    <div class="flex flex-col h-full min-h-0 bg-slate-950/90 text-slate-100">
      <div class="flex items-center gap-2 px-3 py-2.5 border-b border-slate-700/80 bg-slate-900/90 shrink-0">
        <button
          type="button"
          aria-label="Voltar para a lista de nós"
          title="Voltar"
          class="size-9 rounded-lg flex items-center justify-center text-slate-300 hover:text-white hover:bg-slate-800 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          onClick={() => props.onBack()}
        >
          <ArrowLeftIcon class="h-4.5 w-4.5" aria-hidden="true" />
        </button>
        <div class="min-w-0 flex-1">
          <h4 class="truncate font-bold text-white text-sm leading-snug">
            Registros · {props.node().nome}
          </h4>
          <div class="font-mono text-[11px] text-emerald-400">
            {nodeHex(props.node())}
          </div>
        </div>
        <Show when={pagina() === 1}>
          <span
            class="flex items-center gap-1 text-[10px] text-slate-400"
            title="A primeira página atualiza sozinha a cada 15 s"
          >
            <span class="size-1.5 rounded-full bg-emerald-400 animate-pulse" />
            ao vivo
          </span>
        </Show>
      </div>

      <div class="flex flex-wrap gap-1.5 px-3 py-2 border-b border-slate-800 shrink-0">
        <For each={FILTROS}>
          {(f) => (
            <button
              type="button"
              aria-pressed={filtro() === f.id}
              class={`rounded-full px-2.5 py-1 text-[11px] font-semibold border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                filtro() === f.id
                  ? "bg-emerald-500 border-emerald-400 text-slate-950"
                  : "bg-slate-900 border-slate-700 text-slate-300 hover:bg-slate-800"
              }`}
              onClick={() => setFiltro(f.id)}
            >
              {f.label}
            </button>
          )}
        </For>
      </div>

      <Show when={erro() !== ""}>
        <div
          role="alert"
          class="px-3 py-1.5 bg-red-950/80 border-b border-red-800 text-xs text-red-300 shrink-0"
        >
          {erro()}
        </div>
      </Show>

      <div class="flex-1 min-h-0 overflow-y-auto" aria-live="polite">
        <Show
          when={!(carregando() && eventos().length === 0)}
          fallback={
            <div class="p-4 text-xs text-slate-400">Carregando registros…</div>
          }
        >
          <Show
            when={eventos().length > 0}
            fallback={
              <div class="p-4 text-xs text-slate-400">Sem registros.</div>
            }
          >
            <ul class="divide-y divide-slate-800/80">
              <For each={rows()}>
                {({ e, novoDia }) => (
                  <>
                    <Show when={novoDia}>
                      <li class="sticky top-0 z-10 px-3 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-400 bg-slate-900/95 border-y border-slate-800">
                        {formatDay(e.ts)}
                      </li>
                    </Show>
                    <li>
                      <button
                        type="button"
                        disabled={!temPosicao(e)}
                        title={
                          temPosicao(e)
                            ? "Centralizar no mapa neste ponto"
                            : ROTULO_EVENTO.get(e.kind)
                        }
                        class={`w-full text-left px-3 py-2 flex gap-2.5 enabled:hover:bg-slate-900 disabled:cursor-default focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-400 ${
                          e.kind === "msg" && e.is_alert
                            ? "border-l-2 border-red-500 bg-red-950/30"
                            : "border-l-2 border-transparent"
                        }`}
                        onClick={() =>
                          temPosicao(e) &&
                          centerOnPoint(e.lon as number, e.lat as number)
                        }
                      >
                        <span class="mt-0.5 shrink-0 text-slate-400">
                          <Switch>
                            <Match when={e.kind === "pos"}>
                              <MapPinIcon
                                class="h-4 w-4"
                                aria-label="Posição"
                              />
                            </Match>
                            <Match when={e.kind === "telem"}>
                              <BatteryMediumIcon
                                class="h-4 w-4"
                                aria-label="Telemetria"
                              />
                            </Match>
                            <Match when={e.kind === "msg"}>
                              <ChatCircleTextIcon
                                class="h-4 w-4"
                                aria-label="Mensagem"
                              />
                            </Match>
                          </Switch>
                        </span>
                        <span class="min-w-0 flex-1">
                          <span class="flex items-baseline gap-2">
                            <span class="font-mono text-[11px] tabular-nums text-slate-400 shrink-0">
                              {formatClock(e.ts)}
                            </span>
                            <span
                              class={`text-xs break-words min-w-0 ${
                                e.kind === "pos"
                                  ? "font-mono text-slate-100"
                                  : "text-slate-100"
                              }`}
                            >
                              {eventHeadline(e)}
                            </span>
                          </span>
                          <span class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-slate-400">
                            <For each={eventDetails(e)}>
                              {(d) => <span>{d}</span>}
                            </For>
                            <Show when={e.kind === "msg" && e.is_alert}>
                              <span class="inline-flex items-center gap-0.5 font-bold text-red-300">
                                <WarningIcon
                                  class="h-3 w-3"
                                  aria-hidden="true"
                                />
                                alerta
                              </span>
                            </Show>
                            <Show when={e.kind === "pos" && e.time_flag}>
                              <span
                                class="rounded border border-amber-600/80 bg-amber-950/40 px-1 font-semibold text-amber-300"
                                title={`Alerta de horário: ${e.time_flag}`}
                              >
                                Horário suspeito
                              </span>
                            </Show>
                            <Show
                              when={
                                e.kind === "pos" && e.time_source === "gateway"
                              }
                            >
                              <span title="O dispositivo não informou hora; usada a do gateway">
                                hora do gateway
                              </span>
                            </Show>
                          </span>
                        </span>
                      </button>
                    </li>
                  </>
                )}
              </For>
            </ul>
          </Show>
        </Show>
      </div>

      <div class="flex items-center justify-between gap-2 px-3 py-2 border-t border-slate-700/80 bg-slate-900/90 shrink-0 text-xs">
        <button
          type="button"
          class="rounded-lg px-2.5 py-1.5 font-semibold border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:hover:bg-slate-900"
          disabled={pagina() === 1 || carregando()}
          onClick={anterior}
        >
          ‹ Mais novos
        </button>
        <span class="text-slate-400 tabular-nums">
          Página {pagina()}
          <Show when={atualizadoEm() !== null && pagina() === 1}>
            {" "}
            ·{" "}
            {new Date(atualizadoEm() as number).toLocaleTimeString("pt-BR", {
              hour12: false,
            })}
          </Show>
        </span>
        <button
          type="button"
          class="rounded-lg px-2.5 py-1.5 font-semibold border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:hover:bg-slate-900"
          disabled={proximo() === null || carregando()}
          onClick={proxima}
        >
          Mais antigos ›
        </button>
      </div>
    </div>
  );
};
