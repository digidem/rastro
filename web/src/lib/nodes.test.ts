import { describe, expect, it } from "vitest";
import type { NodeInfo } from "../types";
import {
  deviceModelSvgUrl,
  hardwareEfetivo,
  isBoatNode,
  nodeKind,
  nodeSidebarSvgUrl,
} from "./nodes";

const INDICATOR_SVG = "/devices/seeed-sensecap-indicator.svg";

function no(parcial: Partial<NodeInfo>): NodeInfo {
  return { nodeNum: 1, nome: "x", ...parcial } as NodeInfo;
}

describe("torre", () => {
  it("é classificada como estação fixa (base), nunca barco", () => {
    const torre = no({ nome: "Torre Atalaia" });
    expect(nodeKind(torre)).toBe("fixed_station");
    expect(isBoatNode(torre)).toBe(false);
  });

  it("kind=boat do backend não prevalece sobre o nome torre", () => {
    expect(nodeKind(no({ nome: "torre-1", kind: "boat" }))).toBe(
      "fixed_station",
    );
  });

  it("usa o ícone SenseCAP Indicator pelo nome, mesmo com hwModel desconhecido", () => {
    const torre = no({ nome: "Torre Atalaia", hwModel: "ALGO_NAO_MAPEADO" });
    expect(hardwareEfetivo(torre.hwModel, torre.nome)).toBe(
      "SENSECAP_INDICATOR",
    );
    expect(nodeSidebarSvgUrl(torre)).toBe(INDICATOR_SVG);
  });

  it("usa o ícone sem hwModel", () => {
    expect(nodeSidebarSvgUrl(no({ nome: "torre", hwModel: null }))).toBe(
      INDICATOR_SVG,
    );
  });
});

describe("deviceModelSvgUrl", () => {
  it("mapeia SENSECAP_INDICATOR", () => {
    expect(deviceModelSvgUrl("SENSECAP_INDICATOR")).toBe(INDICATOR_SVG);
  });

  it("não afeta outros nós", () => {
    expect(hardwareEfetivo("HELTEC_V3", "barco-1")).toBe("HELTEC_V3");
    expect(deviceModelSvgUrl(null)).toBe("/devices/unknown.svg");
  });
});
