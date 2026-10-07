import { describe, expect, it } from "vitest";
import {
  batteryLabel,
  batteryLevel,
  deviceModelSvgUrl,
  fixAgeLabel,
  formatAgeFromSeconds,
  formatDateTimeJavari,
  hardwareModelLabel,
  hasConfirmedPosition,
  isAgeWarning,
  isBoatNode,
  isFixStale,
  isNodeOlderThan7Days,
  matchesFilters,
  matchesQuery,
  nodeAgeLabel,
  nodeKind,
  nodeSidebarSvgUrl,
  nodesGeoJson,
} from "../../src/lib/nodes.js";
import type { NodeInfo } from "../../src/store.js";

// Nó de referência: hex do nodeNum é "abcd1234" (bate com o nodeId).
const no = (over: Partial<NodeInfo> = {}): NodeInfo => ({
  nodeNum: 0xabcd1234,
  nodeId: "!abcd1234",
  nome: "Nó A",
  posTime: "2026-09-29T12:00:00.000Z",
  battery: 87,
  lon: -70.3,
  lat: -4.5,
  ...over,
});

const NOW = Date.parse("2026-09-29T21:00:00.000Z");
const horasAtras = (h: number) => new Date(NOW - h * 3600 * 1000).toISOString();

describe("matchesQuery", () => {
  it("query vazia (ou só '!') não filtra ninguém", () => {
    expect(matchesQuery(no(), "")).toBe(true);
    expect(matchesQuery(no(), "   ")).toBe(true);
    expect(matchesQuery(no(), "!")).toBe(true);
  });

  it("acha por trecho do nome, sem caixa", () => {
    expect(matchesQuery(no({ nome: "Base Curuçá" }), "BASE")).toBe(true);
    expect(matchesQuery(no({ nome: "Base Curuçá" }), "curuça")).toBe(true);
    expect(matchesQuery(no({ nome: "Base Curuçá" }), "zzz")).toBe(false);
  });

  it("ignora acentos nos dois lados", () => {
    expect(matchesQuery(no({ nome: "Estação Curuçá" }), "estacao")).toBe(true);
    expect(matchesQuery(no({ nome: "Estação Curuçá" }), "ESTAÇÃO")).toBe(true);
  });

  it("acha pelo shortName", () => {
    expect(matchesQuery(no({ shortName: "ITQ1" }), "itq1")).toBe(true);
    expect(matchesQuery(no({ shortName: null }), "itq1")).toBe(false);
  });

  it("acha pelo nodeId com e sem '!'", () => {
    expect(matchesQuery(no(), "!abcd1234")).toBe(true);
    expect(matchesQuery(no(), "abcd1234")).toBe(true);
    expect(matchesQuery(no(), "abcd")).toBe(true);
    expect(matchesQuery(no(), "ffff")).toBe(false);
  });

  it("usa o hex do nodeNum mesmo sem nodeId", () => {
    expect(matchesQuery(no({ nodeId: "" }), "abcd1234")).toBe(true);
  });
});

describe("hasConfirmedPosition", () => {
  it("aceita coordenada finita dentro dos limites com posTime parseável", () => {
    expect(hasConfirmedPosition(no())).toBe(true);
  });

  it("rejeita coordenada não finita ou fora da faixa geográfica", () => {
    expect(hasConfirmedPosition(no({ lon: Number.NaN }))).toBe(false);
    expect(hasConfirmedPosition(no({ lat: Number.POSITIVE_INFINITY }))).toBe(
      false,
    );
    expect(hasConfirmedPosition(no({ lon: 400 }))).toBe(false);
    expect(hasConfirmedPosition(no({ lat: -91 }))).toBe(false);
  });

  it("rejeita posTime ausente ou inválido", () => {
    expect(hasConfirmedPosition(no({ posTime: null }))).toBe(false);
    expect(hasConfirmedPosition(no({ posTime: "ontem" }))).toBe(false);
  });
});

