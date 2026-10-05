import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type ChatMessage,
  OUTBOX_STATUS_LABELS,
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
} from "./chat.js";

const TIME_FORMAT_REGEX = /\d{2}:\d{2}/;

describe("countUtf8Bytes & isTextLengthValid", () => {
  it("conta bytes ASCII corretamente", () => {
    expect(countUtf8Bytes("hello")).toBe(5);
    expect(countUtf8Bytes("")).toBe(0);
    expect(countUtf8Bytes("1234567890")).toBe(10);
  });

  it("conta caracteres acentuados em português (2 bytes por caractere acentuado)", () => {
    // 'C' (1) + 'u' (1) + 'r' (1) + 'u' (1) + 'ç' (2) + 'á' (2) = 8 bytes
    expect(countUtf8Bytes("Curuçá")).toBe(8);
    // 'a' (1) + 't' (1) + 'e' (1) + 'n' (1) + 'ç' (2) + 'ã' (2) + 'o' (1) = 9 bytes
    expect(countUtf8Bytes("atenção")).toBe(9);
  });

  it("conta emojis e caracteres especiais com precisão (4 bytes por emoji)", () => {
    expect(countUtf8Bytes("🚤")).toBe(4);
    expect(countUtf8Bytes("🚨")).toBe(4);
  });

  it("valida limite de 200 bytes UTF-8", () => {
    expect(isTextLengthValid("")).toBe(false);
    expect(isTextLengthValid("a")).toBe(true);

    const txt200 = "a".repeat(200);
    expect(countUtf8Bytes(txt200)).toBe(200);
    expect(isTextLengthValid(txt200)).toBe(true);

    const txt201 = "a".repeat(201);
    expect(countUtf8Bytes(txt201)).toBe(201);
    expect(isTextLengthValid(txt201)).toBe(false);

    // 100 caracteres acentuados de 2 bytes cada = 200 bytes
    const acentos100 = "ç".repeat(100);
    expect(countUtf8Bytes(acentos100)).toBe(200);
    expect(isTextLengthValid(acentos100)).toBe(true);

    // 101 caracteres acentuados = 202 bytes
    const acentos101 = "ç".repeat(101);
    expect(countUtf8Bytes(acentos101)).toBe(202);
    expect(isTextLengthValid(acentos101)).toBe(false);
  });
});

describe("Status de saída do chat (WP-F2)", () => {
  it("mapeia exatamente os 4 estados em português", () => {
    expect(OUTBOX_STATUS_LABELS.queued).toBe("na fila");
    expect(OUTBOX_STATUS_LABELS.sent).toBe("enviado ao gateway do barco");
    expect(OUTBOX_STATUS_LABELS.expired).toBe("expirada (não entregue)");
    expect(OUTBOX_STATUS_LABELS.failed).toBe("falhou");
  });

  it("getOutboxStatusLabel devolve os rótulos exatos", () => {
    expect(getOutboxStatusLabel("queued")).toBe("na fila");
    expect(getOutboxStatusLabel("sent")).toBe("enviado ao gateway do barco");
    expect(getOutboxStatusLabel("expired")).toBe("expirada (não entregue)");
    expect(getOutboxStatusLabel("failed")).toBe("falhou");
    expect(getOutboxStatusLabel("desconhecido")).toBe("na fila");
  });

  it("REGRA CRÍTICA: o termo 'lido' NUNCA aparece em nenhum rótulo ou estado", () => {
    const allLabels = Object.values(OUTBOX_STATUS_LABELS);
    for (const label of allLabels) {
      expect(label.toLowerCase()).not.toContain("lido");
      expect(label.toLowerCase()).not.toContain("entregue ao tripulante");
    }
  });
});

describe("Formatação de data e idade relativa", () => {
  const t0 = Date.parse("2026-10-05T12:00:00.000Z");

  it("formatChatAge calcula intervalos em português", () => {
    expect(formatChatAge(null, t0)).toBe("agora");
    expect(formatChatAge("invalido", t0)).toBe("agora");

    // Menos de 60 segundos
    const iso30s = new Date(t0 - 30 * 1000).toISOString();
    expect(formatChatAge(iso30s, t0)).toBe("agora");

    // 10 minutos
    const iso10m = new Date(t0 - 10 * 60 * 1000).toISOString();
    expect(formatChatAge(iso10m, t0)).toBe("há 10 min");

    // 3 horas
    const iso3h = new Date(t0 - 3 * 3600 * 1000).toISOString();
    expect(formatChatAge(iso3h, t0)).toBe("há 3 h");

    // 2 dias
    const iso2d = new Date(t0 - 2 * 86400 * 1000).toISOString();
    expect(formatChatAge(iso2d, t0)).toBe("há 2 d");
  });

  it("formatChatTime formata hora ou traço se ausente", () => {
    expect(formatChatTime(null)).toBe("—");
    expect(formatChatTime("invalido")).toBe("—");
    const formatted = formatChatTime("2026-10-05T12:34:56.000Z");
    expect(formatted).toMatch(TIME_FORMAT_REGEX);
  });
});

