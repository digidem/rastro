import type { Component } from "solid-js";
import {
  For,
  Show,
  createEffect,
  createSignal,
  onCleanup,
  onMount,
  useContext,
} from "solid-js";
import {
  ChatCircleTextIcon,
  PaperPlaneRightIcon,
  SpeakerHighIcon,
  SpeakerSlashIcon,
  WarningIcon,
  XIcon,
} from "solid-phosphor/regular";
import {
  type ChatBoat,
  type ChatMessage,
  MAX_CHAT_BYTES,
  countUtf8Bytes,
  dedupeMessages,
  fetchChatBoats,
  fetchChatMessages,
  fetchOutboxStatus,
  formatChatAge,
  formatChatTime,
  getChatMuted,
  getOutboxStatusLabel,
  isTextLengthValid,
  maxReceivedAt,
  playAlertSound,
  sendChatMessage,
  setChatMuted,
} from "../lib/chat.js";
import { LocalStateContext } from "../providers/index.js";
import { LocalState } from "../store.js";

export interface ChatPanelProps {
  /** Callback para testes ou controle externo de abertura. */
  onClose?: () => void;
}

interface ChatMessageItemProps {
  message: ChatMessage;
  nowMs: number;
}

const ChatMessageItem: Component<ChatMessageItemProps> = (props) => {
  const m = () => props.message;
  const isInbound = () => m().direction === "in";
  const timestamp = () => m().received_at || m().observed_at;
  const statusTexto = () => {
    const s = m().status;
    return m().status_label || (s ? getOutboxStatusLabel(s) : "");
  };

  return (
    <div
      class={`flex flex-col max-w-[85%] rounded-lg p-2.5 text-xs transition-colors ${
        isInbound()
          ? m().is_alert
            ? "self-start mr-auto bg-red-950/80 border-2 border-red-500 text-red-100 shadow-[0_0_12px_rgba(239,68,68,0.35)]"
            : "self-start mr-auto bg-slate-800/90 border border-slate-700/80 text-slate-200"
          : "self-end ml-auto bg-emerald-950/60 border border-emerald-600/70 text-emerald-100"
      }`}
    >
      <div class="flex items-center justify-between gap-2 mb-1">
        <span
          class={`font-semibold text-[11px] ${
            m().is_alert
              ? "text-red-300 font-bold"
              : isInbound()
                ? "text-emerald-400"
                : "text-emerald-300"
          }`}
        >
          {isInbound() ? m().boat_id : `Para: ${m().boat_id}`}
        </span>

        <Show when={m().is_alert}>
          <span class="inline-flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[9px] font-bold text-white uppercase tracking-wider animate-pulse">
            <WarningIcon class="h-3 w-3" aria-hidden="true" />
            <span>ALERTA</span>
          </span>
        </Show>
      </div>

      <p class="whitespace-pre-wrap break-words leading-relaxed text-xs">
        {m().text}
      </p>

      <div class="flex flex-wrap items-center gap-1.5 mt-1.5 text-[10px] text-slate-400">
        <span>{formatChatTime(timestamp())}</span>
        <span aria-hidden="true">·</span>
        <span>{formatChatAge(timestamp(), props.nowMs)}</span>

        <Show when={m().direction === "out" && Boolean(m().status)}>
          <span aria-hidden="true">·</span>
          <span
            class={`font-medium ${
              m().status === "sent"
                ? "text-emerald-400"
                : m().status === "expired" || m().status === "failed"
                  ? "text-amber-400"
                  : "text-slate-300"
            }`}
          >
            {statusTexto()}
          </span>
        </Show>
      </div>
    </div>
  );
};

function atualizarCursorSince(
  m: ChatMessage,
  cursorAtual: string | null,
): string | null {
  const ts = m.received_at;
  if (!ts) {
    return cursorAtual;
  }
  const tVal = Date.parse(ts);
  const curVal = cursorAtual ? Date.parse(cursorAtual) : 0;
  if (!cursorAtual || (!Number.isNaN(tVal) && tVal > curVal)) {
    return ts;
  }
  return cursorAtual;
}

function mesclarMensagens(
  atuais: ChatMessage[],
  novas: ChatMessage[],
): ChatMessage[] {
  return dedupeMessages(atuais, novas);
}

