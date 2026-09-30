Você é o arquiteto sênior consultado para planejar duas melhorias fundamentais no visualizador tático **Rastro** (`web/`):

1. **Orientação do Barco no Mapa pelo Rumo (Heading/Bearing)**:
   - No mapa MapLibre GL, o ícone de barco deve apontar na direção correta do movimento baseado no caminho/trilha que ele está seguindo.
   - Atualmente, os barcos têm o layer `nodes-boat` (tipo `symbol` com `icon-image: "boat-icon"`).
   - Precisamos calcular o rumo/azimute (bearing de 0° a 360°, onde 0°/360° é Norte, 90° Leste, 180° Sul, 270° Oeste) a partir dos últimos pontos de trilha/trajetória do nó (`getTrackGeoJson` ou `track.line` / coordenadas consecutivas `[lon, lat]`).
   - Passar essa propriedade `bearing` no GeoJSON dos nós (`nodesGeoJson` em `src/lib/nodes.ts`).
   - Configurar a camada MapLibre com `icon-rotate: ["get", "bearing"]` e `icon-rotation-alignment: "map"`.
   - Se um nó não tiver movimento anterior ou pontos suficientes, definir fallback elegante (ex: 0° ou manter o último rumo conhecido).

2. **Melhoria do SVG do Barco com base na Imagem Real da Univaja**:
   - Foi fornecida como anexo a foto aérea real das embarcações da Equipe de Vigilância da Univaja (EVU) em operação no Vale do Javari (`boat_reference.webp`).
   - Na foto, vê-se a clássica embarcação regional de patrulha:
     - Casco alongado de barco regional amazônico (proa suave, borda resistente, pneu de proteção/fender na ponta da proa).
     - Convés superior/cabine de comando com teto onde a equipe faz vigília.
     - Lona azul característica cobrindo suprimentos/equipamentos no centro/ré do convés superior.
     - Linhas limpas e contrastantes em vista superior (top-down), otimizadas para visualização tática no mapa em escala reduzida (24px a 48px).
   - O SVG precisa apontar estritamente para o **NORTE** (proa voltada para cima no eixo Y, 0° de rotação) para que a rotação do MapLibre funcione perfeitamente.

### O que você deve entregar:
1. **Plano de Implementação Arquitetural**:
   - Como e onde calcular o bearing (função matemática pura de azimute geodésico `calcularBearing(lon1, lat1, lon2, lat2)`).
   - Como integrar na store / fixtures / `nodesGeoJson` / `InitializeMap.tsx` sem degradar performance nem quebrar os 105 testes existentes.
   - Como configurar o layer MapLibre (`icon-rotate`, `icon-rotation-alignment`, etc.).
2. **Código Completo do SVG Aperfeiçoado**:
   - O código XML `<svg ...>...</svg>` completo, pronto para salvar em `web/public/devices/boat.svg`.
   - O SVG deve incorporar os elementos distintivos do barco real da Univaja (proa com pneu/fender, convés, cabine, lona azul no teto, motor/leme na popa), com alto contraste sobre o mapa e viewBox adequado (ex: `0 0 64 64` ou `0 0 100 100`).
3. **Casos de Teste Unitários**:
   - Testes para o cálculo de bearing (Norte 0°, Leste 90°, Sul 180°, Oeste 270°, coordenadas idênticas, etc.).
