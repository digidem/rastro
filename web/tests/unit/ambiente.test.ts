import { describe, expect, it } from "vitest";
import {
  ambienteDesatualizado,
  formatarAmbiente,
  rotuloIdadeAmbiente,
} from "../../src/lib/ambiente";
import type { LeituraAmbiente } from "../../src/store";

const AGORA = Date.parse("2026-10-24T12:00:00Z");

const leitura = (parcial: Partial<LeituraAmbiente> = {}): LeituraAmbiente => ({
  nodeNum: 1,
  nodeId: "!00000001",
  nome: "Sensor 1",
  temperaturaC: 27.34,
  umidade: 82.4,
  pressaoHpa: 1009.4,
  telemTime: "2026-10-24T11:55:00Z",
  ...parcial,
});

describe("formatarAmbiente", () => {
  it("formata em pt-BR com rótulos", () => {
    expect(formatarAmbiente(leitura())).toBe(
      "27,3 °C · umidade 82 % · 1.009 hPa",
    );
  });

  it("medida ausente fica de fora; sem nenhuma, traço", () => {
    expect(formatarAmbiente(leitura({ pressaoHpa: null, umidade: null }))).toBe(
      "27,3 °C",
    );
    expect(
      formatarAmbiente(
        leitura({ temperaturaC: null, umidade: null, pressaoHpa: null }),
      ),
    ).toBe("—");
  });

  it("zero é leitura válida", () => {
    expect(formatarAmbiente(leitura({ temperaturaC: 0 }))).toContain("0,0 °C");
  });
});

describe("idade da leitura", () => {
  it.each([
    ["2026-10-24T11:59:40Z", "agora"],
    ["2026-10-24T11:55:00Z", "há 5 min"],
    ["2026-10-24T09:00:00Z", "há 3 h"],
    ["2026-10-20T12:00:00Z", "há 4 d"],
    ["2026-10-24T12:03:00Z", "agora"], // relógio adiantado: nunca negativa
  ])("%s → %s", (telemTime, esperado) => {
    expect(rotuloIdadeAmbiente(leitura({ telemTime }), AGORA)).toBe(esperado);
  });

  it("mais de 1 h é desatualizada", () => {
    expect(ambienteDesatualizado(leitura(), AGORA)).toBe(false);
    expect(
      ambienteDesatualizado(
        leitura({ telemTime: "2026-10-24T10:30:00Z" }),
        AGORA,
      ),
    ).toBe(true);
  });
});