describe("Alerta sonoro WebAudio", () => {
  it("não toca se muted for true", () => {
    expect(() => playAlertSound(true)).not.toThrow();
  });

  it("não lança exceção em ambiente de teste ou com falha de áudio", () => {
    expect(() => playAlertSound(false)).not.toThrow();
  });
});

describe("Persistência do mute em localStorage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("salva e recupera o valor booleano de muted", () => {
    expect(getChatMuted()).toBe(false);
    setChatMuted(true);
    expect(getChatMuted()).toBe(true);
    setChatMuted(false);
    expect(getChatMuted()).toBe(false);
  });
});

describe("Clientes de API de chat", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const jsonRes = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status });

  it("fetchChatBoats busca e devolve barcos", async () => {
    const boatsFixture = [
      { boat_id: "Barco 1", boat: "Barco 1", active: true },
      { boat_id: "Barco 2", boat: "Barco 2", active: true },
    ];
    fetchMock.mockResolvedValue(jsonRes(boatsFixture));

    const res = await fetchChatBoats();
    expect(res).toEqual(boatsFixture);
    expect(fetchMock).toHaveBeenCalledWith("/api/chat/boats", {
      credentials: "same-origin",
    });
  });

  it("fetchChatMessages busca mensagens com ou sem since", async () => {
    const msgsFixture = [
      {
        id: 1,
        direction: "in",
        boat_id: "Barco 1",
        text: "Olá",
        is_alert: false,
        observed_at: "2026-10-05T10:00:00Z",
        received_at: "2026-10-05T10:00:01Z",
      },
    ];
    fetchMock.mockResolvedValue(jsonRes(msgsFixture));

    const resSemSince = await fetchChatMessages();
    expect(resSemSince).toEqual(msgsFixture);
    expect(fetchMock).toHaveBeenCalledWith("/api/chat/messages", {
      credentials: "same-origin",
    });

    fetchMock.mockClear();
    fetchMock.mockResolvedValue(jsonRes([]));
    await fetchChatMessages("2026-10-05T10:00:00Z");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/messages?since=2026-10-05T10%3A00%3A00Z",
      { credentials: "same-origin" },
    );

    fetchMock.mockClear();
    fetchMock.mockResolvedValue(jsonRes([]));
    await fetchChatMessages(null, "", { limit: 50, before_id: 100 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/messages?limit=50&before_id=100",
      { credentials: "same-origin" },
    );
  });

  it("sendChatMessage posta corpo JSON e devolve id e status queued", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({
        id: 42,
        status: "queued",
        expires_at: "2026-10-05T12:15:00Z",
      }),
    );

    const res = await sendChatMessage("Barco 1", "Mensagem de teste");
    expect(res.id).toBe(42);
    expect(res.status).toBe("queued");
    expect(fetchMock).toHaveBeenCalledWith("/api/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ boat: "Barco 1", text: "Mensagem de teste" }),
    });
  });

  it("fetchOutboxStatus consulta item na fila", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({
        id: 42,
        boat_id: "Barco 1",
        status: "sent",
        label: "enviado ao gateway do barco",
        status_label: "enviado ao gateway do barco",
      }),
    );

    const res = await fetchOutboxStatus(42);
    expect(res.status).toBe("sent");
    expect(res.label).toBe("enviado ao gateway do barco");
    expect(fetchMock).toHaveBeenCalledWith("/api/chat/outbox/42", {
      credentials: "same-origin",
    });
  });
});

