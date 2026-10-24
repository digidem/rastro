import type { LeituraAmbiente } from "../store.js";

/** Leitura com mais de 1 h é marcada como desatualizada. */
export const AMBIENTE_VELHO_MS = 3_600_000;

const nf = (casas: number) =>
  new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: casas,
    maximumFractionDigits: casas,
  });
const um = nf(1);
const zero = nf(0);

/** "27,3 °C · umidade 82 % · 1.009 hPa" (agrupamento pt-BR); medidas ausentes ficam de fora; sem nenhuma → "—". */
export function formatarAmbiente(l: LeituraAmbiente): string {
  const partes: string[] = [];
  if (l.temperaturaC !== null) {
    partes.push(`${um.format(l.temperaturaC)} °C`);
  }
  if (l.umidade !== null) {
    partes.push(`umidade ${zero.format(l.umidade)} %`);
  }
  if (l.pressaoHpa !== null) {
    partes.push(`${zero.format(l.pressaoHpa)} hPa`);
  }
  return partes.length > 0 ? partes.join(" · ") : "—";
}

/** Idade da leitura contra o relógio do navegador (não o `age_s` do servidor, que envelhece entre polls). */
export function idadeAmbienteMs(l: LeituraAmbiente, nowMs: number): number {
  const t = Date.parse(l.telemTime);
  return Number.isFinite(t) ? Math.max(0, nowMs - t) : 0;
}

/** "agora", "há 5 min", "há 3 h", "há 2 d". */
export function rotuloIdadeAmbiente(l: LeituraAmbiente, nowMs: number): string {
  const min = Math.floor(idadeAmbienteMs(l, nowMs) / 60_000);
  if (min < 1) {
    return "agora";
  }
  if (min < 60) {
    return `há ${min} min`;
  }
  const h = Math.floor(min / 60);
  return h < 48 ? `há ${h} h` : `há ${Math.floor(h / 24)} d`;
}

export function ambienteDesatualizado(
  l: LeituraAmbiente,
  nowMs: number,
): boolean {
  return idadeAmbienteMs(l, nowMs) > AMBIENTE_VELHO_MS;
}