describe("nodeKind", () => {
  it("devolve a categoria do contrato", () => {
    expect(nodeKind(no({ kind: "boat" }))).toBe("boat");
    expect(nodeKind(no({ kind: "fixed_station" }))).toBe("fixed_station");
    expect(nodeKind(no({ kind: "handheld" }))).toBe("handheld");
  });

  it("detecta 'boat' quando o nome contém 'barco' (case-insensitive e sem acentos)", () => {
    expect(nodeKind(no({ nome: "barco-1" }))).toBe("boat");
    expect(nodeKind(no({ nome: "univaja-curuca-barco-1" }))).toBe("boat");
    expect(nodeKind(no({ nome: "BARCO DE APOIO" }))).toBe("boat");
  });

  it("sem metadado e sem 'barco' no nome, é 'unknown'", () => {
    expect(nodeKind(no({ nome: "Base Curuçá" }))).toBe("fixed_station");
    expect(nodeKind(no({ nome: "Meshtastic 1234" }))).toBe("unknown");
  });
});

describe("isBoatNode", () => {
  it("retorna true para kind boat ou nome contendo barco", () => {
    expect(isBoatNode(no({ kind: "boat", nome: "Qualquer" }))).toBe(true);
    expect(isBoatNode(no({ nome: "univaja-itui-barco-1" }))).toBe(true);
    expect(isBoatNode(no({ nome: "Base Principal" }))).toBe(false);
  });
});

describe("hardwareModelLabel e nodeSidebarSvgUrl", () => {
  it("nodeSidebarSvgUrl retorna boat.svg para barcos e placa de hardware para outros nós", () => {
    expect(nodeSidebarSvgUrl(no({ nome: "univaja-itui-barco-1", hwModel: "HELTEC_V4" }))).toBe(
      "/devices/boat.svg",
    );
    expect(nodeSidebarSvgUrl(no({ nome: "escritorio-fixo-1", hwModel: "TBEAM" }))).toBe(
      "/devices/tbeam.svg",
    );
    expect(nodeSidebarSvgUrl(no({ nome: "desconhecido", hwModel: null }))).toBe(
      "/devices/unknown.svg",
    );
  });

  it("hardwareModelLabel formata e infere modelos conhecidos", () => {
    expect(hardwareModelLabel(no({ hwModel: "HELTEC_V4" }))).toBe("Heltec V4");
    expect(hardwareModelLabel(no({ hwModel: "TRACKER_T1000_E" }))).toBe("T1000-E");
    expect(hardwareModelLabel(no({ hwModel: null, nome: "univaja-cartao-1" }))).toBe("T1000-E");
    expect(hardwareModelLabel(no({ hwModel: null, nome: "heltec-v4-itq1" }))).toBe("Heltec V4");
    expect(hardwareModelLabel(no({ hwModel: null, nome: "estacao-sem-modelo" }))).toBeNull();
  });
});

describe("isFixStale", () => {
  it("é estritamente maior que 12 h", () => {
    expect(isFixStale(horasAtras(13), NOW)).toBe(true);
    expect(isFixStale(horasAtras(11), NOW)).toBe(false);
    expect(isFixStale(horasAtras(12), NOW)).toBe(false);
  });

  it("timestamp ausente/inválido nunca é 'antigo'", () => {
    expect(isFixStale(null, NOW)).toBe(false);
    expect(isFixStale("quebrado", NOW)).toBe(false);
  });
});

describe("isNodeOlderThan7Days", () => {
  it("detecta nós com mais de 7 dias usando age_s", () => {
    expect(isNodeOlderThan7Days({ age_s: 7 * 86400 + 1 }, NOW)).toBe(true);
    expect(isNodeOlderThan7Days({ age_s: 7 * 86400 - 10 }, NOW)).toBe(false);
  });

  it("detecta nós com mais de 7 dias usando posTime", () => {
    const oitoDiasAtras = new Date(NOW - 8 * 86400 * 1000).toISOString();
    const tresDiasAtras = new Date(NOW - 3 * 86400 * 1000).toISOString();
    expect(isNodeOlderThan7Days({ posTime: oitoDiasAtras }, NOW)).toBe(true);
    expect(isNodeOlderThan7Days({ posTime: tresDiasAtras }, NOW)).toBe(false);
    expect(isNodeOlderThan7Days({ posTime: null }, NOW)).toBe(false);
  });
});

