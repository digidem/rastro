/**
 * Tipos e funções utilitárias da API de chat da malha Rastro (WP-F2).
 *
 * Regras estritas:
 * - NUNCA exibir os termos "lido" ou "entregue ao tripulante".
 * - Estados de saída refletem apenas o gateway do barco:
 *   "na fila", "enviado ao gateway do barco", "expirada (não entregue)", "falhou".
 * - Mensagens com is_alert recebem destaque visual e aviso sonoro via WebAudio.
 */

export type ChatDirection = "in" | "out";

export type OutboxStatus = "queued" | "sent" | "expired" | "failed";

export const OUTBOX_STATUS_LABELS: Record<OutboxStatus, string> = {
  queued: "na fila",
  sent: "enviado ao gateway do barco",
  expired: "expirada (não entregue)",
  failed: "falhou",
};

export interface ChatBoat {
  boat_id: string;
  boat?: string;
  gateway_id?: string;
  virtual_node_num?: number;
  active: boolean;
}

export interface ChatMessage {
  id: number;
  direction: ChatDirection;
  boat_id: string;
  from_num?: number | null;
  text: string;
  is_alert: boolean;
  observed_at: string | null;
  received_at: string | null;
  outbox_id?: number | null;
  status?: OutboxStatus;
  status_label?: string;
}

export interface OutboxItem {
  id: number;
  boat_id: string;
  status: OutboxStatus;
  label: string;
  status_label: string;
  created_by?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  sent_at?: string | null;
  error?: string | null;
}

/** Limite máximo de bytes UTF-8 para uma mensagem de chat. */
export const MAX_CHAT_BYTES = 200;

/** Conta bytes UTF-8 exatos de uma string. */
export function countUtf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Valida se o texto está no intervalo [1, 200] bytes UTF-8. */
export function isTextLengthValid(text: string): boolean {
  const bytes = countUtf8Bytes(text);
  return bytes >= 1 && bytes <= MAX_CHAT_BYTES;
}

/** Retorna o rótulo em português para o status da fila de envio. */
export function getOutboxStatusLabel(status: OutboxStatus | string): string {
  if (status === "sent") {
    return OUTBOX_STATUS_LABELS.sent;
  }
  if (status === "expired") {
    return OUTBOX_STATUS_LABELS.expired;
  }
  if (status === "failed") {
    return OUTBOX_STATUS_LABELS.failed;
  }
  return OUTBOX_STATUS_LABELS.queued;
}

/** Formata data/hora em fuso do Javari ou hora simples (HH:mm). */
export function formatChatTime(iso: string | null): string {
  if (!iso) {
    return "—";
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return "—";
  }
  return d.toLocaleTimeString("pt-BR", {
    timeZone: "America/Manaus",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** Formata a idade relativa da mensagem em português. */
export function formatChatAge(iso: string | null, nowMs: number): string {
  if (!iso) {
    return "agora";
  }
  const t = Date.parse(iso);
  if (Number.isNaN(t)) {
    return "agora";
  }
  const diffSec = Math.max(0, Math.floor((nowMs - t) / 1000));
  if (diffSec < 60) {
    return "agora";
  }
  if (diffSec < 3600) {
    return `há ${Math.floor(diffSec / 60)} min`;
  }
  if (diffSec < 86400) {
    return `há ${Math.floor(diffSec / 3600)} h`;
  }
  return `há ${Math.floor(diffSec / 86400)} d`;
}

/** Emite um bipe curto com WebAudio sintetizado em código (sem asset). */
export function playAlertSound(muted: boolean): void {
  if (muted || typeof window === "undefined") {
    return;
  }
  try {
    const AudioContextClass =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioContextClass) {
      return;
    }
    const ctx = new AudioContextClass();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime); // nota A5
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.2);
    setTimeout(() => {
      try {
        ctx.close().catch(() => {
          // Ignora erro no fechamento do contexto
        });
      } catch {
        // Silencia erro em navegadores sem suporte
      }
    }, 400);
  } catch {
    // Falha de autoplay ou permissão nunca deve levantar erro
  }
}

const CHAT_MUTED_KEY = "rastro_chat_muted";

/** Lê do localStorage se os alertas sonoros estão silenciados. */
export function getChatMuted(): boolean {
  if (typeof window === "undefined" || !window.localStorage) {
    return false;
  }
  try {
    return window.localStorage.getItem(CHAT_MUTED_KEY) === "true";
  } catch {
    return false;
  }
}

/** Salva no localStorage se os alertas sonoros estão silenciados. */
export function setChatMuted(muted: boolean): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(CHAT_MUTED_KEY, String(muted));
  } catch {
    // Ignora erro de cota ou bloqueio de localStorage
  }
}

export interface FetchChatMessagesOptions {
  limit?: number | null;
  // biome-ignore lint/style/useNamingConvention: compatibilidade com a rota /api/chat/messages
  before_id?: number | null;
}

/**
 * Retorna o maior timestamp `received_at` encontrado entre as mensagens e o cursor atual.
 * Ignora observed_at ou mensagens sem received_at válido.
 */
