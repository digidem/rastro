import type { Component } from "solid-js";
import { Match, Show, Switch, createSignal, onMount } from "solid-js";
import { InitializeMap } from "./InitializeMap.jsx";
import { MapWindow } from "./MapWindow.jsx";
import { Button } from "./components/ui/button.jsx";
import {
  Body,
  Description,
  Footer,
  Header,
  Root,
  Title,
} from "./components/ui/card.jsx";
import { Input } from "./components/ui/input.jsx";
import { Text } from "./components/ui/text.jsx";
import { iniciarTitulo, titulo } from "./lib/config.js";
import { AVISO_SEM_TLS, credenciaisPermitidas } from "./lib/credenciais.js";
import { DataProvider, useData } from "./providers/DataProvider.jsx";
import { ErrOffline, ErrTokenInvalid } from "./providers/api.js";
import { LocalStateContext } from "./providers/index.js";
import { LocalState } from "./store.js";

interface TelaTokenProps {
  /** Erro a exibir (da última tentativa de entrar/verificar). */
  erro: string;
  /** Requisição em andamento — desabilita o botão e mostra "Entrando…". */
  tentando: boolean;
  /** Dias que o servidor aceita no "Lembrar" (GET /api/auth/estado). */
  dias: number;
  /**
   * HTTPS (ou loopback): único contexto em que digitar a credencial é seguro.
   * Falso → a tela não renderiza o campo, só o aviso para ativar TLS.
   */
  podeCredenciais: boolean;
  onEntrar: (token: string, lembrar: boolean) => void;
  /** Verificação de rede falhou: botão refaz o authEstado(). */
  onTentarDeNovo: () => void;
}

// Tela de login por token. O token nunca toca o store/localStorage: entra no
// POST e vira cookie HttpOnly que o JS não lê.
const TelaToken: Component<TelaTokenProps> = (props) => {
  const [valor, setValor] = createSignal("");
  const [lembrar, setLembrar] = createSignal(false); // DESMARCADO por padrão

  const entrar = () => {
    const token = valor().trim();
    if (token === "" || props.tentando) {
      return;
    }
    props.onEntrar(token, lembrar());
  };

  return (
    <div class="flex h-full w-full items-center justify-center">
      <Root class="w-80">
        <Header>
          <Title>{titulo()}</Title>
          <Description>Mapa da malha</Description>
        </Header>
        {/* Sem TLS não se pede credencial: nada de campo de token em http://
            que não seja loopback — o aviso diz o que habilitar. */}
        <Show
          when={props.podeCredenciais}
          fallback={
            <Body class="flex flex-col gap-2">
              <Text class="text-red-500">{AVISO_SEM_TLS}</Text>
            </Body>
          }
        >
          <Body class="flex flex-col gap-2">
            <Input
              type="password"
              placeholder="Token de acesso"
              autocomplete="current-password"
              value={valor()}
              onInput={(e) => setValor(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  entrar();
                }
              }}
            />
            <Show when={props.erro !== ""}>
              <Text class="text-red-500">{props.erro}</Text>
              <Show when={props.erro === "Sem conexão com a API"}>
                <Button
                  variant="outline"
                  disabled={props.tentando}
                  onClick={() => props.onTentarDeNovo()}
                >
                  Tentar de novo
                </Button>
              </Show>
            </Show>
          </Body>
          <Footer class="flex flex-col gap-2">
            <label class="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={lembrar()}
                onInput={(e) => setLembrar(e.currentTarget.checked)}
              />
              <span>Lembrar neste computador ({props.dias} dias)</span>
            </label>
            <Text class="text-xs text-gray-400">
              Não marque em computador compartilhado.
            </Text>
            <Button
              class="w-full"
              disabled={props.tentando}
              onClick={() => entrar()}
            >
              {props.tentando ? "Entrando…" : "Entrar"}
            </Button>
          </Footer>
        </Show>
      </Root>
    </div>
  );
};

// Erro de login em PT-BR: 401 → "Token inválido", rede → "Sem conexão com a
// API", 409 → a mensagem do servidor (token não configurado).
const mensagemDeErro = (err: unknown): string => {
  if (err instanceof ErrTokenInvalid) {
    return "Token inválido";
  }
  if (err instanceof ErrOffline) {
    return "Sem conexão com a API";
  }
  if (err instanceof Error && err.message !== "") {
    return err.message;
  }
  return "Sem conexão com a API";
};

// Filho dentro do DataProvider: máquina de estados da sessão.
const Conteudo: Component = () => {
  const { api, startSession } = useData();
  const [erro, setErro] = createSignal("");
  const [tentando, setTentando] = createSignal(false);
  // Sem TLS fora do loopback não se pede credencial: a tela de login vira o
  // aviso de HTTPS (e a api.ts também não manda Authorization/cookie).
  const podeCredenciais = credenciaisPermitidas();

  // Verifica o estado de auth (cookie) e decide entre mapa e tela de login.
  const verificar = async () => {
    try {
      const estado = await api.authEstado();
      LocalState.setAuthExigida(estado.exigida);
      LocalState.setDiasLembrar(estado.diasLembrar);
      setErro("");
      if (!estado.exigida || estado.autenticado) {
        startSession();
      } else {
        LocalState.setAuth("login");
      }
    } catch {
      // API inalcançável: tela de login com o aviso; "Tentar de novo" refaz.
      setErro("Sem conexão com a API");
      LocalState.setAuth("login");
    }
  };

  onMount(() => {
    verificar();
  });

  const entrar = async (token: string, lembrar: boolean) => {
    if (tentando()) {
      return;
    }
    setTentando(true);
    setErro("");
    try {
      await api.login(token, lembrar);
      startSession();
    } catch (err) {
      setErro(mensagemDeErro(err));
    } finally {
      setTentando(false);
    }
  };

  return (
    <Switch>
      <Match when={LocalState.localState.auth === "ok"}>
        <InitializeMap>
          <MapWindow />
        </InitializeMap>
      </Match>
      <Match when={LocalState.localState.auth === "verificando"}>
        <div class="flex h-full w-full items-center justify-center">
          <Text class="text-gray-400">Carregando…</Text>
        </div>
      </Match>
      <Match when={LocalState.localState.auth === "login"}>
        <TelaToken
          erro={erro()}
          tentando={tentando()}
          dias={LocalState.localState.diasLembrar}
          podeCredenciais={podeCredenciais}
          onEntrar={(token, lembrar) => entrar(token, lembrar)}
          onTentarDeNovo={() => verificar()}
        />
      </Match>
    </Switch>
  );
};

export const App: Component = () => {
  // Título do deploy na subida: /config.json (escrito pelo entrypoint com
  // RASTRO_TITLE) manda na aba e no card; sem ele, fica "Rastro".
  onMount(() => {
    iniciarTitulo();
  });

  return (
    <div class="h-screen w-screen">
      <LocalStateContext.Provider value={LocalState}>
        <DataProvider>
          <Conteudo />
        </DataProvider>
      </LocalStateContext.Provider>
    </div>
  );
};
