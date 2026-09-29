import type { APIRequestContext, Page, Response } from "@playwright/test";
import { expect, test } from "@playwright/test";

const tokenE2e = process.env.RASTRO_API_TOKEN || "teste-token-123";

// Regex no escopo do módulo (lint/performance/useTopLevelRegex).
const MSG_CSP = /content[ -]security[ -]policy|csp violation/i;
const BTN_ENTRAR = /Entrar/i;
const TITULO_Rastro = /Rastro/i;

// GET público /api/auth/estado via fixture request (baseURL da config);
// devolve só o que os testes usam, com o snake_case cru lido por índice.
const estadoAuth = async (
  request: APIRequestContext,
): Promise<{ exigida: boolean }> => {
  const res = await request.get("/api/auth/estado");
  const corpo = (await res.json()) as Record<string, unknown>;
  return { exigida: corpo.exigida === true };
};

const botaoSair = (page: Page) =>
  page.locator("button").filter({ hasText: "Sair" });

const telaDeToken = (page: Page) => page.locator("input[type='password']");

// Carga REAL do mapa: canvas visível + UMA requisição sob /tiles/ com 200/206
// (404/204 ou só headers não provam render) e, se glyphs forem pedidos,
// respostas 200 deles. Armado ANTES da navegação/carga que dispara tiles.
const prepararMapa = async (page: Page) => {
  const tiles = page.waitForResponse(
    (r) =>
      r.url().includes("/tiles/") && (r.status() === 200 || r.status() === 206),
    { timeout: 20000 },
  );
  const respostas: Response[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/tiles/")) {
      respostas.push(r);
    }
  });

  return async () => {
    await tiles;
    await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
    // Nenhuma falha HTTP no pacote de tiles/glyphs — 404 não é carga real.
    const falhas = respostas
      .filter((r) => r.status() >= 400)
      .map((r) => `${r.status()} ${r.url()}`);
    expect(falhas, "respostas de tiles/glyphs com falha").toEqual([]);
    for (const g of respostas.filter((r) =>
      r.url().includes("/tiles/glyphs/"),
    )) {
      expect(g.status(), `glyph ${g.url()}`).toBe(200);
    }
  };
};

// Login por token (cookie HttpOnly); "lembrar" grava cookie persistente.
const entrarComToken = async (page: Page, lembrar = false) => {
  await page.goto("/");
  await telaDeToken(page).fill(tokenE2e);
  if (lembrar) {
    await page.locator("input[type='checkbox']").check();
  }
  await page.locator("button").filter({ hasText: BTN_ENTRAR }).click();
};

