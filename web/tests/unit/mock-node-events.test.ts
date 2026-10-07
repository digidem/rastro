import { describe, expect, it } from "vitest";
import { FLEET_NODES, getNodeEvents } from "../../src/fixtures/nodesFixture.js";

const boat = FLEET_NODES.find((n) => n.kind === "boat" && n.telemetry);

describe("getNodeEvents (mock)", () => {
  it("pagina por cursor sem repetir nem pular registros", () => {
    expect(boat).toBeTruthy();
    const id = boat?.nodeId ?? "";
    const seen: string[] = [];
    let before: string | null = null;
    for (let i = 0; i < 100; i++) {
      const page = getNodeEvents(id, { before, limit: 50 });
      for (const e of page.events) {
        seen.push(`${e.kind}${e.id}`);
      }
      if (page.next_cursor === null) {
        break;
      }
      before = page.next_cursor;
    }
    expect(new Set(seen).size).toBe(seen.length);
    const total = getNodeEvents(id, { limit: 200 }).events.length;
    expect(seen.length).toBeGreaterThanOrEqual(total);
  });

  it("filtra por tipo e ordena do mais novo ao mais antigo", () => {
    const { events } = getNodeEvents(boat?.nodeId ?? "", {
      kinds: ["pos"],
      limit: 30,
    });
    expect(events.every((e) => e.kind === "pos")).toBe(true);
    const ts = events.map((e) => Date.parse(e.ts));
    expect([...ts].sort((a, b) => b - a)).toEqual(ts);
  });

  it("nó desconhecido devolve vazio", () => {
    expect(getNodeEvents("!deadbeef")).toEqual({
      events: [],
      next_cursor: null,
    });
  });
});