export function maxReceivedAt(
  messages: ChatMessage[],
  currentCursor: string | null = null,
): string | null {
  let max = currentCursor;
  let maxTs = max ? Date.parse(max) || 0 : 0;

  for (const m of messages) {
    if (!m.received_at) {
      continue;
    }
    const ts = Date.parse(m.received_at);
    if (!Number.isNaN(ts) && ts > maxTs) {
      maxTs = ts;
      max = m.received_at;
    }
  }
  return max;
}

/**
 * Mescla e deduplica mensagens pelo campo `id`, preservando a ordem cronológica ASC.
 * Reconcilia mensagens otimistas (com id negativo) substituindo-as pela cópia
 * persistida correspondente (com id positivo), casando por `outbox_id` ou por
 * barco e texto (com direção 'out').
 */
export function dedupeMessages(
  existing: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const map = new Map<number, ChatMessage>();
  for (const m of existing) {
    map.set(m.id, m);
  }

  const replacedOptimisticIds = new Set<number>();

  for (const inc of incoming) {
    // 1. Se inc já tem id conhecido no map (atualiza campos preservando atributos locais)
    const prev = map.get(inc.id);
    if (prev) {
      map.set(inc.id, {
        ...prev,
        ...inc,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: inc.outbox_id ?? prev.outbox_id,
        status: inc.status ?? prev.status,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        status_label: inc.status_label ?? prev.status_label,
      });
      continue;
    }

    // 2. Se inc é uma mensagem persistida (id > 0), procura se corresponde a alguma mensagem otimista existente (id < 0)
    let matchedOptId: number | null = null;
    let matchedOptMsg: ChatMessage | null = null;

    if (inc.id > 0) {
      for (const [id, msg] of map.entries()) {
        if (id < 0 && !replacedOptimisticIds.has(id)) {
          // Casamento preferencial por outbox_id
          const matchOutbox =
            inc.outbox_id != null &&
            msg.outbox_id != null &&
            inc.outbox_id === msg.outbox_id;

          // Casamento por barco + texto para mensagens de saída
          const matchBoatAndText =
            inc.direction === "out" &&
            msg.direction === "out" &&
            inc.boat_id === msg.boat_id &&
            inc.text === msg.text;

          if (matchOutbox || matchBoatAndText) {
            matchedOptId = id;
            matchedOptMsg = msg;
            break;
          }
        }
      }
    }

    if (matchedOptId !== null && matchedOptMsg !== null) {
      // Remove a versão otimista com id negativo
      map.delete(matchedOptId);
      replacedOptimisticIds.add(matchedOptId);

      // Insere a versão persistida herdando status/outbox_id locais caso ausentes no backend
      map.set(inc.id, {
        ...matchedOptMsg,
        ...inc,
        id: inc.id,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: inc.outbox_id ?? matchedOptMsg.outbox_id,
        status: inc.status ?? matchedOptMsg.status,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        status_label: inc.status_label ?? matchedOptMsg.status_label,
      });
    } else {
      map.set(inc.id, inc);
    }
  }

  return Array.from(map.values()).sort((a, b) => {
    const ta = Date.parse(a.received_at || a.observed_at || "") || 0;
    const tb = Date.parse(b.received_at || b.observed_at || "") || 0;
    if (ta !== tb) {
      return ta - tb;
    }
    return a.id - b.id;
  });
}

/** Busca lista de barcos ativos na malha (GET /api/chat/boats). */
export async function fetchChatBoats(base = ""): Promise<ChatBoat[]> {
  const res = await fetch(`${base}/api/chat/boats`, {
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(`GET /api/chat/boats: ${res.status}`);
  }
  return (await res.json()) as ChatBoat[];
}

/** Busca mensagens de chat (GET /api/chat/messages?since=...). */
export async function fetchChatMessages(
  since?: string | null,
  base = "",
  options?: FetchChatMessagesOptions,
): Promise<ChatMessage[]> {
  const params = new URLSearchParams();
  if (since) {
    params.set("since", since);
  }
  if (options?.limit != null) {
    params.set("limit", String(options.limit));
  }
  if (options?.before_id != null) {
    params.set("before_id", String(options.before_id));
  }
  const qs = params.toString();
  const url = qs
    ? `${base}/api/chat/messages?${qs}`
    : `${base}/api/chat/messages`;
  const res = await fetch(url, {
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(`GET /api/chat/messages: ${res.status}`);
  }
  return (await res.json()) as ChatMessage[];
}

/** Envia mensagem de chat para um barco (POST /api/chat/send). */
export async function sendChatMessage(
  boat: string,
  text: string,
  base = "",
): Promise<{ id: number; status: OutboxStatus; expires_at: string }> {
  const res = await fetch(`${base}/api/chat/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ boat, text }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(data.detail || `POST /api/chat/send: ${res.status}`);
  }
  return (await res.json()) as {
    id: number;
    status: OutboxStatus;
    expires_at: string;
  };
}

/** Consulta status da mensagem na fila outbox (GET /api/chat/outbox/:id). */
export async function fetchOutboxStatus(
  outboxId: number,
  base = "",
): Promise<OutboxItem> {
  const res = await fetch(`${base}/api/chat/outbox/${outboxId}`, {
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(`GET /api/chat/outbox/${outboxId}: ${res.status}`);
  }
  return (await res.json()) as OutboxItem;
}
