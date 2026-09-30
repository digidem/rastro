import type { Setter } from "solid-js";
import { createContext } from "solid-js";

export const MapContext = createContext<{
  setMapRef: Setter<HTMLDivElement | undefined>;
  initializeMap: () => void;
  /** Enquadra no mapa todos os nós com posição confirmada (ação explícita). */
  fitAllNodes: () => void;
  /** Centraliza no nó selecionado (ação explícita de "Centralizar no mapa"). */
  centerOnNode: (nodeNum: number) => void;
}>();