describe("Deduplicação de mensagens e avanço de cursor (WP-F2)", () => {
  it("maxReceivedAt avança para o maior timestamp received_at e ignora observed_at", () => {
    const msgs: ChatMessage[] = [
      {
        id: 1,
        direction: "in",
        boat_id: "b1",
        text: "Msg 1",
        is_alert: false,
        observed_at: "2026-10-05T12:00:00Z", // no futuro
        received_at: "2026-10-05T09:00:00Z",
      },
      {
        id: 2,
        direction: "in",
        boat_id: "b1",
        text: "Msg 2",
        is_alert: false,
        observed_at: null,
        received_at: "2026-10-05T09:05:00Z",
      },
    ];

    expect(maxReceivedAt([])).toBe(null);
    expect(maxReceivedAt(msgs, null)).toBe("2026-10-05T09:05:00Z");
    // Não regride se o cursor atual for mais recente que as mensagens recebidas
    expect(maxReceivedAt(msgs, "2026-10-05T09:10:00Z")).toBe(
      "2026-10-05T09:10:00Z",
    );
  });

  it("dedupeMessages deduplica por id e preserva ordenação cronológica", () => {
    const existentes: ChatMessage[] = [
      {
        id: 1,
        direction: "in",
        boat_id: "b1",
        text: "Primeira",
        is_alert: false,
        observed_at: null,
        received_at: "2026-10-05T09:00:00Z",
      },
      {
        id: 2,
        direction: "out",
        boat_id: "b1",
        text: "Segunda",
        is_alert: false,
        observed_at: null,
        received_at: "2026-10-05T09:01:00Z",
        status: "queued",
      },
    ];

    // Novas mensagens contendo sobreposição do id 2 (com status atualizado) e uma nova id 3
    const novas: ChatMessage[] = [
      {
        id: 2,
        direction: "out",
        boat_id: "b1",
        text: "Segunda",
        is_alert: false,
        observed_at: null,
        received_at: "2026-10-05T09:01:00Z",
        status: "sent",
      },
      {
        id: 3,
        direction: "in",
        boat_id: "b1",
        text: "Terceira",
        is_alert: true,
        observed_at: null,
        received_at: "2026-10-05T09:02:00Z",
      },
    ];

    const resultado = dedupeMessages(existentes, novas);
    expect(resultado).toHaveLength(3);
    expect(resultado.map((m) => m.id)).toEqual([1, 2, 3]);
    // Status do id 2 foi atualizado para sent
    expect(resultado[1].status).toBe("sent");
  });

  it("reconcilia mensagem otimista substituindo-a pela persistida por outbox_id", () => {
    const existentes: ChatMessage[] = [
      {
        id: -1728000000,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Mensagem em trânsito",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 42,
        status: "queued",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        status_label: "na fila",
      },
    ];

    const persistidas: ChatMessage[] = [
      {
        id: 105,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        from_num: 999999,
        text: "Mensagem em trânsito",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:02Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 42,
      },
    ];

    const resultado = dedupeMessages(existentes, persistidas);
    expect(resultado).toHaveLength(1);
    expect(resultado[0].id).toBe(105);
    expect(resultado[0].outbox_id).toBe(42);
    expect(resultado[0].status).toBe("queued");
    expect(resultado[0].status_label).toBe("na fila");
    // Garante que o id negativo foi completamente removido
    expect(resultado.some((m) => m.id < 0)).toBe(false);
  });

  it("reconcilia mensagem otimista casando por barco e texto quando outbox_id está ausente no backend", () => {
    const existentes: ChatMessage[] = [
      {
        id: -1728000001,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 2",
        text: "Ordem de retorno",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:01:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:01:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 99,
        status: "sent",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        status_label: "enviado ao gateway do barco",
      },
    ];

    const persistidas: ChatMessage[] = [
      {
        id: 200,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 2",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        from_num: 888888,
        text: "Ordem de retorno",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:01:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:01:05Z",
      },
    ];

    const resultado = dedupeMessages(existentes, persistidas);
    expect(resultado).toHaveLength(1);
    expect(resultado[0].id).toBe(200);
    // Preserva outbox_id e status da mensagem local para continuar rastreando fila
    expect(resultado[0].outbox_id).toBe(99);
    expect(resultado[0].status).toBe("sent");
    expect(resultado[0].status_label).toBe("enviado ao gateway do barco");
    expect(resultado.some((m) => m.id < 0)).toBe(false);
  });

  it("não confunde mensagem recebida (in) com mensagem otimista de saída (out) com texto idêntico", () => {
    const existentes: ChatMessage[] = [
      {
        id: -1728000002,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Câmbio",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:02:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:02:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 101,
      },
    ];

    const recebidasIn: ChatMessage[] = [
      {
        id: 300,
        direction: "in",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        from_num: 12345,
        text: "Câmbio",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:02:01Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:02:01Z",
      },
    ];

    const resultado = dedupeMessages(existentes, recebidasIn);
    // Ambas devem existir porque uma é de entrada e a outra é de saída otimista
    expect(resultado).toHaveLength(2);
    expect(resultado.map((m) => m.id)).toEqual([-1728000002, 300]);
  });

  it("reconcilia mensagens otimistas sequenciais com o mesmo texto para o mesmo barco", () => {
    const existentes: ChatMessage[] = [
      {
        id: -1,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Alô",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 1,
      },
      {
        id: -2,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Alô",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:05Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:05Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 2,
      },
    ];

    // Chega a primeira persistida
    const primeira = dedupeMessages(existentes, [
      {
        id: 11,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Alô",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:00Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:01Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 1,
      },
    ]);
    expect(primeira).toHaveLength(2);
    expect(primeira.map((m) => m.id)).toEqual([11, -2]);

    // Chega a segunda persistida
    const segunda = dedupeMessages(primeira, [
      {
        id: 12,
        direction: "out",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        boat_id: "Barco 1",
        text: "Alô",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        is_alert: false,
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        observed_at: "2026-10-05T12:00:05Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        received_at: "2026-10-05T12:00:06Z",
        // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
        outbox_id: 2,
      },
    ]);
    expect(segunda).toHaveLength(2);
    expect(segunda.map((m) => m.id)).toEqual([11, 12]);
    expect(segunda.some((m) => m.id < 0)).toBe(false);
  });
});