describe("matchesFilters", () => {
  const filtros = (
    over: Partial<{
      query: string;
      kindFilter: "all" | "boat" | "fixed_station" | "handheld" | "unknown";
      conditionFilter: "all" | "no-position" | "stale";
    }> = {},
  ) => ({
    query: "",
    kindFilter: "all" as const,
    conditionFilter: "all" as const,
    ...over,
  });

  it("combina busca + categoria + condição por AND", () => {
    const n = no({ nome: "Barco Itaquaí", kind: "boat" });
    expect(matchesFilters(n, filtros({ query: "itaquai" }), NOW)).toBe(true);
    expect(
      matchesFilters(n, filtros({ query: "itaquai", kindFilter: "boat" }), NOW),
    ).toBe(true);
    expect(
      matchesFilters(
        n,
        filtros({ query: "itaquai", kindFilter: "handheld" }),
        NOW,
      ),
    ).toBe(false);
    expect(
      matchesFilters(n, filtros({ query: "curuca", kindFilter: "boat" }), NOW),
    ).toBe(false);
  });

  it("'all' em categoria inclui desconhecidos; categoria filtra exclusivo", () => {
    const desconhecido = no();
    expect(matchesFilters(desconhecido, filtros(), NOW)).toBe(true);
    expect(
      matchesFilters(desconhecido, filtros({ kindFilter: "boat" }), NOW),
    ).toBe(false);
    expect(
      matchesFilters(desconhecido, filtros({ kindFilter: "unknown" }), NOW),
    ).toBe(true);
  });

  it("condição 'no-position' pega só quem não tem posição confirmada", () => {
    const semFix = no({ posTime: null });
    expect(
      matchesFilters(semFix, filtros({ conditionFilter: "no-position" }), NOW),
    ).toBe(true);
    expect(
      matchesFilters(no(), filtros({ conditionFilter: "no-position" }), NOW),
    ).toBe(false);
  });

  it("condição 'stale' exige posição confirmada E fix antigo", () => {
    const antigo = no({ posTime: horasAtras(13) });
    expect(
      matchesFilters(antigo, filtros({ conditionFilter: "stale" }), NOW),
    ).toBe(true);
    expect(
      matchesFilters(no(), filtros({ conditionFilter: "stale" }), NOW),
    ).toBe(false);
    expect(
      matchesFilters(
        no({ posTime: null }),
        filtros({ conditionFilter: "stale" }),
        NOW,
      ),
    ).toBe(false);
  });
});

