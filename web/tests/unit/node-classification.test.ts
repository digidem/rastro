import { describe, expect, it } from "vitest";
import {
  hardwareModelLabel,
  isBoatNode,
  nodeKind,
  nodeSidebarSvgUrl,
} from "../../src/lib/nodes";

// biome-ignore lint/suspicious/noExplicitAny: fixture mínima
const n = (nome: string, extra: Record<string, unknown> = {}): any => ({
  nome,
  ...extra,
});

describe("detecção de barcos (Tarefa 18)", () => {
  it("nome com barco é barco", () => {
    expect(isBoatNode(n("univaja-barco-1"))).toBe(true);
  });

  it.each([
    "univaja-atalaia-movel-2",
    "univaja-teto-1",
    "univaja-cartao-3",
    "base-fixa",
  ])("%s nunca é barco, mesmo com kind=boat", (nome) => {
    expect(isBoatNode(n(nome, { kind: "boat" }))).toBe(false);
    expect(nodeKind(n(nome, { kind: "boat" }))).not.toBe("boat");
  });

  it("infere kind pelo nome", () => {
    expect(nodeKind(n("atalaia-movel-2"))).toBe("handheld");
    expect(nodeKind(n("x-cartao-1"))).toBe("handheld");
    expect(nodeKind(n("x-teto-1"))).toBe("fixed_station");
  });

  it("infere hardware e SVG", () => {
    expect(hardwareModelLabel(n("atalaia-movel-2"))).toBe("Heltec V4");
    expect(hardwareModelLabel(n("univaja-atalaia-admin-movel-1"))).toBe(
      "Heltec V4",
    );
    expect(hardwareModelLabel(n("admin-movel"))).toBe("Heltec V4");
    expect(hardwareModelLabel(n("univaja-barco-1"))).toBe("Heltec V4");
    expect(hardwareModelLabel(n("x-teto-1"))).toBe("Heltec V4");
    expect(hardwareModelLabel(n("tbeam-att1"))).toBe("T-Beam");
    expect(hardwareModelLabel(n("barco-rak-1"))).toBe("RAK4631");
    // Nomes que contenham 'rak' ou 'echo' embutidos em palavras não são falsos positivos
    expect(hardwareModelLabel(n("araka-estacao"))).toBeNull();
    expect(nodeSidebarSvgUrl(n("univaja-barco-1"))).toBe(
      "/devices/heltec_v4.svg",
    );
    expect(nodeSidebarSvgUrl(n("x-cartao-1"))).toBe(
      "/devices/tracker-t1000-e.svg",
    );
    expect(nodeSidebarSvgUrl(n("atalaia-movel-2", { kind: "boat" }))).toBe(
      "/devices/heltec_v4.svg",
    );
  });
});
