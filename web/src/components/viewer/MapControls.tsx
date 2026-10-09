import type { Component } from "solid-js";
import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import {
  ArrowsOutIcon,
  ChatCircleTextIcon,
  CheckIcon,
  ClockCounterClockwiseIcon,
  ListIcon,
  MagnifyingGlassMinusIcon,
  MagnifyingGlassPlusIcon,
  StackSimpleIcon,
} from "solid-phosphor/regular";
import { useMap } from "../../hooks/useMap.jsx";
import { useStore } from "../../hooks/useStore.jsx";
import {
  JANELAS_TRILHA_H,
  type JanelaTrilhaH,
  rotuloJanela,
  rotuloJanelaCurto,
} from "../../lib/trilhaJanela.js";
import type { BasemapMode } from "../../store.js";

export interface MapControlsProps {
  sidebarOpen: () => boolean;
  onToggleSidebar: () => void;
}

/**
 * Ações explícitas do mapa.
 * Esquerda (topo): chat e nós da malha (com texto no desktop).
 * Direita (coluna vertical): zoom +, zoom −, enquadrar todos e camadas (só ícones).
 */
export const MapControls: Component<MapControlsProps> = (props) => {
  const {
    localState,
    setChatOpen,
    resetFilters,
    setBasemapMode,
    setJanelaTrilhaH,
  } = useStore();
  const { fitAllNodes, zoomIn, zoomOut } = useMap();
  const [menuCamadasAberto, setMenuCamadasAberto] = createSignal(false);
  const [menuJanelaAberto, setMenuJanelaAberto] = createSignal(false);
  let menuRef: HTMLDivElement | undefined;
  let menuTriggerRef: HTMLButtonElement | undefined;
  let menuJanelaRef: HTMLDivElement | undefined;
  let menuJanelaTriggerRef: HTMLButtonElement | undefined;

  // Fecha o menu da janela de trilha ao clicar fora ou pressionar Escape
  createEffect(() => {
    if (!menuJanelaAberto()) {
      return;
    }
    // Ao abrir, o foco vai para o período marcado; setas percorrem as opções.
    queueMicrotask(() =>
      menuJanelaRef
        ?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
        ?.focus(),
    );
    const aoClicarFora = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (
        menuJanelaRef &&
        !menuJanelaRef.contains(target) &&
        menuJanelaTriggerRef &&
        !menuJanelaTriggerRef.contains(target)
      ) {
        setMenuJanelaAberto(false);
      }
    };
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuJanelaAberto(false);
        menuJanelaTriggerRef?.focus();
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") {
        return;
      }
      const opcoes = [
        ...(menuJanelaRef?.querySelectorAll<HTMLButtonElement>(
          '[role="menuitemradio"]',
        ) ?? []),
      ];
      if (opcoes.length === 0) {
        return;
      }
      e.preventDefault();
      const atual = opcoes.indexOf(document.activeElement as HTMLButtonElement);
      const passo = e.key === "ArrowDown" ? 1 : -1;
      opcoes[(atual + passo + opcoes.length) % opcoes.length].focus();
    };
    document.addEventListener("pointerdown", aoClicarFora);
    document.addEventListener("keydown", aoTeclar);
    onCleanup(() => {
      document.removeEventListener("pointerdown", aoClicarFora);
      document.removeEventListener("keydown", aoTeclar);
    });
  });

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

  const selecionarJanela = (h: JanelaTrilhaH) => {
    setJanelaTrilhaH(h);
    setMenuJanelaAberto(false);
    menuJanelaTriggerRef?.focus(); // a opção some com o menu: foco volta ao botão
  };

  const btnVisual =
    "rounded-lg bg-slate-950/90 hover:bg-slate-800 text-slate-100 border border-slate-700/80 shadow-lg backdrop-blur-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400";
  // Botões só com ícone (zoom, enquadrar, camadas): quadrados em todas as larguras.
  const btnIcone = `flex items-center justify-center h-10 w-10 min-h-[40px] min-w-[40px] p-0 md:h-[38px] md:w-[38px] md:min-h-[38px] md:min-w-[38px] ${btnVisual}`;
  const btnBase = `flex items-center justify-center h-10 w-10 min-h-[40px] min-w-[40px] p-0 md:h-[38px] md:min-h-[38px] md:w-auto md:px-3.5 md:gap-2 ${btnVisual}`;

  return (
    <div class="flex w-full items-start justify-between">
      {/* Coluna da esquerda (topo): chat e nós da malha. Mobile: vertical; Desktop: horizontal */}
      <div class="pointer-events-auto flex flex-col md:flex-row items-start gap-2">
        {/* Chat da malha */}
        <button
          type="button"
          aria-expanded={localState.chatOpen}
          aria-label={
            localState.unreadChatCount > 0
              ? `Chat da malha, ${localState.unreadChatCount} não lidas`
              : "Chat da malha"
          }
          class={`relative ${btnBase}`}
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

        {/* Nós da malha / Sidebar */}
        <button
          type="button"
          aria-expanded={props.sidebarOpen()}
          aria-label="Nós da malha"
          class={btnBase}
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

      {/* Coluna da direita (vertical no canto superior direito, em todas as telas).
          Com a sidebar aberta, o overlay em desktop termina antes dela (ver MapWindow). */}
      <div
        class={`pointer-events-auto flex flex-col items-end gap-2 ${
          props.sidebarOpen() ? "hidden md:flex" : "flex"
        }`}
      >
        {/* Zoom + */}
        <button
          type="button"
          aria-label="Aproximar o mapa"
          class={btnIcone}
          title="Aproximar o mapa"
          onClick={() => zoomIn()}
        >
          <MagnifyingGlassPlusIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
        </button>

        {/* Zoom − */}
        <button
          type="button"
          aria-label="Afastar o mapa"
          class={btnIcone}
          title="Afastar o mapa"
          onClick={() => zoomOut()}
        >
          <MagnifyingGlassMinusIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
        </button>

        {/* Enquadrar todos os nós (só ícone; logo abaixo do zoom) */}
        <button
          type="button"
          aria-label="Enquadrar todos os nós com posição"
          class={btnIcone}
          title="Enquadrar todos os nós com posição"
          onClick={() => enquadrarTodos()}
        >
          <ArrowsOutIcon
            class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
            aria-hidden="true"
          />
        </button>

        {/* Camadas do mapa base (só ícone; menu abre para a esquerda) */}
        <div class="relative">
          <button
            ref={menuTriggerRef}
            type="button"
            aria-expanded={menuCamadasAberto()}
            aria-haspopup="menu"
            aria-controls="menu-camadas"
            aria-label="Camadas do mapa base"
            class={btnIcone}
            title="Alternar camadas do mapa base"
            onClick={() => setMenuCamadasAberto((v) => !v)}
          >
            <StackSimpleIcon
              class="h-[18px] w-[18px] md:h-4 md:w-4 text-emerald-400 shrink-0"
              aria-hidden="true"
            />
          </button>

          <Show when={menuCamadasAberto()}>
            <div
              ref={menuRef}
              id="menu-camadas"
              role="menu"
              aria-label="Camadas do mapa"
              class="absolute right-0 top-full mt-1.5 w-48 rounded-lg bg-slate-950/95 border border-slate-700/80 shadow-2xl backdrop-blur-md p-1 z-30 flex flex-col gap-0.5 text-xs"
            >
              <button
                type="button"
                role="menuitemradio"
                aria-checked={localState.basemapMode === "google"}
                class={`flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-left transition-colors ${
                  localState.basemapMode === "google"
                    ? "bg-slate-800 text-emerald-400 font-semibold"
                    : "text-slate-200 hover:bg-slate-900"
                }`}
                onClick={() => selecionarModo("google")}
              >
                <span>Google Satélite (Padrão)</span>
                <Show when={localState.basemapMode === "google"}>
                  <CheckIcon
                    class="h-3.5 w-3.5 text-emerald-400"
                    aria-hidden="true"
                  />
                </Show>
              </button>
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
                <span>Satélite (Esri)</span>
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

        {/* Janela da trilha: quantas horas de histórico buscar e desenhar */}
        <div class="relative">
          <button
            ref={menuJanelaTriggerRef}
            type="button"
            aria-expanded={menuJanelaAberto()}
            aria-haspopup="menu"
            aria-controls="menu-janela-trilha"
            aria-label={`Período da trilha: ${rotuloJanela(localState.janelaTrilhaH)}`}
            class={`${btnIcone} flex-col gap-0`}
            title="Período da trilha"
            onClick={() => setMenuJanelaAberto((v) => !v)}
          >
            <ClockCounterClockwiseIcon
              class="h-4 w-4 text-emerald-400 shrink-0"
              aria-hidden="true"
            />
            <span class="text-[9px] leading-none text-slate-300">
              {rotuloJanelaCurto(localState.janelaTrilhaH)}
            </span>
          </button>

          <Show when={menuJanelaAberto()}>
            <div
              ref={menuJanelaRef}
              id="menu-janela-trilha"
              role="menu"
              aria-label="Período da trilha"
              class="absolute right-0 top-full mt-1.5 w-40 rounded-lg bg-slate-950/95 border border-slate-700/80 shadow-2xl backdrop-blur-md p-1 z-30 flex flex-col gap-0.5 text-xs"
            >
              <div class="px-2.5 pt-1 pb-0.5 text-[10px] uppercase tracking-wide text-slate-500">
                Trilha dos últimos
              </div>
              <For each={JANELAS_TRILHA_H}>
                {(h) => (
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={localState.janelaTrilhaH === h}
                    class={`flex items-center justify-between w-full px-2.5 py-1.5 rounded-md text-left transition-colors ${
                      localState.janelaTrilhaH === h
                        ? "bg-slate-800 text-emerald-400 font-semibold"
                        : "text-slate-200 hover:bg-slate-900"
                    }`}
                    onClick={() => selecionarJanela(h)}
                  >
                    <span>{rotuloJanela(h)}</span>
                    <Show when={localState.janelaTrilhaH === h}>
                      <CheckIcon
                        class="h-3.5 w-3.5 text-emerald-400"
                        aria-hidden="true"
                      />
                    </Show>
                  </button>
                )}
              </For>
            </div>
          </Show>
        </div>
      </div>
    </div>
  );
};
