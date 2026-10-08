/**
 * Fixture de alertas ativos (GET /api/alerts) para o modo dev:test.
 * Coordenadas/nomes fictícios derivados de FLEET_NODES (nada territorial).
 */
import { FLEET_NODES } from "./nodesFixture.js";

/** Forma crua idêntica ao contrato do plano (docs/PLANO-ALERTAS-E-RETENCAO.md §2.3). */
export interface MockAlert {
  alert_id: string;
  node_num: number;
  node_id: string;
  node_name: string;
  alert_type: "gateway_mudo" | "bateria_critica";
  severity: "critical" | "high";
  triggered_at: string;
  details: Record<string, unknown>;
}

const MIN = 60 * 1000;
const HORA = 60 * MIN;

export function getAlerts(now = new Date()): MockAlert[] {
  const barco = FLEET_NODES.find((n) => n.nodeId === "!a35ae5d0");
  const base = FLEET_NODES.find((n) => n.kind === "fixed_station");
  const alertas: MockAlert[] = [];
  if (barco) {
    alertas.push({
      alert_id: `gateway_mudo:${barco.nodeNum}`,
      node_num: barco.nodeNum,
      node_id: barco.nodeId,
      node_name: barco.nome,
      alert_type: "gateway_mudo",
      severity: "critical",
      triggered_at: new Date(now.getTime() - 2 * HORA).toISOString(),
      details: { last_seen: new Date(now.getTime() - 2 * HORA).toISOString() },
    });
  }
  if (base) {
    alertas.push({
      alert_id: `bateria_critica:${base.nodeNum}`,
      node_num: base.nodeNum,
      node_id: base.nodeId,
      node_name: base.nome,
      alert_type: "bateria_critica",
      severity: "high",
      triggered_at: new Date(now.getTime() - 40 * MIN).toISOString(),
      details: {
        battery_level: 15,
        voltage: 3.4,
        last_seen: new Date(now.getTime() - 8 * HORA).toISOString(),
      },
    });
  }
  return alertas;
}
