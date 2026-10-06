import type { Component } from "solid-js";
import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import {
  ArrowsOutIcon,
  ChatCircleTextIcon,
  CheckIcon,
  ListIcon,
  StackSimpleIcon,
} from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";
import type { BasemapMode } from "../../store.js";

export interface MapControlsProps {
  sidebarOpen: () => boolean;
  onToggleSidebar: () => void;
}

/** Ações explícitas do mapa: camadas, enquadrar todos, chat e abrir/fechar a sidebar em estilo pill dark. */
export const MapControls: Component<MapControlsProps> = (props) => {
  const { localState, setChatOpen, resetFilters, setBasemapMode } = useStore();
  const { fitAllNodes } = useMap();
  const [menuCamadasAberto, setMenuCamadasAberto] = createSignal(false);
  let menuRef: HTMLDivElement | undefined;
  let menuTriggerRef: HTMLButtonElement | undefined;

  // Fecha o menu de camadas ao clicar fora ou pressionar Escape
  createEffect(() => {
    if (!menuCamadasAberto()) {
      return;
    }
    const aoClicarFora = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (
        menuRef &&
        !menuRef.contains(target) &&
        menuTriggerRef &&
        !menuTriggerRef.contains(target)
      ) {
        setMenuCamadasAberto(false);
      }
    };
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuCamadasAberto(false);
        menuTriggerRef?.focus();
      }
    };
    document.addEventListener("pointerdown", aoClicarFora);
    document.addEventListener("keydown", aoTeclar);
    onCleanup(() => {
      document.removeEventListener("pointerdown", aoClicarFora);
      document.removeEventListener("keydown", aoTeclar);
    });
  });

  const enquadrarTodos = () => {
    resetFilters();
    fitAllNodes();
  };

  const selecionarModo = (modo: BasemapMode) => {
    setBasemapMode(modo);
    setMenuCamadasAberto(false);
  };

  const btnBase =
    "flex items-center justify-center h-10 w-10 min-h-[40px] min-w-[40px] p-0 md:h-[38px] md:min-h-[38px] md:w-auto md:px-3.5 md:gap-2 rounded-lg bg-slate-950/90 hover:bg-slate-800 text-slate-100 border border-slate-700/80 shadow-lg backdrop-blur-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400";

  return (
    <div class="flex w-full items-start justify-between md:justify-start md:gap-2">
      {/* Coluna da esquerda (Mobile: 2 linhas verticais na margem esquerda; Desktop: horizontal) */}
      <div class="pointer-events-auto flex flex-col md:flex-row items-center gap-2">
        {/* Linha 1: Botão de Camadas do Mapa Base */}
        <div class="relative">
          <button
            ref={menuTriggerRef}
            type="button"
            aria-expanded={menuCamadasAberto()}
            aria-haspopup="menu"
            aria-controls="menu-camadas"
            aria-label="Camadas do mapa base"
            class={btnBase}
            title="Alternar camadas do mapa base"
            onClick={() => setMenuCamadasAberto((v) => !v)}
          >
            <StackSimpleIcon
              class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
              aria-hidden="true"
            />
            <span class="hidden md:inline">Camadas</span>
          </button>

          <Show when={menuCamadasAberto()}>
            <div
              ref={menuRef}
              id="menu-camadas"
              role="menu"
              aria-label="Camadas do mapa"
              class="absolute left-0 top-full mt-1.5 w-48 rounded-lg bg-slate-950/95 border border-slate-700/80 shadow-2xl backdrop-blur-md p-1 z-30 flex flex-col gap-0.5 text-xs"
            >
              <button
                type="button"
                role="menuitemradio"
                aria-checked={localState.basemapMode === "satellite"}
                class={`flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-left transition-colors ${
                  localState.basemapMode === "satellite"
                    ? "bg-slate-800 text-emerald-400 font-semibold"
                    : "text-slate-200 hover:bg-slate-900"
                }`}
                onClick={() => selecionarModo("satellite")}
              >
                <span>Satélite (Padrão)</span>
                <Show when={localState.basemapMode === "satellite"}>
                  <CheckIcon
                    class="h-3.5 w-3.5 text-emerald-400"
                    aria-hidden="true"
                  />
                </Show>
              </button>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={localState.basemapMode === "osm"}
                class={`flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-left transition-colors ${
                  localState.basemapMode === "osm"
                    ? "bg-slate-800 text-emerald-400 font-semibold"
                    : "text-slate-200 hover:bg-slate-900"
                }`}
                onClick={() => selecionarModo("osm")}
              >
                <span>OpenStreetMap</span>
                <Show when={localState.basemapMode === "osm"}>
                  <CheckIcon
                    class="h-3.5 w-3.5 text-emerald-400"
                    aria-hidden="true"
                  />
                </Show>
              </button>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={localState.basemapMode === "local"}
                class={`flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-left transition-colors ${
                  localState.basemapMode === "local"
                    ? "bg-slate-800 text-emerald-400 font-semibold"
                    : "text-slate-200 hover:bg-slate-900"
                }`}
                onClick={() => selecionarModo("local")}
              >
                <span>Offline (PMTiles)</span>
                <Show when={localState.basemapMode === "local"}>
                  <CheckIcon
                    class="h-3.5 w-3.5 text-emerald-400"
                    aria-hidden="true"
                  />
                </Show>
              </button>
            </div>
          </Show>
        </div>

        {/* Linha 2: Enquadrar todos os nós */}
        <button
          type="button"
          aria-label="Enquadrar todos os nós com posição"
          class={btnBase}
          title="Enquadrar todos os nós com posição"
          onClick={() => enquadrarTodos()}
        >
          <ArrowsOutIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
          <span class="hidden md:inline">Enquadrar todos</span>
        </button>
      </div>

      {/* Coluna da direita (Mobile: 2 linhas verticais na margem direita; Desktop: continuação da horizontal) */}
      <div
        class={`pointer-events-auto flex flex-col md:flex-row items-center gap-2 ${
          props.sidebarOpen() ? "hidden md:flex" : "flex"
        }`}
      >
        {/* Chat (Desktop: 3º botão; Mobile: 2ª linha da coluna direita) */}
        <button
          type="button"
          aria-expanded={localState.chatOpen}
          aria-label={
            localState.unreadChatCount > 0
              ? `Chat da malha, ${localState.unreadChatCount} não lidas`
              : "Chat da malha"
          }
          class={`relative order-2 md:order-1 ${btnBase}`}
          title={
            localState.chatOpen ? "Fechar chat da malha" : "Abrir chat da malha"
          }
          onClick={() => setChatOpen(!localState.chatOpen)}
        >
          <ChatCircleTextIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
          <span class="hidden md:inline">Chat</span>
          <Show when={localState.unreadChatCount > 0}>
            <span
              class={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full leading-none absolute -top-1.5 -right-1.5 md:static md:top-auto md:right-auto ${
                localState.hasAlertUnread
                  ? "bg-rose-500 text-white animate-pulse motion-reduce:animate-none"
                  : "bg-emerald-500 text-slate-950"
              }`}
            >
              {localState.unreadChatCount > 99
                ? "99+"
                : localState.unreadChatCount}
            </span>
          </Show>
        </button>

        {/* Nós da malha / Sidebar (Desktop: 4º botão; Mobile: 1ª linha da coluna direita) */}
        <button
          type="button"
          aria-expanded={props.sidebarOpen()}
          aria-label="Nós da malha"
          class={`order-1 md:order-2 ${btnBase}`}
          title={
            props.sidebarOpen() ? "Ocultar painel" : "Mostrar nós da malha"
          }
          onClick={() => props.onToggleSidebar()}
        >
          <ListIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
          <span class="hidden md:inline">
            {props.sidebarOpen() ? "Ocultar painel" : "Nós da malha"}
          </span>
        </button>
      </div>
    </div>
  );
};
