import type { PontoRegua } from "../store.js";
import { calcularBearing } from "./bearing.js";

/** Par [lon, lat] em graus, como no restante do visualizador. */
export type LngLat = [number, number];

const RAIO_TERRA_M = 6_371_008.8;

const rad = (graus: number): number => (graus * Math.PI) / 180;

/** Distância geodésica em metros (haversine, R = 6_371_008.8). */
export function haversineM(a: LngLat, b: LngLat): number {
  const phi1 = rad(a[1]);
  const phi2 = rad(b[1]);
  const dPhi = rad(b[1] - a[1]);
  const dLambda = rad(b[0] - a[0]);

  const h =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;

  return 2 * RAIO_TERRA_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Distância de cada segmento (tamanho n-1). */
export function distanciasSegmentos(pts: readonly LngLat[]): number[] {
  const distancias: number[] = [];
  for (let i = 1; i < pts.length; i++) {
    distancias.push(haversineM(pts[i - 1] as LngLat, pts[i] as LngLat));
  }
  return distancias;
}

/** Soma dos segmentos; 0 com menos de 2 pontos. */
export function distanciaTotalM(pts: readonly LngLat[]): number {
  return distanciasSegmentos(pts).reduce((soma, d) => soma + d, 0);
}

/**
 * "850 m" (< 1000 m, inteiro); "1,2 km" (< 100 km, 1 casa, vírgula);
 * "134 km" (>= 100 km, inteiro). Arredonda antes de escolher a faixa.
 */
export function rotuloDistancia(m: number): string {
  const metros = Math.round(m);
  if (metros < 1000) {
    return `${metros} m`;
  }

  // Décimos de km, arredondados a partir de metros inteiros.
  const decimos = Math.round(metros / 100);
  if (decimos >= 1000) {
    return `${Math.round(metros / 1000)} km`;
  }

  const inteiro = Math.floor(decimos / 10);
  const decimal = decimos % 10;
  return `${inteiro},${decimal} km`;
}

const SETORES_RUMO = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"] as const;

/**
 * Rosa de 8 pontos em PT + graus inteiros: "NE 47°". Setores de 45°
 * centrados em cada ponto. null vira "—".
 */
export function rotuloRumo(graus: number | null): string {
  if (graus === null) {
    return "—";
  }

  const normalizado = ((graus % 360) + 360) % 360;
  const inteiro = Math.round(normalizado) % 360;
  const setor = SETORES_RUMO[Math.floor((inteiro + 22.5) / 45) % 8];

  return `${setor} ${inteiro}°`;
}

/** Rumo inicial de a para b (reusa calcularBearing). */
export function rumo(a: LngLat, b: LngLat): number | null {
  return calcularBearing(a[0], a[1], b[0], b[1]);
}

/** Abaixo disso o "deslocamento" é ruído de multipath (AGENTS.md, lição 9). */
export const VELOCIDADE_MIN_ETA_KMH = 2;

/**
 * Tempo em ms para `distanciaM` a `velocidadeKmh`. null se a velocidade for
 * null, não finita ou menor que VELOCIDADE_MIN_ETA_KMH.
 */
export function etaMs(
  distanciaM: number,
  velocidadeKmh: number | null,
): number | null {
  if (
    velocidadeKmh === null ||
    !Number.isFinite(velocidadeKmh) ||
    velocidadeKmh < VELOCIDADE_MIN_ETA_KMH
  ) {
    return null;
  }

  const metrosPorSegundo = velocidadeKmh / 3.6;
  return (distanciaM / metrosPorSegundo) * 1000;
}

/**
 * Texto para a área de transferência: uma linha por vértice
 * "1. -5.00000, -70.00000 (Nome do nó)", depois "Total: 1,2 km".
 */
export function textoCompartilhar(
  pts: readonly { pos: LngLat; nome?: string | null }[],
): string {
  const linhas = pts.map((ponto, i) => {
    const [lon, lat] = ponto.pos;
    const base = `${i + 1}. ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    return ponto.nome ? `${base} (${ponto.nome})` : base;
  });

  const total = distanciaTotalM(pts.map((ponto) => ponto.pos));
  linhas.push(`Total: ${rotuloDistancia(total)}`);

  return linhas.join("\n");
}

/** Vértice da régua já com coordenadas (nós lidos ao vivo do store). */
export interface VerticeResolvido {
  pos: LngLat;
  nome: string | null;
  nodeNum: number | null;
  /** Índice no array `pontos` original (o arrasto precisa dele). */
  indice: number;
}

/**
 * Resolve vértices em coordenadas. Vértice de nó lê `nodes`; nó ausente ou com
 * lon/lat não finitos é descartado.
 */
export function resolverPontosRegua(
  pontos: readonly PontoRegua[],
  nodes: Readonly<Record<number, { lon: number; lat: number; nome: string }>>,
): VerticeResolvido[] {
  const resolvidos: VerticeResolvido[] = [];
  pontos.forEach((ponto, indice) => {
    if (ponto.tipo === "livre") {
      resolvidos.push({
        pos: [ponto.lon, ponto.lat],
        nome: null,
        nodeNum: null,
        indice,
      });
      return;
    }
    const no = nodes[ponto.nodeNum];
    if (!(no && Number.isFinite(no.lon) && Number.isFinite(no.lat))) {
      return;
    }
    resolvidos.push({
      pos: [no.lon, no.lat],
      nome: no.nome,
      nodeNum: ponto.nodeNum,
      indice,
    });
  });
  return resolvidos;
}