function contabilizarNovas(
  novas: ChatMessage[],
  seenIds: Set<number>,
  inicial: boolean,
): {
  temAlertaNovo: boolean;
  qtdNovasNaoLidas: number;
  novoCursor: string | null;
} {
  let temAlertaNovo = false;
  let qtdNovasNaoLidas = 0;
  let cursor: string | null = null;

  for (const m of novas) {
    if (!seenIds.has(m.id)) {
      seenIds.add(m.id);
      if (!inicial && m.direction === "in") {
        qtdNovasNaoLidas++;
        if (m.is_alert) {
          temAlertaNovo = true;
        }
      }
    }
    cursor = atualizarCursorSince(m, cursor);
  }

  return { temAlertaNovo, qtdNovasNaoLidas, novoCursor: cursor };
}

/**
 * Painel flutuante de chat da malha Rastro (WP-F2).
 *
 * Exibe lista de mensagens recebidas e enviadas, seletor de barcos da frota,
 * contador de bytes UTF-8 ao vivo (limite estrito de 200 bytes) e estados de
 * entrega estritamente em português:
 * "na fila", "enviado ao gateway do barco", "expirada (não entregue)", "falhou".
 * NUNCA exibe os termos "lido" ou "entregue ao tripulante".
 */
export const ChatPanel: Component<ChatPanelProps> = (props) => {
  const storeContext = useContext(LocalStateContext);
  const store = storeContext || LocalState;
  const localState = store.localState;
  const setChatOpen = store.setChatOpen;
  const setUnreadChatCount = store.setUnreadChatCount;
  const setHasAlertUnread = store.setHasAlertUnread;

  const [boats, setBoats] = createSignal<ChatBoat[]>([]);
  const [selectedBoat, setSelectedBoat] = createSignal<string>("todos");
  const [destinationBoat, setDestinationBoat] = createSignal<string>("");
  const [messages, setMessages] = createSignal<ChatMessage[]>([]);
  const [inputText, setInputText] = createSignal("");
  const [isSending, setIsSending] = createSignal(false);
  const [sendError, setSendError] = createSignal("");
  const [muted, setMutedState] = createSignal(getChatMuted());
  const [nowMs, setNowMs] = createSignal(Date.now());

  let messagesEndRef: HTMLDivElement | undefined;
  let pollingTimer: ReturnType<typeof setInterval> | undefined;
  let clockTimer: ReturnType<typeof setInterval> | undefined;
  let sinceCursor: string | null = null;
  let boatsLoaded = false;
  let isFetchingBoats = false;
  const pendingOutboxIds = new Set<number>();
  const seenMessageIds = new Set<number>();

  const alternarMudo = () => {
    const novo = !muted();
    setMutedState(novo);
    setChatMuted(novo);
  };

  const rolarParaFinal = () => {
    setTimeout(() => {
      if (
        messagesEndRef &&
        typeof messagesEndRef.scrollIntoView === "function"
      ) {
        messagesEndRef.scrollIntoView({ behavior: "smooth" });
      }
    }, 40);
  };

  const carregarBarcos = async (): Promise<boolean> => {
    if (isFetchingBoats) {
      return false;
    }
    isFetchingBoats = true;
    try {
      const lista = await fetchChatBoats();
      setBoats(lista);
      if (!destinationBoat() && lista.length > 0) {
        setDestinationBoat(lista[0].boat_id);
      }
      if (lista.length > 0) {
        boatsLoaded = true;
      }
      return true;
    } catch {
      // Ignora erro de rede; nova tentativa na próxima rodada do poll
      return false;
    } finally {
      isFetchingBoats = false;
    }
  };

  const processarMensagensNovas = (novas: ChatMessage[], inicial: boolean) => {
    if (novas.length === 0) {
      return;
    }

    const contagem = contabilizarNovas(novas, seenMessageIds, inicial);
    const maxRec = maxReceivedAt(novas, sinceCursor);
    if (maxRec) {
      sinceCursor = maxRec;
    }

    setMessages((atuais) => mesclarMensagens(atuais, novas));

    if (contagem.temAlertaNovo) {
      playAlertSound(muted());
      if (!localState.chatOpen) {
        setHasAlertUnread(true);
      }
    }

    if (!localState.chatOpen && contagem.qtdNovasNaoLidas > 0) {
      setUnreadChatCount((prev) => prev + contagem.qtdNovasNaoLidas);
    }

    rolarParaFinal();
  };

  const atualizarOutboxPendentes = async () => {
    if (pendingOutboxIds.size === 0) {
      return;
    }

    for (const outboxId of Array.from(pendingOutboxIds)) {
      try {
        const item = await fetchOutboxStatus(outboxId);
        if (item.status !== "queued") {
          pendingOutboxIds.delete(outboxId);
        }
        setMessages((atuais) =>
          atuais.map((m) => {
            if (m.outbox_id === outboxId) {
              return {
                ...m,
                status: item.status,
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                status_label: getOutboxStatusLabel(item.status),
              };
            }
            return m;
          }),
        );
      } catch {
        // Tenta novamente na próxima rodada
      }
    }
  };

  const executarPoll = async (inicial = false) => {
    try {
      if (!boatsLoaded) {
        await carregarBarcos();
      }
      const novas = await fetchChatMessages(sinceCursor);
      processarMensagensNovas(novas, inicial);
      await atualizarOutboxPendentes();
    } catch {
      // Falha temporária de rede silenciosa
    }
  };

  onMount(() => {
    carregarBarcos();
    executarPoll(true);

    // Polling a cada 5 segundos com cursor since
    pollingTimer = setInterval(() => {
      executarPoll(false);
    }, 5000);

    // Atualização de idade relativa a cada 15 segundos
    clockTimer = setInterval(() => {
      setNowMs(Date.now());
    }, 15000);
  });

  onCleanup(() => {
    if (pollingTimer) {
      clearInterval(pollingTimer);
    }
    if (clockTimer) {
      clearInterval(clockTimer);
    }
  });

  // Ao abrir o painel, reseta a contagem de não lidos e rola para a base
  createEffect(() => {
    if (localState.chatOpen) {
      setUnreadChatCount(0);
      setHasAlertUnread(false);
      rolarParaFinal();
    }
  });

  const barcoParaEnvio = () => {
    if (selectedBoat() !== "todos") {
      return selectedBoat();
    }
    return destinationBoat() || (boats().length > 0 ? boats()[0].boat_id : "");
  };

  const enviar = async () => {
    const texto = inputText().trim();
    const barco = barcoParaEnvio();
    if (isSending()) {
      return;
    }
    if (!(barco && isTextLengthValid(texto))) {
      return;
    }

    setIsSending(true);
    setSendError("");

    try {
      const res = await sendChatMessage(barco, texto);
      pendingOutboxIds.add(res.id);

      const msgLocal: ChatMessage = {
        id: -Date.now(),
        direction: "out",
        boat_id: barco,
        text: texto,
        is_alert: false,
        observed_at: new Date().toISOString(),
        received_at: new Date().toISOString(),
        outbox_id: res.id,
        status: res.status,
        status_label: getOutboxStatusLabel(res.status),
      };

      seenMessageIds.add(msgLocal.id);
      setMessages((atuais) => mesclarMensagens(atuais, [msgLocal]));
      setInputText("");
      rolarParaFinal();
    } catch (err) {
      setSendError(
        err instanceof Error ? err.message : "Não foi possível enviar",
      );
    } finally {
      setIsSending(false);
    }
  };

  const fechar = () => {
    setChatOpen(false);
    props.onClose?.();
  };

  const mensagensFiltradas = () => {
    const b = selectedBoat();
    const todos = messages();
    if (!b || b === "todos") {
      return todos;
    }
    return todos.filter((m) => m.boat_id === b);
  };

  const byteCount = () => countUtf8Bytes(inputText());
  const excedeuLimite = () => byteCount() > MAX_CHAT_BYTES;
  const podeEnviar = () =>
    byteCount() >= 1 &&
    byteCount() <= MAX_CHAT_BYTES &&
    Boolean(barcoParaEnvio()) &&
    !isSending();

  return (
    <>
      <Show when={localState.chatOpen}>
        <div class="pointer-events-auto fixed bottom-3 left-3 sm:bottom-4 sm:left-4 z-30 flex flex-col w-[360px] sm:w-[410px] h-[520px] max-h-[calc(100vh-2rem)] max-w-[calc(100vw-1.5rem)] rounded-xl border border-slate-700/80 bg-slate-900/95 shadow-2xl backdrop-blur-md text-slate-100 overflow-hidden">
          {/* Cabeçalho */}
          <div class="border-b border-slate-800 px-4 py-2.5 bg-slate-900/90 flex items-center justify-between shrink-0">
            <div class="flex items-center gap-2 min-w-0">
              <ChatCircleTextIcon
                class="h-4.5 w-4.5 text-emerald-400 shrink-0"
                aria-hidden="true"
              />
              <span class="font-bold text-white text-sm tracking-wide truncate">
                Chat da malha
              </span>
            </div>

            <div class="flex items-center gap-1.5">
              <button
                type="button"
                aria-label={
                  muted()
                    ? "Ativar alertas sonoros"
                    : "Silenciar alertas sonoros"
                }
                title={
                  muted()
                    ? "Ativar alertas sonoros"
                    : "Silenciar alertas sonoros"
                }
                class="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                onClick={() => alternarMudo()}
              >
                <Show
                  when={muted()}
                  fallback={
                    <SpeakerHighIcon class="h-4 w-4" aria-hidden="true" />
                  }
                >
                  <SpeakerSlashIcon
                    class="h-4 w-4 text-amber-400"
                    aria-hidden="true"
                  />
                </Show>
              </button>

              <button
                type="button"
                aria-label="Fechar chat"
                title="Fechar chat"
                class="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                onClick={() => fechar()}
              >
                <XIcon class="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* Subcabeçalho: Seletor de Barco */}
          <div class="border-b border-slate-800/80 px-3.5 py-2 bg-slate-950/60 flex items-center gap-2 shrink-0">
            <label
              for="chat-boat-select"
              class="text-xs font-semibold text-slate-400 shrink-0"
            >
              Barco:
            </label>
            <select
              id="chat-boat-select"
              aria-label="Selecionar barco"
              value={selectedBoat()}
              onChange={(e) => setSelectedBoat(e.currentTarget.value)}
              class="flex-1 bg-slate-900 border border-slate-700/80 rounded-lg px-2.5 py-1 text-xs text-slate-200 font-medium focus:ring-1 focus:ring-emerald-400 focus:outline-none"
            >
              <option value="todos">Todos os barcos</option>
              <For each={boats()}>
                {(b) => (
                  <option value={b.boat_id}>{b.boat || b.boat_id}</option>
                )}
              </For>
            </select>
          </div>

          {/* Lista de Mensagens */}
          <div
            data-testid="chat-message-list"
            class="flex-1 min-h-0 overflow-y-auto p-3 space-y-2.5 overscroll-contain"
          >
            <Show
              when={mensagensFiltradas().length > 0}
              fallback={
                <div class="h-full flex items-center justify-center p-4 text-center">
                  <span class="text-xs text-slate-400 font-medium">
                    Nenhuma mensagem registrada na malha.
                  </span>
                </div>
              }
            >
              <For each={mensagensFiltradas()}>
                {(m) => <ChatMessageItem message={m} nowMs={nowMs()} />}
              </For>
            </Show>
            <div ref={messagesEndRef} />
          </div>

          {/* Rodapé: Compositor */}
          <div class="border-t border-slate-800 p-2.5 bg-slate-900/90 flex flex-col gap-1.5 shrink-0">
            <Show when={sendError()}>
              <div class="text-[11px] text-red-400 bg-red-950/60 border border-red-800 px-2 py-1 rounded">
                {sendError()}
              </div>
            </Show>

            <Show when={selectedBoat() === "todos" && boats().length > 0}>
              <div class="flex items-center gap-1.5 text-xs text-slate-400 px-0.5">
                <span class="text-[11px] font-medium">Enviar para:</span>
                <select
                  aria-label="Barco de destino"
                  value={barcoParaEnvio()}
                  onChange={(e) => setDestinationBoat(e.currentTarget.value)}
                  class="bg-slate-950 border border-slate-700/80 rounded px-2 py-0.5 text-xs text-slate-200 font-medium focus:ring-1 focus:ring-emerald-400 focus:outline-none"
                >
                  <For each={boats()}>
                    {(b) => (
                      <option value={b.boat_id}>{b.boat || b.boat_id}</option>
                    )}
                  </For>
                </select>
              </div>
            </Show>

            <div class="relative">
              <textarea
                aria-label="Texto da mensagem"
                rows={2}
                placeholder={
                  barcoParaEnvio()
                    ? `Mensagem para ${barcoParaEnvio()}…`
                    : "Digite uma mensagem…"
                }
                value={inputText()}
                onInput={(e) => setInputText(e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (podeEnviar()) {
                      enviar();
                    }
                  }
                }}
                class="w-full bg-slate-950 border border-slate-700/80 rounded-lg p-2 text-xs text-slate-100 placeholder:text-slate-500 focus:ring-1 focus:ring-emerald-400 focus:outline-none resize-none"
              />
            </div>

            <div class="flex items-center justify-between px-0.5">
              <span
                class={`text-[11px] font-mono tabular-nums ${
                  excedeuLimite()
                    ? "text-red-400 font-bold"
                    : byteCount() > 180
                      ? "text-amber-400 font-medium"
                      : "text-slate-400"
                }`}
              >
                {byteCount()}/{MAX_CHAT_BYTES} bytes
              </span>

              <button
                type="button"
                disabled={!podeEnviar()}
                onClick={() => enviar()}
                class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 disabled:bg-slate-800 disabled:text-slate-500 text-slate-950 text-xs font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
              >
                <PaperPlaneRightIcon class="h-3.5 w-3.5" aria-hidden="true" />
                <span>{isSending() ? "Enviando…" : "Enviar"}</span>
              </button>
            </div>
          </div>
        </div>
      </Show>
    </>
  );
};
