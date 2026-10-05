// biome-ignore lint/style/useFilenamingConvention: convenção de arquivo de teste em Solid
import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalStateContext } from "../providers/index.js";
import { LocalState } from "../store.js";
import { ChatPanel } from "./ChatPanel.jsx";

const REGEX_ENVIAR = /enviar/i;
const REGEX_SILENCIAR = /Silenciar alertas sonoros/i;
const REGEX_ATIVAR = /Ativar alertas sonoros/i;

const renderChat = () =>
  render(() => (
    <LocalStateContext.Provider value={LocalState}>
      <ChatPanel />
    </LocalStateContext.Provider>
  ));

const fetchMock = vi.fn();

const jsonRes = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

const BOATS_FIXTURE = [
  { boat_id: "Barco 1", boat: "Barco 1", active: true },
  { boat_id: "Barco 2", boat: "Barco 2", active: true },
];

const MESSAGES_FIXTURE = [
  {
    id: 1,
    direction: "in",
    boat_id: "Barco 1",
    from_num: 12345,
    text: "Chegando ao ponto de apoio",
    is_alert: false,
    observed_at: "2026-10-05T12:00:00Z",
    received_at: "2026-10-05T12:00:05Z",
  },
  {
    id: 2,
    direction: "out",
    boat_id: "Barco 1",
    from_num: null,
    text: "Ciente Barco 1. Prossiga.",
    is_alert: false,
    observed_at: "2026-10-05T12:01:00Z",
    received_at: "2026-10-05T12:01:02Z",
    status: "sent",
    status_label: "enviado ao gateway do barco",
  },
  {
    id: 3,
    direction: "in",
    boat_id: "Barco 2",
    from_num: 67890,
    text: "SOCORRO: motor parou no meio do canal",
    is_alert: true,
    observed_at: "2026-10-05T12:02:00Z",
    received_at: "2026-10-05T12:02:03Z",
  },
  {
    id: 4,
    direction: "out",
    boat_id: "Barco 2",
    from_num: null,
    text: "Mensagem na fila",
    is_alert: false,
    observed_at: "2026-10-05T12:03:00Z",
    received_at: "2026-10-05T12:03:01Z",
    status: "queued",
    status_label: "na fila",
  },
  {
    id: 5,
    direction: "out",
    boat_id: "Barco 2",
    from_num: null,
    text: "Mensagem que expirou",
    is_alert: false,
    observed_at: "2026-10-05T12:04:00Z",
    received_at: "2026-10-05T12:04:01Z",
    status: "expired",
    status_label: "expirada (não entregue)",
  },
  {
    id: 6,
    direction: "out",
    boat_id: "Barco 2",
    from_num: null,
    text: "Mensagem que falhou",
    is_alert: false,
    observed_at: "2026-10-05T12:05:00Z",
    received_at: "2026-10-05T12:05:01Z",
    status: "failed",
    status_label: "falhou",
  },
];

