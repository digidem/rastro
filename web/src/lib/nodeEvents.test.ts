import { describe, expect, it } from "vitest";
import type { NodeEvent } from "../providers/api.js";
import {
  eventDetails,
  eventHeadline,
  formatUptime,
  gatewayLabel,
} from "./nodeEvents.js";

const pos: NodeEvent = {
  kind: "pos",
  id: 1,
  ts: "2026-10-01T12:00:00Z",
  lat: -3.1,
  lon: -60.02,
  altitude_m: 40,
  sats_in_view: 9,
  snr: 6.5,
  rssi: -91,
  hop_limit: 3,
  gateway_num: 255,
  gateway_name: null,
};

describe("nodeEvents", () => {
  it("formata uptime", () => {
    expect(formatUptime(45)).toBe("45s");
    expect(formatUptime(3900)).toBe("1h 05m");
    expect(formatUptime(90000)).toBe("1d 1h");
  });

  it("rótulo do gateway: nome ou hex", () => {
    expect(gatewayLabel({ ...pos, gateway_name: "Base Ituí" })).toBe(
      "Base Ituí",
    );
    expect(gatewayLabel(pos)).toBe("!000000ff");
    expect(gatewayLabel({ ...pos, gateway_num: null })).toBeNull();
  });

  it("posição: headline e detalhes com gateway", () => {
    expect(eventHeadline(pos)).toBe("-3.10000, -60.02000");
    expect(eventDetails(pos)).toEqual([
      "40 m",
      "9 sats",
      "SNR 6.5",
      "RSSI -91",
      "3 saltos",
      "via !000000ff",
    ]);
  });

  it("telemetria e mensagem", () => {
    const t: NodeEvent = {
      kind: "telem",
      id: 2,
      ts: "x",
      battery_level: 87,
      voltage: 4.1,
      channel_util: 2,
      uptime_s: 3900,
    };
    expect(eventHeadline(t)).toBe("87% · 4.10 V");
    expect(eventDetails(t)).toEqual(["canal 2.0%", "ligado 1h 05m"]);
    const m: NodeEvent = {
      kind: "msg",
      id: 3,
      ts: "x",
      text: "oi",
      direction: "in",
    };
    expect(eventHeadline(m)).toBe("oi");
    expect(eventDetails(m)).toEqual(["recebida"]);
  });
});
