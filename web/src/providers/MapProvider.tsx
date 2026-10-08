import type { Setter } from "solid-js";
import { createContext } from "solid-js";

export const MapContext = createContext<{
  setMapRef: Setter<HTMLDivElement | undefined>;
  initializeMap: () => void;
  /** Enquadra no mapa todos os nós com posição confirmada (ação explícita). */
  fitAllNodes: () => void;
  /** Aproxima um nível de zoom (botão "+"). */
  zoomIn: () => void;
  /** Afasta um nível de zoom (botão "−"). */
  zoomOut: () => void;
  /** Centraliza no nó selecionado (ação explícita de "Centralizar no mapa"). */
  centerOnNode: (nodeNum: number) => void;
  /** Centraliza num ponto qualquer (ex.: fix histórico do registro do nó). */
  centerOnPoint: (lon: number, lat: number) => void;
}>();
