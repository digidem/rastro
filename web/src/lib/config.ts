import { createSignal } from "solid-js";

/**
 * Título do deploy, lido de /config.json em runtime — arquivo que o entrypoint
 * do container escreve a partir de RASTRO_TITLE. O nome do mapa muda com uma
 * variável do CapRover, sem rebuildar a imagem.
 *
 * Deu qualquer coisa errada (sem /config.json no `pnpm dev`, JSON inválido,
 * campo ausente, rede caída) fica o padrão "Rastro": o mapa nunca fica sem
 * título.
 */
export const TITULO_PADRAO = "Rastro";

/** Sinal do título: lido pelo `<Title>` do card; escrito por iniciarTitulo(). */
export const [titulo, setTitulo] = createSignal<string>(TITULO_PADRAO);

/**
 * GET /config.json (same-origin, sem credenciais, sem cache). Devolve o
 * `title` quando é uma string não vazia; TITULO_PADRAO em qualquer outro caso.
 */
export const carregarTitulo = async (): Promise<string> => {
  try {
    const res = await fetch("/config.json", {
      cache: "no-store",
      credentials: "omit",
    });
    if (!res.ok) {
      return TITULO_PADRAO;
    }
    // Leitura leve como a do /api/auth/estado: corpo inesperado cai no padrão.
    const corpo = (await res.json().catch(() => ({}))) as unknown;
    const o = (corpo ?? {}) as Record<string, unknown>;
    return typeof o.title === "string" && o.title.trim() !== ""
      ? o.title
      : TITULO_PADRAO;
  } catch {
    return TITULO_PADRAO;
  }
};

/**
 * Carrega o título uma vez na subida do app: o sinal (para o `<Title>` do
 * card) e o `document.title` (aba do navegador).
 */
export const iniciarTitulo = async (): Promise<void> => {
  const t = await carregarTitulo();
  setTitulo(t);
  if (typeof document !== "undefined") {
    document.title = t;
  }
};
