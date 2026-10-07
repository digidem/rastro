type Coordenada = readonly [number, number] | readonly number[];

const rad = (graus: number): number => (graus * Math.PI) / 180;

function coordenadaValida(lon: number, lat: number): boolean {
  return (
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    Math.abs(lon) <= 180 &&
    Math.abs(lat) <= 90
  );
}

/**
 * Azimute inicial geodésico sobre uma esfera, em [0, 360).
 * null significa direção indefinida ou coordenadas inválidas.
 */
export function calcularBearing(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number,
): number | null {
  if (!(coordenadaValida(lon1, lat1) && coordenadaValida(lon2, lat2))) {
    return null;
  }

  const phi1 = rad(lat1);
  const phi2 = rad(lat2);
  const deltaLambda = rad(lon2 - lon1);

  const y = Math.sin(deltaLambda) * Math.cos(phi2);
  const x =
    Math.cos(phi1) * Math.sin(phi2) -
    Math.sin(phi1) * Math.cos(phi2) * Math.cos(deltaLambda);

  // Coincidentes, antípodas ou outro caso numericamente indefinido.
  if (Math.hypot(x, y) < 1e-12) {
    return null;
  }

  const graus = (Math.atan2(y, x) * 180) / Math.PI;
  return ((graus % 360) + 360) % 360;
}

/**
 * Coordenadas em ordem cronológica: antiga → recente.
 * Procura o último segmento com direção definida.
 * Um ponto inválido interrompe a continuidade da trilha.
 */
export function bearingDaTrilha(
  coordenadas: readonly Coordenada[],
): number | null {
  for (let i = coordenadas.length - 1; i > 0; i--) {
    const anterior = coordenadas[i - 1];
    const atual = coordenadas[i];

    if (
      !(anterior && atual) ||
      anterior.length < 2 ||
      atual.length < 2 ||
      !coordenadaValida(anterior[0], anterior[1]) ||
      !coordenadaValida(atual[0], atual[1])
    ) {
      return null;
    }

    const bearing = calcularBearing(
      anterior[0],
      anterior[1],
      atual[0],
      atual[1],
    );
    if (bearing !== null) {
      return bearing;
    }
  }

  return null;
}

/**
 * Rumo a partir da trilha simplificada (ST-DAH). Parado: congela no último
 * rumo de aproximação (null se não houve). Navegando: último segmento
 * da linha sem o novelo de GPS, para o ícone não girar a cada fix.
 */
export function bearingComParada(t: {
  parado: boolean;
  aproximacao: readonly Coordenada[] | null;
  linhas: readonly (readonly Coordenada[])[];
}): number | null {
  if (t.parado) {
    return t.aproximacao ? bearingDaTrilha(t.aproximacao) : null;
  }
  const ultima = t.linhas[t.linhas.length - 1];
  return ultima && ultima.length >= 2 ? bearingDaTrilha(ultima) : null;
}
