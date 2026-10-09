# TASK — Trilha sem "novelo" de GPS em barco parado

Branch `feat/parada-jitter` (worktree `../rastro-parada-jitter`). Cada tarefa abaixo é
implementada por um subagente, revisada e commitada antes da próxima.

## Problema

Barco ancorado sob dossel gera multipath: o mapa vira uma estrela de spikes e a
parada se fragmenta em várias "Parada de 22m". Causas medidas numa trilha real
(39 h, 2000 fixes, dados NÃO versionados):

1. `web/src/lib/dwell.ts` encerra a parada com 2 fixes seguidos fora de 50 m, ou com
   1 fix a >100 m e ≥4 km/h. No barco parado, 23% dos fixes caem fora de 50 m, em
   sequências de 2 a 6; um salto de 100 m em 30 s "vale" 12 km/h. Resultado: 11
   paradas fragmentadas e 1579 de 2000 fixes viram vértices da linha; odômetro 122 km.
2. A camada `track-points` (`web/src/InitializeMap.tsx`, ~linha 1105) desenha TODOS os
   fixes crus, mesmo com a linha simplificada.
3. `sats_in_view` é sempre 0 nos dados atuais; não há HDOP/PDOP no banco. Nenhum
   campo de qualidade está disponível para filtrar hoje.

Estatística da parada real de 15 h (521 fixes, distância à mediana): p50 23 m,
p75 39 m, p90 74–110 m (cauda inclui chegada/partida). Ruído branco: distância entre
fixes consecutivos (40 m) ≈ entre pares aleatórios (41 m). Intervalo típico 30 s,
p90 120 s.

Protótipo do algoritmo novo (abaixo) na mesma trilha: a parada de 15 h vira UMA
parada (p90 59 m, p50 20 m); a parada esparsa de 4 h (fix a cada ~4 min) é
preservada (p90 35 m).

## Decisões do dono (2026-10-09)

- Fixes crus dentro da parada: ocultos por padrão; botão "Fixes brutos" no inspector
  mostra todos, esmaecidos e sem linha.
- Parada mostra marcador único + círculo translúcido de dispersão (p90).
- Fase final inclui guardar HDOP/PDOP/velocidade do firmware (migração aditiva),
  sem deploy.

## Regras para todos os subagentes

- Não comitar dados reais nem coordenadas reais. Testes usam dados sintéticos com
  gerador determinístico (PRNG com semente), coordenadas fictícias.
- Funções de algoritmo são puras, sem DOM/mapa, em `web/src/lib/`.
- Comentários e nomes em português, no estilo dos arquivos vizinhos.
- Gates de cada tarefa web: `cd web && pnpm test` e `cd web && pnpm biome check`.
  `pnpm typecheck` pode não terminar nesta máquina; rode com `timeout 400` e relate
  o resultado, sem tratá-lo como bloqueante se estourar o tempo.
- Gates Python: `cd services/rastro_gateway && .venv/bin/pytest -q` e os testes de
  `services/rastro_api`.
- Não alterar nada do contrato congelado (AGENTS.md §5). Esquema: só
  `ADD COLUMN IF NOT EXISTS` anulável.
- Não comitar; o revisor commita.

## Tarefas

### T1 — Gerador sintético de trilha + filtro de spike

Arquivos: `web/tests/unit/fixtures/trilhaSintetica.ts` (novo),
`web/src/lib/gpsSpike.ts` (novo), `web/tests/unit/gps-spike.test.ts` (novo).

1. Gerador determinístico (`mulberry32(seed)`), saída `FixDwell[]` (tipo de `dwell.ts`):
   - `parada({ centro, inicioMs, duracaoMs, intervaloS = 30 })`: ruído branco em
     metros: 80% gaussiano σ 15 m, 15% gaussiano σ 50 m, 5% spikes de 100–300 m;
     além disso, 3 sequências de 2–6 fixes seguidos a 60–120 m em direção aleatória.
   - `navegacao({ de, para, inicioMs, kmh, intervaloS = 30, ruidoM = 10 })`: linha reta.
   - `deriva({ de, rumoGraus, kmh, inicioMs, duracaoMs })`: deslocamento lento (2–3 km/h)
     com o mesmo ruído da parada.
   - `concat(...)` e `comLacuna(trilha, aposMs, lacunaMs)`.
   Use projeção local metros↔graus (mesma de `distanciaM`). Centro fictício
   (ex.: `[-70.0, -5.0]`).
