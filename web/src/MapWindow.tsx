import type { Component } from "solid-js";
import { For, Show, createSignal, onMount } from "solid-js";
import { Button } from "./components/ui/button.jsx";
import { Body, Footer, Header, Root, Title } from "./components/ui/card.jsx";
import { Text } from "./components/ui/text.jsx";
import { useMap } from "./hooks/useMap.jsx";
import { useStore } from "./hooks/useStore.jsx";
import { useData } from "./providers/DataProvider.jsx";

// Idade do fix em PT-BR: "agora", "há 12 min", "há 3 h", "há 2 d".
const idadeFix = (iso: string | null): string => {
  if (iso === null) {
    return "sem fix";
  }
  const s = Math.max(
    0,
    Math.floor((Date.now() - new Date(iso).getTime()) / 1000),
  );
  if (s < 60) {
    return "agora";
  }
  if (s < 3600) {
    return `há ${Math.floor(s / 60)} min`;
  }
  if (s < 86400) {
    return `há ${Math.floor(s / 3600)} h`;
  }
  return `há ${Math.floor(s / 86400)} d`;
};

export const MapWindow: Component = () => {
  const { localState, select, setAuth } = useStore();
  const { api, startPolling, clearSessionData } = useData();
  const { setMapRef, initializeMap } = useMap();
  const [erroSair, setErroSair] = createSignal("");

  onMount(() => {
    initializeMap();
  });

  const nos = () => Object.values(localState.nodes);

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
    <div ref={setMapRef} class="relative p-0 m-0 w-full h-full">
      <div class="absolute h-full flex flex-col w-1/4 min-w-64 p-2 gap-2 right-0 top-0">
        <Root class="flex flex-col min-h-0 flex-1">
          <Header>
            <Title>Nós da malha</Title>
          </Header>
          <Body class="flex flex-col gap-1 flex-1 overflow-y-auto">
            <Show when={erroSair() !== ""}>
              <Text class="text-red-500">{erroSair()}</Text>
            </Show>
            <Show
              when={nos().length > 0}
              fallback={<Text class="text-gray-400">Aguardando dados…</Text>}
            >
              <For each={nos()}>
                {(n) => (
                  <button
                    type="button"
                    class={`text-left rounded px-2 py-1 ${
                      localState.selected === n.nodeNum
                        ? "bg-emerald-800/60"
                        : "hover:bg-gray-700/40"
                    }`}
                    onClick={() =>
                      select(
                        localState.selected === n.nodeNum ? null : n.nodeNum,
                      )
                    }
                  >
                    <div class="font-medium">{n.nome}</div>
                    <div class="text-xs text-gray-400">
                      {n.battery === null
                        ? "bateria —"
                        : `bateria ${n.battery}%`}{" "}
                      · {idadeFix(n.posTime)}
                    </div>
                  </button>
                )}
              </For>
            </Show>
          </Body>
          <Footer class="flex items-center gap-2">
            <button
              type="button"
              class="flex items-center gap-1 text-xs"
              title={
                localState.online
                  ? "Conectado à API"
                  : "Sem conexão — tentar de novo"
              }
              onClick={() => reconectar()}
            >
              <span
                class={`h-2 w-2 rounded-full ${
                  localState.online ? "bg-green-500" : "bg-gray-500"
                }`}
              />
              {localState.online ? "Conectado" : "Sem conexão"}
            </button>
            <Show when={localState.authExigida}>
              <Button variant="outline" class="ml-auto" onClick={() => sair()}>
                Sair
              </Button>
            </Show>
          </Footer>
        </Root>
      </div>
    </div>
  );
};