describe("ChatPanel (SolidJS)", () => {
  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);

    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/chat/boats")) {
        return Promise.resolve(jsonRes(BOATS_FIXTURE));
      }
      if (url.includes("/api/chat/messages")) {
        return Promise.resolve(jsonRes(MESSAGES_FIXTURE));
      }
      return Promise.resolve(jsonRes({}));
    });

    LocalState.setChatOpen(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    vi.unstubAllGlobals();
    LocalState.setChatOpen(false);
  });

  it("renderiza o cabeçalho, o seletor de barcos e as mensagens", async () => {
    renderChat();

    // Aguarda carregar dados
    await new Promise((r) => setTimeout(r, 50));

    expect(screen.getByText("Chat da malha")).toBeDefined();
    expect(screen.getByText("Chegando ao ponto de apoio")).toBeDefined();
    expect(screen.getByText("Ciente Barco 1. Prossiga.")).toBeDefined();

    // Seletor de barcos
    const select = screen.getByLabelText(
      "Selecionar barco",
    ) as HTMLSelectElement;
    expect(select).toBeDefined();
    expect(select.options.length).toBeGreaterThanOrEqual(3); // "todos" + 2 barcos
  });

  it("destaca visualmente mensagem de alerta com 'ALERTA'", async () => {
    renderChat();
    await new Promise((r) => setTimeout(r, 50));

    const alertaTexto = screen.getByText(
      "SOCORRO: motor parou no meio do canal",
    );
    expect(alertaTexto).toBeDefined();

    const badgeAlerta = screen.getByText("ALERTA");
    expect(badgeAlerta).toBeDefined();
  });

  it("contador de bytes UTF-8 ao vivo atualiza com caracteres multibyte", async () => {
    renderChat();
    await new Promise((r) => setTimeout(r, 50));

    const textarea = screen.getByLabelText(
      "Texto da mensagem",
    ) as HTMLTextAreaElement;
    expect(screen.getByText("0/200 bytes")).toBeDefined();

    // Digita "Curuçá" (8 bytes UTF-8)
    fireEvent.input(textarea, { target: { value: "Curuçá" } });
    expect(screen.getByText("8/200 bytes")).toBeDefined();

    // Digita 200 caracteres 'a'
    fireEvent.input(textarea, { target: { value: "a".repeat(200) } });
    expect(screen.getByText("200/200 bytes")).toBeDefined();

    // 201 bytes desabilita o botão
    fireEvent.input(textarea, { target: { value: "a".repeat(201) } });
    expect(screen.getByText("201/200 bytes")).toBeDefined();

    const btnEnviar = screen.getByRole("button", {
      name: REGEX_ENVIAR,
    }) as HTMLButtonElement;
    expect(btnEnviar.disabled).toBe(true);
  });

  it("exibe todos os 4 estados de saída exatamente em português", async () => {
    renderChat();
    await new Promise((r) => setTimeout(r, 50));

    expect(screen.getByText("na fila")).toBeDefined();
    expect(screen.getByText("enviado ao gateway do barco")).toBeDefined();
    expect(screen.getByText("expirada (não entregue)")).toBeDefined();
    expect(screen.getByText("falhou")).toBeDefined();
  });

  it("REGRA CRÍTICA: o termo 'lido' NUNCA aparece em nenhum estado renderizado", async () => {
    const { container } = renderChat();
    await new Promise((r) => setTimeout(r, 50));

    const htmlRenderizado = container.innerHTML.toLowerCase();

    // Asserções estritas que garantem a política de privacidade
    expect(htmlRenderizado).not.toContain("lido");
    expect(htmlRenderizado).not.toContain("lida");
    expect(htmlRenderizado).not.toContain("entregue ao tripulante");
  });

  it("botão de mudo alterna estado e persiste no localStorage", async () => {
    renderChat();
    await new Promise((r) => setTimeout(r, 50));

    const btnMudo = screen.getByLabelText(REGEX_SILENCIAR);
    expect(btnMudo).toBeDefined();

    fireEvent.click(btnMudo);
    expect(localStorage.getItem("rastro_chat_muted")).toBe("true");

    const btnAtivar = screen.getByLabelText(REGEX_ATIVAR);
    expect(btnAtivar).toBeDefined();

    fireEvent.click(btnAtivar);
    expect(localStorage.getItem("rastro_chat_muted")).toBe("false");
  });

  it("reconcilia mensagem otimista no envio substituindo-a pela persistida sem duplicar", async () => {
    vi.useFakeTimers();
    let returnPersisted = false;
    const msgText = "Nova instrução de patrulha";
    const outboxId = 77;

    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/chat/boats")) {
        return Promise.resolve(jsonRes(BOATS_FIXTURE));
      }
      if (url.includes("/api/chat/send")) {
        return Promise.resolve(
          jsonRes({
            id: outboxId,
            status: "queued",
            // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
            expires_at: "2026-10-05T13:00:00Z",
          }),
        );
      }
      if (url.includes("/api/chat/outbox/")) {
        return Promise.resolve(
          jsonRes({
            id: outboxId,
            // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
            boat_id: "Barco 1",
            status: "queued",
            label: "na fila",
            // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
            status_label: "na fila",
          }),
        );
      }
      if (url.includes("/api/chat/messages")) {
        if (returnPersisted) {
          return Promise.resolve(
            jsonRes([
              ...MESSAGES_FIXTURE,
              {
                id: 100,
                direction: "out",
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                boat_id: "Barco 1",
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                from_num: 999999,
                text: msgText,
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                is_alert: false,
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                observed_at: "2026-10-05T12:10:00Z",
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                received_at: "2026-10-05T12:10:01Z",
                // biome-ignore lint/style/useNamingConvention: compatibilidade de propriedade da API
                outbox_id: outboxId,
              },
            ]),
          );
        }
        return Promise.resolve(jsonRes(MESSAGES_FIXTURE));
      }
      return Promise.resolve(jsonRes({}));
    });

    renderChat();
    await vi.advanceTimersByTimeAsync(50);

    const textarea = screen.getByLabelText("Texto da mensagem");
    fireEvent.input(textarea, { target: { value: msgText } });

    const btnEnviar = screen.getByRole("button", { name: REGEX_ENVIAR });
    expect((btnEnviar as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(btnEnviar);
    await vi.advanceTimersByTimeAsync(60);

    // Mensagem otimista presente
    expect(screen.getAllByText(msgText).length).toBe(1);

    // Backend agora retorna a versão persistida no poll seguinte
    returnPersisted = true;
    await vi.advanceTimersByTimeAsync(5500);

    // A mensagem deve continuar aparecendo exatamente UMA vez (reconciliada, sem duplicata)
    expect(screen.getAllByText(msgText).length).toBe(1);
  });

  it("tenta recarregar lista de barcos no loop de polling após falha inicial e habilita envio", async () => {
    vi.useFakeTimers();
    let boatFetchCount = 0;

    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/chat/boats")) {
        boatFetchCount++;
        if (boatFetchCount === 1) {
          // Primeira tentativa falha
          return Promise.reject(new Error("Falha temporária de rede"));
        }
        // Segunda tentativa (via poll) é bem-sucedida
        return Promise.resolve(jsonRes(BOATS_FIXTURE));
      }
      if (url.includes("/api/chat/messages")) {
        return Promise.resolve(jsonRes([]));
      }
      return Promise.resolve(jsonRes({}));
    });

    renderChat();
    await vi.advanceTimersByTimeAsync(50);

    // Inicialmente a lista de barcos falhou, logo não há barcos disponíveis para envio
    const select = screen.getByLabelText(
      "Selecionar barco",
    ) as HTMLSelectElement;
    expect(select.options.length).toBe(1); // apenas "todos"

    const textarea = screen.getByLabelText(
      "Texto da mensagem",
    ) as HTMLTextAreaElement;
    fireEvent.input(textarea, { target: { value: "Tentativa de envio" } });

    const btnEnviar = screen.getByRole("button", {
      name: REGEX_ENVIAR,
    }) as HTMLButtonElement;
    // Envio desabilitado pois não há barco de destino
    expect(btnEnviar.disabled).toBe(true);

    // Avança o tempo para disparar o loop de polling (5s)
    await vi.advanceTimersByTimeAsync(5500);

    // O polling deve ter tentado carregar os barcos novamente
    expect(boatFetchCount).toBeGreaterThanOrEqual(2);
    expect(select.options.length).toBeGreaterThanOrEqual(3); // "todos" + Barco 1 + Barco 2

    // Com o barco carregado automaticamente como destino, o botão de envio deve estar habilitado
    expect(btnEnviar.disabled).toBe(false);
  });
});