2. `marcarSpikes(fixes: FixDwell[], o?): boolean[]` — teste A–B–C, avaliado UMA vez
   sobre os triplos originais (sem passes repetidos):
   ```text
   para cada B com vizinhos A e C, ambos com horário válido:
     exige t(B)-t(A) <= 120 s e t(C)-t(B) <= 120 s
     Bhat = interpolação linear de A→C no tempo de B
     residual = dist(B, Bhat)
     excesso  = dist(A,B) + dist(B,C) - dist(A,C)
     spike se residual > 100 m E excesso > 150 m E dist(A,C) <= 100 m
   ```
   Opções com esses defaults (`SPIKE_PADRAO`). Primeiro e último fix nunca são spike.
   Velocidade sozinha NUNCA marca spike.
3. Testes: spike isolado numa parada é marcado; partida rápida real (A→B→C seguindo
   em frente a 40 km/h) não é marcada; ida-e-volta com A–C > 100 m não é marcada;
   lacuna > 120 s entre vizinhos desliga o teste; gerador é determinístico (mesma
   semente ⇒ mesma saída).

### T2 — Detecção de parada robusta (reescrita de `processarClusters`)

Arquivos: `web/src/lib/dwell.ts`, `web/tests/unit/dwell.test.ts`.

Mantenha a API pública `simplifyTrackDwells(pontos, opcoes)` e os campos atuais de
`TrilhaSimplificada`/`Dwell` (consumidos por `InitializeMap.tsx` e `bearing.ts`).
Novas opções em `DWELL_PADRAO` (substituem `raioM`, `kSaida`, `saidaImediataM`,
`velNavMinKmh`, que devem sair):

| Opção | Default | Papel |
|---|---:|---|
| `raioEntradaM` | 100 | ocupância para entrar em parada |
| `raioSaidaM` | 150 | fora disso o fix conta como evidência de saída |
| `minDuracaoMs` | 10 min | janela candidata mínima |
| `minFixes` | 4 | fixes mínimos na janela (cadência esparsa ~4 min existe) |
| `ocupanciaMin` | 0.8 | fração da janela dentro de `raioEntradaM` |
| `derivaMaxM` | 75 | veto de tendência (deriva lenta não é parada) |
| `saidaK` / `saidaN` | 5 / 6 | 5 dos últimos 6 fixes fora de `raioSaidaM` |
| `saidaSpanMinMs` | 2 min | a evidência de saída cobre ao menos isso |
| `mesclarDistM` | 100 | mescla paradas vizinhas |
| `mesclarIntervaloMs` | 5 min | intervalo máximo entre paradas mescladas |
| `gapMs` | 30 min | quebra de segmento (igual hoje) |

Algoritmo (fixes marcados por `marcarSpikes` ficam FORA de tudo isto, mas são
contados em `Dwell.excluidos`):

```text
centro(pontos) = mediana por eixo em metros locais (não média)
i = 0
enquanto i < n:
  janela = fixes de i até cobrir minDuracaoMs (para em lacuna > gapMs)
  se janela.dur >= minDuracaoMs e len >= minFixes
     e ocupância(janela, centro(janela), raioEntradaM) >= ocupanciaMin
     e dist(centro(primeiros 2 min), centro(últimos 2 min)) <= derivaMaxM:
       PARADA: membros = janela; c = centro(membros)
       para cada fix seguinte k:
         lacuna > gapMs ⇒ encerra (parada termina no último membro)
         se dist(k, c) <= raioSaidaM: membros += k
         últimos saidaN fixes: se >= saidaK fora de raioSaidaM
            e span(primeiro..último fora) >= saidaSpanMinMs:
              partida = primeiro fix fora dessa sequência; encerra
         recalcula c a cada 20 membros (c fica fixo durante a avaliação de saída)
       registra Dwell; i = índice da partida
  senão: fix i é movimento; i++
depois: mescla dwells consecutivos com centros <= mesclarDistM e intervalo
<= mesclarIntervaloMs, sem lacuna > gapMs entre eles; recalcula estatísticas.
```

Novos campos em `Dwell`: `dispersaoP50M`, `dispersaoP90M` (distância radial dos
membros ao centro; exclui spikes e fixes de partida), `excluidos` (spikes dentro do
intervalo da parada), `ultimoFixMs` (último membro). Novo campo em
`TrilhaSimplificada`: `spikes: boolean[]` alinhado com `pontos` de entrada (índice
original), para a camada de fixes brutos.

Linha simplificada: parada contribui UM vértice (o centro), sem o vértice de
"chegada" (que vira raio da estrela). Odômetro não soma nada dentro da parada.

