> [!NOTE]
> **Status:** Proposta arquitetural histórica (Fases 1A e 1B implementadas e consolidadas no commit `5fdfe71`). A referência aos 63 testes reflete o baseline inicial antes da expansão para 132 testes aprovados.

A recomendação é uma evolução incremental: **Fase 1** para localizar e inspecionar a frota; Fase 2 para explorar histórico e telemetria. O [plano técnico completo para o cline](superpowers/plans/2026-09-29-rastro-ui-ux.md) contém arquivos, interfaces, passos de implementação, testes e critérios de aceite.

1. **Avaliação das sugestões**

   Busca, inspector básico e re-enquadramento oferecem o maior benefício imediato: reduzem o esforço para encontrar uma equipe, entender sua última posição e recuperar a orientação no mapa.

   Categorias e frescor precisam de canais visuais distintos. Azul/cyan para barcos, âmbar para bases e esmeralda para portáteis são adequados; idade do fix deve aparecer por contorno/anel e texto. Usar a mesma cor para tipo e idade cria ambiguidade. Nós antigos precisam continuar visíveis.

   “Sem fix / Offline” deve ser separado. Ausência de posição, fix antigo e falha de conexão com a API são condições diferentes. O frontend atual não possui informação suficiente para afirmar que um rádio está offline.

   Para trilhas, presets “6h” e “24h” são mais claros que um slider duplo. “Todas” depende de um contrato de histórico: hoje a API usa 24h por padrão e limita a resposta a 2.000 pontos.

2. **Prioridades por fase**

   | Fase | Entrega |
   |---|---|
   | 1A — imediata | Busca por nome/hex, contador, filtros de condição, inspector básico, indicação de frescor, centralizar, enquadrar todos e sidebar recolhível |
   | 1B — contrato necessário | Busca por nome curto, filtros e cores por categoria, modelo do rádio |
   | 2 | Janela 6h/24h, alternar pontos, contagem, distância estimada e telemetria expandida |
   | 3 | Inventário real sem posição, histórico amplo/paginação, playback, formas por categoria e controles adicionais de camadas |

   Existe uma dependência concreta na Fase 1B: a fixture conhece categoria, nome curto e modelo, mas esses campos não chegam pelo `/latest` atual. Importar a fixture na UI produziria uma funcionalidade incompatível com produção.

   O plano propõe propriedades opcionais no endpoint autenticado, alimentadas por metadados locais minimizados. Sem elas, o viewer permanece operacional com “Tipo não informado”, sem classificar nós por heurísticas de nome.

3. **Arquitetura SolidJS**

   `store.ts` mantém dados, seleção e filtros. Lista filtrada, contagens, nó selecionado e pins são derivações com `createMemo`, sem duplicar arrays no store. Essa divisão acompanha o modelo de [valores derivados do Solid](https://docs.solidjs.com/concepts/derived-values/memos).

   A expansão proposta inclui campos opcionais de identificação e posição, além de `query`, `kindFilter`, `conditionFilter` e `latestStatus`. Campos opcionais preservam os objetos antigos; o parser só acrescenta propriedades presentes no payload, mantendo as asserções exatas dos testes existentes.

   `MapWindow.tsx` compõe `NodeFilters`, `NodeList`, `NodeInspector` e `MapControls`. `InitializeMap.tsx` continua dono da instância MapLibre e separa efeitos de câmera, solicitação de trilha e desenho. Polling, bateria e relógio não devem provocar novos voos ou pedidos de trilha.

   Um relógio compartilhado atualiza a idade mesmo durante perda de conexão. Timers, observers e respostas pendentes têm limpeza no lifecycle, conforme [onCleanup](https://docs.solidjs.com/reference/lifecycle/on-cleanup).

4. **Sequência modular para o cline**

   O documento detalha estas entregas independentes:

   1. Normalizar altitude, satélites e timestamps; implementar regras puras.
   2. Alinhar metadados entre API real e mock, com revisão do mantenedor.
   3. Adicionar filtros, memos e reset de sessão.
   4. Extrair busca, chips e lista.
   5. Implementar inspector básico.
   6. Integrar cores, seleção por pin e proteção contra respostas atrasadas.
   7. Implementar comandos de câmera e layout responsivo.
   8. Executar o gate de testes, navegador e operação offline.
   9. Adicionar janelas de trilha e alternância de pontos.
   10. Acrescentar resumo e distância estimada.
   11. Implementar telemetria sob demanda.

   Tailwind cuida de layout, contraste e responsividade; os wrappers locais Ark/Park fornecem Input, Badge, Card e Button. SegmentGroup e Switch entram nos controles de trilha. Nenhuma dependência nova é necessária.

5. **Integridade e validação**

   Os **63 testes existentes passaram** nesta consulta. O typecheck ficou sem resultado e foi interrompido; build, lint e validação visual não foram executados. Apenas o documento de planejamento foi criado.

   O gate proposto cobre seleção rápida A→B, logout/401 durante requests, remoção de nós, teclado, toque, diferentes telas e ausência de requisições externas. O mock precisa respeitar parâmetros temporais e manter timestamps estáveis: atualmente renova a idade dos fixes a cada resposta.

   Também há uma diferença estrutural: a API real omite nós que nunca tiveram posição; a fixture inclui um “sem fix” com coordenadas. Esse nó deve permanecer na lista, sem pin de posição confirmada. Inventário real sem posição exige contrato próprio.

   A próxima entrega indicada é o módulo de dados e regras puras, seguido da busca e do inspector básico; categorias recebem aceite somente após validação com o contrato real.