test.describe("Rastro Map Viewer (e2e)", () => {
  // Mensagens acumuladas EM ARRAY NODE-SIDE: handlers page.on vivem fora do
  // documento e sobrevivem a reloads; nada de estado dentro da página.
  let erros: string[];

  test.beforeEach(async ({ page }) => {
    erros = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        erros.push(`console.error: ${msg.text()}`);
      }
    });
    page.on("pageerror", (err) => {
      erros.push(`pageerror: ${err.message}`);
    });
    // Violação de CSP espelhada no console: o listener é registrado em cada
    // documento novo pelo init script — um array DENTRO do documento morreria
    // no reload (documento novo), o espelho via console + array node-side não.
    await page.addInitScript(() => {
      window.addEventListener("securitypolicyviolation", (e) => {
        // biome-ignore lint/suspicious/noConsole: espelho exigido da violação CSP no console
        console.error(`CSP violation: ${e.effectiveDirective}`);
      });
    });
  });

  test.afterEach(() => {
    const pageerrors = erros.filter((t) => t.startsWith("pageerror:"));
    const csp = erros.filter((t) => MSG_CSP.test(t));
    expect(pageerrors, "pageerror capturado").toEqual([]);
    expect(csp, "violação de Content-Security-Policy").toEqual([]);
  });

  test("1. tela inicial bloqueia sem autenticação e exibe prompt de token", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    await page.goto("/");
    // Deve exibir o título, o campo de senha do token e o checkbox de lembrar
    await expect(
      page.locator("h1, h2, h3").filter({ hasText: TITULO_Rastro }),
    ).toBeVisible();
    await expect(telaDeToken(page)).toBeVisible();
    await expect(page.locator("input[type='checkbox']")).not.toBeChecked();
    await expect(
      page.locator("button").filter({ hasText: BTN_ENTRAR }),
    ).toBeVisible();
  });

  test("2. token incorreto exibe erro e não abre o mapa", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    await page.goto("/");
    await telaDeToken(page).fill("token-errado-1234");
    await page.locator("button").filter({ hasText: BTN_ENTRAR }).click();
    await expect(page.locator("text=token inválido")).toBeVisible();
    await expect(page.locator("canvas.maplibregl-canvas")).toHaveCount(0);
  });

  test("3. token válido autentica e exibe o painel de nós da malha", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    const mapa = await prepararMapa(page);
    await entrarComToken(page);
    await mapa();
    // Painel lateral com título e canvas do MapLibre renderizaram — e o mapa
    // carregou tiles de verdade (helper valida 200/206 sob /tiles/).
    await expect(
      page.locator("h3").filter({ hasText: "Nós da malha" }),
    ).toBeVisible();
  });

  test("4. login com lembrar recarrega direto no mapa", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    const mapa = await prepararMapa(page);
    await entrarComToken(page, true);
    await mapa();

    // Reload: cookie persistente autentica de novo — sem tela de token.
    const mapaReload = await prepararMapa(page);
    await page.reload();
    await mapaReload();
    await expect(telaDeToken(page)).toHaveCount(0);
  });

  test("5. login sem lembrar cria cookie de sessão sem expires e httpOnly", async ({
    page,
    context,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    const mapa = await prepararMapa(page);
    await entrarComToken(page);
    await mapa();

    const cookies = await context.cookies();
    const sessao = cookies.find((c) => c.name === "rastro_sessao");
    expect(sessao, "cookie rastro_sessao existe").toBeDefined();
    expect(sessao?.expires).toBe(-1); // sem expires: cookie de sessão
    expect(sessao?.httpOnly).toBe(true); // JS nunca lê
  });

  test("6. sair volta para a tela de token e sobrevive ao reload", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    const mapa = await prepararMapa(page);
    await entrarComToken(page);
    await mapa();

    await botaoSair(page).click();
    await expect(telaDeToken(page)).toBeVisible();
    // Reload: continua pedindo token (cookie foi apagado).
    await page.reload();
    await expect(telaDeToken(page)).toBeVisible();
  });

  test("7. sair com DELETE bloqueado mantém a sessão e o mapa", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(!estado.exigida, "servidor em modo sem senha");
    const mapa = await prepararMapa(page);
    await entrarComToken(page);
    await mapa();

    await page.route("**/api/auth/sessao", (route) => route.abort());
    await botaoSair(page).click();
    // NÃO vira tela de token: o cookie segue válido — mentir "saiu" seria
    // furo de segurança.
    await expect(telaDeToken(page)).not.toBeVisible();
    await expect(page.locator("text=Não foi possível sair")).toBeVisible();
    await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();

    // Sem o bloqueio: reload revalida o cookie e reabre o mapa (tiles de verdade).
    await page.unroute("**/api/auth/sessao");
    const mapaReload = await prepararMapa(page);
    await page.reload();
    await mapaReload();
    await expect(telaDeToken(page)).toHaveCount(0);
  });

  test("8. modo sem senha abre direto no mapa, sem token e sem sair", async ({
    page,
    request,
  }) => {
    const estado = await estadoAuth(request);
    test.skip(estado.exigida, "servidor exige token");
    const mapa = await prepararMapa(page);
    await page.goto("/");
    await mapa();
    await expect(telaDeToken(page)).toHaveCount(0);
    await expect(botaoSair(page)).toHaveCount(0);
  });
});
