import type { Component } from "solid-js";
import { Show } from "solid-js";
import {
  ArrowsOutIcon,
  ChatCircleTextIcon,
  ListIcon,
} from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";

export interface MapControlsProps {
  sidebarOpen: () => boolean;
  onToggleSidebar: () => void;
}

/** Ações explícitas do mapa: enquadrar todos, chat e abrir/fechar a sidebar em estilo pill dark. */
export const MapControls: Component<MapControlsProps> = (props) => {
  const { localState, setChatOpen, resetFilters } = useStore();
  const { fitAllNodes } = useMap();

  const enquadrarTodos = () => {
    resetFilters();
    fitAllNodes();
  };

  return (
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="flex items-center gap-2 h-9.5 min-h-[38px] px-3.5 rounded-lg bg-slate-950/90 hover:bg-slate-800 text-slate-100 border border-slate-700/80 shadow-lg backdrop-blur-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        title="Enquadrar todos os nós com posição"
        onClick={() => enquadrarTodos()}
      >
        <ArrowsOutIcon class="h-4 w-4 text-emerald-400" aria-hidden="true" />
        <span>Enquadrar todos</span>
      </button>

      <button
        type="button"
        aria-pressed={localState.chatOpen}
        class="relative flex items-center gap-2 h-9.5 min-h-[38px] px-3.5 rounded-lg bg-slate-950/90 hover:bg-slate-800 text-slate-100 border border-slate-700/80 shadow-lg backdrop-blur-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        title={
          localState.chatOpen ? "Fechar chat da malha" : "Abrir chat da malha"
        }
        onClick={() => setChatOpen(!localState.chatOpen)}
      >
        <ChatCircleTextIcon
          class="h-4 w-4 text-emerald-400"
          aria-hidden="true"
        />
        <span>Chat</span>
        <Show when={localState.unreadChatCount > 0}>
          <span
            class={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full leading-none ${
              localState.hasAlertUnread
                ? "bg-rose-500 text-white animate-pulse"
                : "bg-emerald-500 text-slate-950"
            }`}
          >
            {localState.unreadChatCount > 99
              ? "99+"
              : localState.unreadChatCount}
          </span>
        </Show>
      </button>

      <button
        type="button"
        aria-pressed={props.sidebarOpen()}
        class="flex items-center gap-2 h-9.5 min-h-[38px] px-3.5 rounded-lg bg-slate-950/90 hover:bg-slate-800 text-slate-100 border border-slate-700/80 shadow-lg backdrop-blur-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        title={props.sidebarOpen() ? "Ocultar painel" : "Mostrar nós da malha"}
        onClick={() => props.onToggleSidebar()}
      >
        <ListIcon class="h-4 w-4 text-emerald-400" aria-hidden="true" />
        <span>{props.sidebarOpen() ? "Ocultar painel" : "Nós da malha"}</span>
      </button>
    </div>
  );
};