Testes (gerador da T1): parada sintética de 15 h ⇒ exatamente 1 dwell e linha com
≤ 3 vértices nela; parada de 4 h com fix a cada 4 min ⇒ 1 dwell; deriva a 2.5 km/h
por 1 h ⇒ 0 dwells; pausa de 5 min ⇒ 0 dwells; partida logo após sequência de
spikes ⇒ partida detectada até 3 min depois do início real; parada até o último fix
⇒ `parado = true` e `partidaMs = null`; parada atravessando lacuna de 40 min ⇒ dois
dwells; 2–3 fixes ⇒ sem dwell. Reescreva os testes antigos que dependiam de
`kSaida`/`saidaImediataM`; mantenha os que ainda valem.

### T3 — Linha em movimento sem zigue-zague + odômetro

Arquivos: `web/src/lib/suavizar.ts` (novo), `web/tests/unit/suavizar.test.ts` (novo),
`web/src/lib/dwell.ts` (só a montagem de `linhas`/`distanciaM`).

1. `suavizarLocal(itens, janelaMs = 60_000, minFixes = 5)`: para cada fix em movimento,
   ajuste linear robusto (posição × tempo, por eixo, em metros) sobre os vizinhos em
   ±`janelaMs`; usa o valor ajustado no tempo do fix. Menos de `minFixes` na janela ⇒
   mantém o fix. Não atravessa lacuna > `gapMs` nem a fronteira de uma parada (o
   centro da parada é âncora fixa). "Robusto" = um passe de mínimos quadrados, descarta
   resíduos > 3× a mediana dos resíduos, reajusta uma vez.
2. `douglasPeucker(linha, toleranciaM = 30)`: em metros locais; preserva o primeiro e o
   último vértice de cada segmento e os centros de parada.
3. `distanciaM` (odômetro) = soma sobre a linha suavizada ANTES do Douglas–Peucker.
   `linhas` = linha suavizada DEPOIS do Douglas–Peucker.
4. `aproximacao` (rumo congelado) continua vindo dos vértices até o centro da parada
   em curso.
5. Testes: navegação reta com ruído de 10 m ⇒ odômetro dentro de ±5% da distância real
   e linha com ≤ 5% dos vértices originais; curva de 90° preservada (vértice a < 40 m
   do canto); deriva lenta continua visível (deslocamento final > 80% do real);
   lacuna > 30 min mantém dois segmentos.

Kalman/RTS fica fora (custo de calibragem; ganho pequeno sobre isto).

### T4 — Mapa: parada com círculo de dispersão, sem nuvem crua

Arquivos: `web/src/InitializeMap.tsx`, `web/tests/unit/initialize-map.test.ts`,
`web/src/lib/circulo.ts` (novo, puro, com teste).

1. `circuloGeo(centro, raioM, passos = 48): LngLat[]` — polígono fechado em graus.
2. Nova fonte `dwell-spread` (Polygon por parada, raio = `dispersaoP90M`) e camada
   `dwell-spread-fill` (fill `#0ea5e9`, opacidade 0.15) + `dwell-spread-line`
   (linha 1 px, opacidade 0.5), ambas ABAIXO de `track-line`.
3. `track-points` deixa de receber todos os fixes. Por padrão recebe só os fixes em
   movimento (fora de qualquer parada e não spike). Precisa de um jeito de saber,
   por índice original, se o fix é membro de parada: adicione em
   `TrilhaSimplificada` um `papel: ("movimento" | "parada" | "spike")[]` alinhado com a
   entrada (substitui ou complementa `spikes` da T2).
4. `analisarTrilha`: cair nas linhas cruas da API SÓ quando `simp === null`. Se `simp`
   existe e tem 0 linhas, a trilha fica vazia (não ressuscita o novelo).
5. Rótulo da parada em curso: usar `ultimoFixMs`. Se o último fix tem menos de 30 min:
   `Ancorado há <duração até agora>`; senão `Parado <duração observada> · último fix
   há <tempo>` (não inventa permanência sem observação).
6. Popup da parada (clique em `dwell-points-circle`): chegada, duração, nº de fixes,
   `50% dos fixes em X m · 90% em Y m`, e `N fixes descartados (ruído)` quando
   `excluidos > 0`. Escape HTML como hoje (`esc`).
7. Limpar as novas fontes onde as atuais são limpas (`limparParadas`, logout, troca de
   seleção).
8. Testes no `initialize-map.test.ts` cobrindo: fonte `track-points` sem fixes de
   parada por padrão; `dwell-spread` com um polígono por parada; fallback cru só sem
   `simp`.

### T5 — Botão "Fixes brutos"

Arquivos: `web/src/store.ts`, `web/src/components/viewer/NodeInspector.tsx`,
`web/src/InitializeMap.tsx`, testes do inspector e do mapa.

1. Estado `mostrarFixesBrutos: boolean` (padrão `false`) + setter, no padrão de
   `showInactive`. Persistir em `localStorage` com try/catch (é conveniência local).
