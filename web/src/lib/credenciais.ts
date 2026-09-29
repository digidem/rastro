/**
 * Credenciais (cookie de sessão / Authorization) só viajam por canal seguro.
 *
 * Negação por padrão: http: só é aceito em loopback — o contexto da bancada
 * (o caddy local escuta em 127.0.0.1). Num http://mapa.exemplo.org da vida o
 * token e o cookie de sessão iriam em claro e qualquer um na rota leria, então
 * o viewer recusa a tela de login e pede HTTPS.
 */
export interface Loc {
  /** `window.location.protocol` — "https:" / "http:". */
  protocol: string;
  /** `window.location.hostname` — IPv6 vem entre colchetes ("[::1]"). */
  hostname: string;
}

export const credenciaisPermitidas = (loc: Loc = window.location): boolean =>
  loc.protocol === "https:" ||
  loc.hostname === "localhost" ||
  loc.hostname === "127.0.0.1" ||
  loc.hostname === "[::1]";

/** Aviso mostrado no lugar da tela de login quando não se pode autenticar. */
export const AVISO_SEM_TLS =
  "Este endereço não usa HTTPS. Ative HTTPS antes de entrar.";
