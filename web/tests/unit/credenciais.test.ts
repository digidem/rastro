import { describe, expect, it } from "vitest";
import {
  AVISO_SEM_TLS,
  credenciaisPermitidas,
} from "../../src/lib/credenciais.js";

// Loc igual ao que o browser entrega em window.location (só os dois campos que
// a decisão usa); hostname de IPv6 chega entre colchetes.
const loc = (href: string) => {
  const u = new URL(href);
  return { protocol: u.protocol, hostname: u.hostname };
};

describe("credenciaisPermitidas", () => {
  it("libera https em qualquer host", () => {
    expect(credenciaisPermitidas(loc("https://mapa.exemplo.org/"))).toBe(true);
    expect(credenciaisPermitidas(loc("https://mapa.local:8443/mapa"))).toBe(
      true,
    );
  });

  it("nega http em host não local", () => {
    expect(credenciaisPermitidas(loc("http://mapa.exemplo.org/"))).toBe(false);
    expect(credenciaisPermitidas(loc("http://10.0.0.5:8081/"))).toBe(false);
    // "localhost" como sufixo não é localhost:
    expect(credenciaisPermitidas(loc("http://mapa.localhost.br/"))).toBe(false);
  });

  it("libera o loopback em http (perfil da bancada)", () => {
    expect(credenciaisPermitidas(loc("http://localhost:5173/"))).toBe(true);
    expect(credenciaisPermitidas(loc("http://127.0.0.1:8081/"))).toBe(true);
    expect(credenciaisPermitidas(loc("http://[::1]:8081/"))).toBe(true);
  });

  it("sem argumento decide por window.location", () => {
    // jsdom sobe em http://localhost:3000 — logo, é o caso liberado.
    expect(credenciaisPermitidas()).toBe(true);
  });

  it("o aviso ao usuário está em pt-BR", () => {
    expect(AVISO_SEM_TLS).toBe(
      "Este endereço não usa HTTPS. Ative HTTPS antes de entrar.",
    );
  });
});