describe("fixAgeLabel", () => {
  it("sem timestamp válido é 'sem fix'", () => {
    expect(fixAgeLabel(null, NOW)).toBe("sem fix");
    expect(fixAgeLabel("sem-data", NOW)).toBe("sem fix");
  });

  it("menos de 60 s é 'agora'", () => {
    expect(fixAgeLabel(new Date(NOW - 30_000).toISOString(), NOW)).toBe(
      "agora",
    );
  });

  it("futuro dentro de 5 min é clamp para 'agora'", () => {
    expect(fixAgeLabel(new Date(NOW + 2 * 60_000).toISOString(), NOW)).toBe(
      "agora",
    );
  });

  it("futuro acima de 5 min é 'horário inconsistente'", () => {
    expect(fixAgeLabel(new Date(NOW + 10 * 60_000).toISOString(), NOW)).toBe(
      "horário inconsistente",
    );
  });

  it("escala min/h/d", () => {
    expect(fixAgeLabel(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe(
      "há 5 min",
    );
    expect(fixAgeLabel(new Date(NOW - 3 * 3600_000).toISOString(), NOW)).toBe(
      "há 3 h",
    );
    expect(fixAgeLabel(new Date(NOW - 2 * 86400_000).toISOString(), NOW)).toBe(
      "há 2 d",
    );
  });
});

describe("formatDateTimeJavari", () => {
  it("formata dd/MM/yyyy HH:mm:ss no fuso America/Manaus (UTC-4)", () => {
    // 01:02:03 UTC = 21:02:03 do dia anterior no Javari.
    expect(formatDateTimeJavari("2026-09-29T01:02:03Z")).toBe(
      "28/09/2026 21:02:03",
    );
  });

  it("sem timestamp válido é 'sem fix'", () => {
    expect(formatDateTimeJavari(null)).toBe("sem fix");
    expect(formatDateTimeJavari("ontem")).toBe("sem fix");
  });
});

describe("batteryLevel/batteryLabel", () => {
  it("0 é válido e faixas seguem <20 / 20-49 / >=50", () => {
    expect(batteryLevel(0)).toBe("critical");
    expect(batteryLevel(19)).toBe("critical");
    expect(batteryLevel(20)).toBe("low");
    expect(batteryLevel(49)).toBe("low");
    expect(batteryLevel(50)).toBe("ok");
    expect(batteryLevel(100)).toBe("ok");
  });

  it("null e fora de 0–100 não viram percentual", () => {
    expect(batteryLevel(null)).toBe("none");
    expect(batteryLabel(null)).toBe("bateria —");
    expect(batteryLevel(101)).toBe("out-of-scale");
    expect(batteryLabel(101)).toBe("bateria fora da escala");
    expect(batteryLevel(-1)).toBe("out-of-scale");
  });
});

describe("nodesGeoJson", () => {
  it("inclui só nós com posição confirmada", () => {
    const fc = nodesGeoJson(
      [no({ nodeNum: 1 }), no({ nodeNum: 2, posTime: null })],
      NOW,
    );
    expect(fc.type).toBe("FeatureCollection");
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].geometry).toEqual({
      type: "Point",
      coordinates: [-70.3, -4.5],
    });
  });

  it("leva kind, shortName, isStale e battery para o mapa", () => {
    const fc = nodesGeoJson(
      [no({ kind: "handheld", shortName: "PT1", posTime: horasAtras(13) })],
      NOW,
    );
    expect(fc.features[0].properties).toEqual({
      id: 0xabcd1234,
      nodeNum: 0xabcd1234,
      nome: "Nó A",
      shortName: "PT1",
      kind: "handheld",
      hwModel: "",
      posTime: horasAtras(13),
      isStale: true,
      battery: 87,
      bearing: 0,
    });
  });

  it("sem metadados, usa 'unknown' e shortName vazio", () => {
    const fc = nodesGeoJson([no()], NOW);
    expect(fc.features[0].properties.kind).toBe("unknown");
    expect(fc.features[0].properties.shortName).toBe("");
    expect(fc.features[0].properties.hwModel).toBe("");
    expect(fc.features[0].properties.isStale).toBe(false);
  });

  it("normaliza e propaga bearing para [0, 360)", () => {
    const fc = nodesGeoJson(
      [
        no({ nodeNum: 1, bearing: 245.5 }),
        no({ nodeNum: 2, bearing: -90 }),
        no({ nodeNum: 3, bearing: 360 }),
        no({ nodeNum: 4, bearing: null }),
      ],
      NOW,
    );
    expect(fc.features[0].properties.bearing).toBe(245.5);
    expect(fc.features[1].properties.bearing).toBe(270);
    expect(fc.features[2].properties.bearing).toBe(0);
    expect(fc.features[3].properties.bearing).toBe(0);
  });
});

