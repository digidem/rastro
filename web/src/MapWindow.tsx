import type { Component } from "solid-js";
import { Show, createEffect, createSignal, onMount } from "solid-js";
import { Button } from "./components/ui/button.jsx";
import { Header, Root, Title } from "./components/ui/card.jsx";
import { Text } from "./components/ui/text.jsx";
import { MapControls } from "./components/viewer/MapControls.jsx";
import { NodeFilters } from "./components/viewer/NodeFilters.jsx";
import { NodeInspector } from "./components/viewer/NodeInspector.jsx";
import { NodeList } from "./components/viewer/NodeList.jsx";
import { NodeLog } from "./components/viewer/NodeLog.jsx";
import { useMap } from "./hooks/useMap.jsx";
import { useStore } from "./hooks/useStore.jsx";
import { useViewerNodes } from "./hooks/useViewerNodes.js";
import { useData } from "./providers/DataProvider.jsx";

export const MapWindow: Component = () => {
  const { localState, setAuth } = useStore();
  const { api, startPolling, clearSessionData } = useData();
  const { setMapRef, initializeMap } = useMap();
  const [erroSair, setErroSair] = createSignal("");
  // Registros do nó selecionado: substituem inspetor + lista enquanto abertos.
  const [logAberto, setLogAberto] = createSignal(false);
  // Sidebar começa fechada no celular e aberta em telas >= md.
  const [sidebarAberta, setSidebarAberta] = createSignal(
    typeof window !== "undefined" && window.innerWidth >= 768,
  );

  // Derivado UMA vez por sessão de tela e compartilhado com os componentes.
  const {
    nowMs,
    filteredNodes,
    selectedNode,
    totalCount,
    filteredCount,
    inactiveCount,
  } = useViewerNodes();

  // Sem nó selecionado não há registro para mostrar.
  createEffect(() => {
    if (selectedNode() === undefined) {
      setLogAberto(false);
    }
  });

  onMount(() => {
    initializeMap();
  });

  // Queda de rede para o polling; clicar no indicador tenta de novo.
  const reconectar = async () => {
    try {
      if (await api.ping()) {
        startPolling();
      }
    } catch {
      // continua offline
    }
  };

  const sair = async () => {
    setErroSair("");
    // Limpa JÁ a visualização: a geração nova mata respostas de latest/track
    // em voo e o InitializeMap apaga seleção/trilha/popup da sessão.
    clearSessionData();
    try {
      await api.logout();
      // Cookie apagado: só agora a tela muda — mentir "saiu" sem apagar o
      // cookie seria furo de segurança.
      setAuth("login");
    } catch {
      // Falhou (sem conexão etc.): o cookie continua válido — manter auth="ok",
      // avisar e retomar o polling.
      setErroSair("Não foi possível sair — sem conexão. Tente novamente.");
      startPolling();
    }
  };

  return (
    <div class="relative m-0 h-full w-full p-0">
      {/* Container do MapLibre: preenche tudo, atrás do overlay de UI. */}
      <div ref={setMapRef} class="absolute inset-0" />

      {/* Overlay: só os controles capturam ponteiro; o mapa continua arrastável. */}
      <div class="pointer-events-none relative z-10 h-full min-h-0">
        {/* Com a sidebar aberta (desktop), os controles param antes dela: 368px da sidebar + 8px de folga. */}
        <div
          class={`pointer-events-none absolute inset-x-2 top-2 z-10 ${
            sidebarAberta() ? "md:right-[368px]" : ""
          }`}
        >
          <MapControls
            sidebarOpen={() => sidebarAberta()}
            onToggleSidebar={() => setSidebarAberta((v) => !v)}
          />
        </div>

        <Show when={sidebarAberta()}>
          <aside class="pointer-events-auto absolute right-0 top-0 flex h-full w-[368px] max-w-[calc(100vw-1rem)] flex-col p-2 z-20">
            <Root class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-700/80 bg-slate-900/95 shadow-2xl backdrop-blur-md text-slate-100">
              <Header class="border-b border-slate-700/80 px-4 py-2.5 bg-slate-900/90 shrink-0 flex items-center justify-between">
                <Title class="text-slate-100 font-bold text-sm tracking-wide">
                  Nós da malha
                </Title>
                <button
                  type="button"
                  class="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
                  title="Fechar painel"
                  onClick={() => setSidebarAberta(false)}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    class="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <title>Fechar painel</title>
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </Header>

              <div class="shrink-0">
                <NodeFilters />
              </div>

              <Show
                when={logAberto() && selectedNode()}
                fallback={
                  <>
                    <Show when={selectedNode()}>
                      {(node) => (
                        <div class="shrink-0 max-h-[45vh] overflow-y-auto border-b border-slate-700/80">
                          <NodeInspector
                            node={node}
                            nowMs={nowMs}
                            onOpenLog={() => setLogAberto(true)}
                          />
                        </div>
                      )}
                    </Show>

                    {/* NodeList preenche TODO o espaço vertical restante */}
                    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
                      <NodeList
                        nodes={filteredNodes}
                        totalCount={totalCount}
                        filteredCount={filteredCount}
                        inactiveCount={inactiveCount}
                        nowMs={nowMs}
                      />
                    </div>
                  </>
                }
              >
                {(node) => (
                  <div class="flex-1 min-h-0 overflow-hidden">
                    <NodeLog node={node} onBack={() => setLogAberto(false)} />
                  </div>
                )}
              </Show>

              <Show when={erroSair() !== ""}>
                <div class="px-3 py-1.5 bg-red-950/80 border-t border-red-800 text-xs text-red-300 shrink-0">
                  <Text class="text-red-300 text-xs">{erroSair()}</Text>
                </div>
              </Show>

              <div class="flex items-center gap-2 border-t border-slate-700/80 px-4 py-3 bg-slate-900/90 shrink-0 text-xs">
                <button
                  type="button"
                  class="flex items-center gap-2.5 text-xs text-slate-300 hover:text-white transition-colors"
                  title={
                    localState.online
                      ? "Conectado à API da malha"
                      : "Sem conexão — tentar reconectar"
                  }
                  onClick={() => reconectar()}
                >
                  <span class="relative flex h-2.5 w-2.5 items-center justify-center">
                    <Show when={localState.online}>
                      <span class="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60 motion-safe:animate-ping" />
                    </Show>
                    <span
                      class={`relative inline-flex rounded-full h-2 w-2 ${
                        localState.online ? "bg-emerald-400" : "bg-slate-500"
                      }`}
                    />
                  </span>
                  <span class="font-medium text-slate-200">
                    {localState.online ? "Rádio conectado" : "Sem conexão"}
                  </span>
                </button>
                <Show when={localState.authExigida}>
                  <Button
                    variant="outline"
                    size="sm"
                    class="ml-auto h-8 px-3 bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700 text-xs font-medium rounded-lg"
                    onClick={() => sair()}
                  >
                    Sair
                  </Button>
                </Show>
              </div>
            </Root>
          </aside>
        </Show>
      </div>
    </div>
  );
};