2. Toggle no `NodeInspector` (componente `switch` de `components/ui`), rótulo
   "Fixes brutos", visível só com trilha carregada.
3. Ligado: `track-points` recebe TODOS os fixes com a propriedade `papel`; paint por
   expressão: movimento = como hoje; parada = raio 2, opacidade 0.35; spike = raio 2,
   cor `#ef4444`, sem preenchimento (só contorno). Nenhuma linha liga esses pontos.
   Desligado: comportamento da T4.
4. Alternar o toggle NÃO refaz o fetch da trilha: guarde a última análise e só
   reaplique o `setData`.

### T6 — Guardar qualidade do fix do firmware (sem deploy)

Objetivo: quando o rádio mandar, guardar `PDOP`, `HDOP`, `ground_speed`,
`ground_track` e `precision_bits` de `meshtastic.Position`, e expor na trilha. Hoje os
rádios não mandam (sats = 0 sempre): os campos só terão valor depois de ajustar
`position.position_flags` no provisionamento (`../univaja-lora`), FORA desta tarefa.

Arquivos (encontre todos os pontos com `grep -rn sats services deploy` excluindo
`.venv`): `deploy/postgres/init/01-schema.sql`, `deploy/postgres/migrate-02-native.sh`
(ou novo `migrate-03-qualidade.sh` no mesmo estilo), `services/rastro_gateway/native/model.py`,
`native/envelope.py`, `common/records.py`, `bridge/packet_filter.py`,
`bridge/geojson_in.py`, `ingest/db.py`, `services/rastro_api/api/queries.py`,
`api/geojson.py`, `web/src/providers/api.ts` (`TrackPoint`), testes de cada pacote.

1. Esquema: `ALTER TABLE rastro.positions ADD COLUMN IF NOT EXISTS pdop REAL,
   hdop REAL, ground_speed_ms REAL, ground_track_deg REAL, precision_bits SMALLINT`
   — todos anuláveis, sem default, sem índice. Mesmo bloco no `01-schema.sql` (instalação
   nova) e num script de migração idempotente com backup como o `migrate-02`.
   Nenhum `DROP`/`RENAME`/mudança de tipo. Views que listam colunas de `positions`
   só ganham colunas novas no FIM.
2. Decodificação: `pdop`/`hdop` vêm em centésimos (`PDOP / 100`); `ground_speed` em km/h inteiro ⇒ m/s = `/ 3.6`;
   `ground_track` em 1e-5 graus (`/ 1e5`). Valor 0 = ausente ⇒ `None`
   (o protobuf não distingue). Campo ausente nunca derruba o fix.
3. Ingest: INSERT com as colunas novas nos DOIS pontos de `db.py` (~linhas 325 e 625).
   O ingest tem de continuar funcionando contra um banco SEM as colunas (deploy em
   ordem errada): detecte as colunas uma vez na conexão
   (`information_schema.columns`) e só inclua as que existem.
4. API: `track` devolve `pdop`, `hdop`, `speed_ms`, `track_deg` nas propriedades do
   ponto quando não nulos. Web: `TrackPoint` ganha os campos opcionais.
5. Web (uso opcional): `marcarSpikes` aceita `hdop`; se presente e > 5, o limite de
   resíduo cai para 60 m. Sem `hdop`, comportamento idêntico à T1.
6. Testes: decodificação com e sem os campos; ingest contra esquema sem as colunas;
   API serializa os campos; migração idempotente (rodar duas vezes) se houver teste de
   SQL no repo.
7. Atenção: `main` tem mudanças não comitadas em `ingest/db.py` e `native/service.py`
   (outro trabalho). Edite só a branch; o revisor resolve conflito no merge.

### T7 — Documentação

1. `AGENTS.md` §3: nova lição "Parada de barco sob multipath" (números medidos,
   regra de saída 5/6 a 150 m, mediana em vez de média, spike A–B–C, velocidade
   sozinha não serve como sinal de saída nem de spike).
2. `TODO.md`: item para ajustar `position_flags` (PDOP/HDOP/SATINVIEW/SPEED) no
   provisionamento do `univaja-lora` + aplicar `migrate-03` no deploy.
3. Contagem de testes em `AGENTS.md` §2 atualizada.

## Verificação final (revisor)

- Rodar o protótipo/harness de calibragem contra a trilha real guardada no scratchpad
  (nunca no repo) e conferir: parada de 15 h = 1 dwell, p90 ≈ 60 m; parada esparsa de
  4 h preservada; vértices da linha muito abaixo de 1579; odômetro plausível.
- `pnpm dev` contra a API e conferir visualmente o barco de Ituí parado.
- Dupla revisão (sonnet + Codex) antes do merge.