describe("deviceModelSvgUrl", () => {
  it("mapeia modelos conhecidos para seus respectivos SVGs locais", () => {
    expect(deviceModelSvgUrl("HELTEC_V4")).toBe("/devices/heltec_v4.svg");
    expect(deviceModelSvgUrl("heltec-v3")).toBe("/devices/heltec-v3.svg");
    expect(deviceModelSvgUrl("TBEAM")).toBe("/devices/tbeam.svg");
    expect(deviceModelSvgUrl("TRACKER_T1000_E")).toBe(
      "/devices/tracker-t1000-e.svg",
    );
    expect(deviceModelSvgUrl("RAK_WISMESH_TAG")).toBe(
      "/devices/rak_wismesh_tag.svg",
    );
    expect(deviceModelSvgUrl("RAK4631")).toBe("/devices/rak4631.svg");
    expect(deviceModelSvgUrl("T_ECHO")).toBe("/devices/t-echo.svg");
  });

  it("modelo nulo ou desconhecido cai para unknown.svg", () => {
    expect(deviceModelSvgUrl(null)).toBe("/devices/unknown.svg");
    expect(deviceModelSvgUrl(undefined)).toBe("/devices/unknown.svg");
    expect(deviceModelSvgUrl("")).toBe("/devices/unknown.svg");
    expect(deviceModelSvgUrl("MODELO_INEXISTENTE")).toBe(
      "/devices/unknown.svg",
    );
  });
});

describe("formatAgeFromSeconds", () => {
  it("formata idade a partir de segundos numéricos em português", () => {
    expect(formatAgeFromSeconds(null)).toBeNull();
    expect(formatAgeFromSeconds(undefined)).toBeNull();
    expect(formatAgeFromSeconds(Number.NaN)).toBeNull();
    expect(formatAgeFromSeconds(0)).toBe("agora");
    expect(formatAgeFromSeconds(45)).toBe("agora");
    expect(formatAgeFromSeconds(60)).toBe("há 1 min");
    expect(formatAgeFromSeconds(720)).toBe("há 12 min");
    expect(formatAgeFromSeconds(3599)).toBe("há 59 min");
    expect(formatAgeFromSeconds(3600)).toBe("há 1 h");
    expect(formatAgeFromSeconds(7200)).toBe("há 2 h");
    expect(formatAgeFromSeconds(86400)).toBe("há 1 d");
    expect(formatAgeFromSeconds(172800)).toBe("há 2 d");
  });
});

describe("isAgeWarning", () => {
  it("retorna true se time_flag ou timeFlag estiver presente", () => {
    expect(isAgeWarning({ timeFlag: "invalid_zero" }, NOW)).toBe(true);
    // biome-ignore lint/style/useNamingConvention: compatibilidade da API
    expect(isAgeWarning({ time_flag: "invalid_past" }, NOW)).toBe(true);
  });

  it("retorna true se age_s ou ageS for maior que 1 hora (3600 s)", () => {
    expect(isAgeWarning({ ageS: 3601 }, NOW)).toBe(true);
    // biome-ignore lint/style/useNamingConvention: compatibilidade da API
    expect(isAgeWarning({ age_s: 7200 }, NOW)).toBe(true);
    expect(isAgeWarning({ ageS: 3600 }, NOW)).toBe(false);
    expect(isAgeWarning({ ageS: 720 }, NOW)).toBe(false);
  });

  it("retorna true se o posTime for mais antigo que 1 hora quando age_s não informado", () => {
    expect(isAgeWarning({ posTime: horasAtras(2) }, NOW)).toBe(true);
    expect(isAgeWarning({ posTime: horasAtras(0.5) }, NOW)).toBe(false);
  });
});

describe("nodeAgeLabel", () => {
  it("prioriza ageS/age_s se fornecido", () => {
    expect(nodeAgeLabel({ ageS: 720, posTime: horasAtras(5) }, NOW)).toBe(
      "há 12 min",
    );
    // biome-ignore lint/style/useNamingConvention: compatibilidade da API
    expect(nodeAgeLabel({ age_s: 30, posTime: horasAtras(5) }, NOW)).toBe(
      "agora",
    );
  });

  it("recorre a fixAgeLabel caso ageS/age_s não esteja disponível", () => {
    expect(nodeAgeLabel({ posTime: horasAtras(2) }, NOW)).toBe("há 2 h");
    expect(nodeAgeLabel({ posTime: null }, NOW)).toBe("sem fix");
  });
});
