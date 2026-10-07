import type { NodeEvent, NodeEventKind } from "../providers/api.js";

export const ROTULO_EVENTO = new Map<NodeEventKind, string>([
  ["pos", "Posição"],
  ["telem", "Telemetria"],
  ["msg", "Mensagem"],
]);

const num = (
  v: number | null | undefined,
  casas: number,
  un = "",
): string | null =>
  v === null || v === undefined ? null : `${v.toFixed(casas)}${un}`;

const int = (v: number | null | undefined, un = ""): string | null =>
  v === null || v === undefined ? null : `${v}${un}`;

/** Uptime em "3d 4h", "2h 05m" ou "45s". */
export function formatUptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) {
    return `${d}d ${h}h`;
  }
  if (h > 0) {
    return `${h}h ${String(m).padStart(2, "0")}m`;
  }
  return m > 0 ? `${m}m` : `${s}s`;
}

/** Rótulo do gateway que ouviu o pacote: nome, senão id hexadecimal. */
export function gatewayLabel(e: NodeEvent): string | null {
  if (e.gateway_num === null || e.gateway_num === undefined) {
    return null;
  }
  return e.gateway_name || `!${e.gateway_num.toString(16).padStart(8, "0")}`;
}

/** Linha principal do registro (resumo de uma linha). */
export function eventHeadline(e: NodeEvent): string {
  switch (e.kind) {
    case "pos":
      return `${(e.lat ?? 0).toFixed(5)}, ${(e.lon ?? 0).toFixed(5)}`;
    case "telem":
      return (
        [num(e.battery_level, 0, "%"), num(e.voltage, 2, " V")]
          .filter((x) => x !== null)
          .join(" · ") || "Telemetria"
      );
    case "msg":
      return e.text ?? "";
  }
}

/** Detalhes secundários (chips): altitude, satélites, SNR/RSSI, saltos, gateway... */
export function eventDetails(e: NodeEvent): string[] {
  const out: (string | null)[] = [];
  if (e.kind === "pos") {
    out.push(
      int(e.altitude_m, " m"),
      int(e.sats_in_view, " sats"),
      num(e.snr, 1) === null ? null : `SNR ${num(e.snr, 1)}`,
      e.rssi === null || e.rssi === undefined ? null : `RSSI ${e.rssi}`,
      e.hop_limit === null || e.hop_limit === undefined
        ? null
        : `${e.hop_limit} saltos`,
    );
    const gw = gatewayLabel(e);
    out.push(gw === null ? null : `via ${gw}`);
  } else if (e.kind === "telem") {
    out.push(
      num(e.channel_util, 1) === null
        ? null
        : `canal ${num(e.channel_util, 1)}%`,
      num(e.air_util_tx, 1) === null ? null : `TX ${num(e.air_util_tx, 1)}%`,
      e.uptime_s === null || e.uptime_s === undefined
        ? null
        : `ligado ${formatUptime(e.uptime_s)}`,
    );
  } else {
    out.push(e.direction === "out" ? "enviada" : "recebida");
  }
  return out.filter((x): x is string => x !== null);
}

/** Chave de dia local ("2026-10-06") para separadores. */
export function dayKey(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

export function formatDay(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("pt-BR", {
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      });
}

export function formatClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString("pt-BR", { hour12: false });
}
