/**
 * Idade da última leitura nos badges de alerta (sugestão da consulta Opus
 * 5.5): transformar badge "preso" em item de fila de manutenção com idade
 * visível — sem mudar a semântica last-known do motor.
 */
import type { NodeAlert } from "../store.js";
import { duracaoLabel } from "./dwell.js";

/** Idade do ``details.last_seen`` (ms); null sem timestamp válido. */
export function idadeUltimaLeituraMs(
  alerta: NodeAlert,
  nowMs: number,
): number | null {
  const bruto = alerta.details?.last_seen;
  if (typeof bruto !== "string") {
    return null;
  }
  const ts = Date.parse(bruto);
  if (Number.isNaN(ts)) {
    return null;
  }
  return Math.max(0, nowMs - ts);
}

/** Sufixo do badge: " · leitura há 8h 0m"; vazio sem last_seen. */
export function sufixoLeitura(alerta: NodeAlert, nowMs: number): string {
  const idade = idadeUltimaLeituraMs(alerta, nowMs);
  return idade === null ? "" : ` · leitura há ${duracaoLabel(idade)}`;
}

/** Title do badge 📡: idade do último uplink conhecido. */
export function tituloGatewayMudo(alerta: NodeAlert, nowMs: number): string {
  const idade = idadeUltimaLeituraMs(alerta, nowMs);
  return idade === null
    ? "Sem uplink há mais de 1 hora"
    : `Sem uplink há mais de 1 hora (último há ${duracaoLabel(idade)})`;
}

/** Title do badge 🪫: idade da última leitura de energia. */
export function tituloBateriaCritica(alerta: NodeAlert, nowMs: number): string {
  const idade = idadeUltimaLeituraMs(alerta, nowMs);
  return idade === null
    ? "Bateria crítica (repetidor/base solar)"
    : `Bateria crítica (repetidor/base solar) · última leitura há ${duracaoLabel(idade)}`;
}
