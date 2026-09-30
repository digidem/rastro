# Desenvolvimento

Como montar o ambiente, rodar os testes e as convenções do código.

## 1. Ambiente

- **Docker ≥ 28** (portas publicadas em `127.0.0.1` não isolam em versões antigas).
- Python 3.11+ para `services/`.
- Node 20+ e pnpm 9 para `web/` (Playwright chromium para o e2e do viewer).
- Um rádio Meshtastic na USB só é necessário para o gateway real; os testes não precisam de rádio.

```bash
# stack local (tudo em loopback)
cp deploy/env.example deploy/.env   # preencha as senhas
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps --format '{{.Service}} {{.Status}}'
```

## 2. Testes

| Suite | Onde | Como |
|---|---|---|
| Gateway + ingest | `services/rastro_gateway` | `pip install -e . pytest && pytest -q` |
| API | `services/rastro_api` | `pip install -e ".[dev]"`; precisa de um PostgreSQL de teste: `RASTRO_PG_HOST`, `RASTRO_PG_PORT`, `RASTRO_PG_USER`, `RASTRO_PG_PASSWORD` e `RASTRO_API_TOKEN` no ambiente; os testes criam e apagam o banco `rastro_api_test` |
| Viewer unitários | `web/` | `pnpm test` (Vitest) |
| Viewer e2e | `web/` | `pnpm e2e` (Playwright) com `RASTRO_API_TOKEN` no ambiente |
| Lint/format | `web/` | `pnpm biome check` |
| Build do viewer | `web/` | `pnpm build` |
| e2e com rádio real | host do gateway | `RASTRO_E2E_NODES="!xxxxxxxx,!yyyyyyyy" python scripts/rastro_e2e_check.py` |

PostgreSQL descartável para a suíte da API:

```bash
docker run -d --rm --name pg-teste -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=rastro -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=rastro postgres:17-alpine
RASTRO_PG_HOST=127.0.0.1 RASTRO_PG_PORT=55432 RASTRO_PG_USER=rastro \
  RASTRO_PG_PASSWORD=teste RASTRO_API_TOKEN=teste pytest -q
```

Fumaça mínima depois de mudar a stack: `docker compose config` valida; `curl -s http://127.0.0.1:8081` → 200; API sem token → 401; `/api/healthz` → 200.

## 3. Convenções

- **PT-BR** em strings de operador, comentários e docs de operação; identificadores em inglês (`node_num`, não `numeroNo`).
- Coordenada de teste fica sempre no oceano (ex.: `10.1234, -30.1234`), nunca sobre território real.
- Ids de nó de teste são sintéticos (`!aaaa0001`, `!deadbeef`).
- Nunca imprimir payloads, chaves ou coordenadas em log — só nome curto do nó e contagens.
- Scripts de operação prefixados `rastro_`, com `--help` em PT-BR.
- Nomes amigáveis dos nós são opcionais: `RASTRO_FLEET_NAMES_FILE` aponta para um JSON `{"devices": [{"id": …, "identity": {"node_num": …, "long_name": …, "short_name": …}}]}`. Sem ele, o nó aparece como `!hexid`.

## 4. Estrutura

```
services/rastro_gateway/
  bridge/   gateway.py (serial) · packet_filter · spool · mqtt_out · geojson_in
  ingest/   MQTT → PostgreSQL (lote, ack depois do commit, savepoint por registro)
  common/   records (schema dos registros) · fleet_names
  tests/
services/rastro_api/
  api/      main.py (FastAPI) · queries.py · geojson.py
  tests/
web/
  src/      SolidJS: providers (DataProvider, MapProvider, StoreProvider)
  e2e/      Playwright
  tests/    Vitest
deploy/
  docker-compose.yml · mosquitto/ · postgres/init/ · caddy/ · systemd/
scripts/
  rastro_gen_certs.sh · rastro_backup.sh · rastro_retention.py · rastro_e2e_check.py
  rastro_provision_node.sh (provisão de nó na USB; ver docs/PROVISAO-noes.md)
```

## 5. Verificação do deploy (antes de qualquer QA humano)

| Script | O que prova |
|---|---|
| `deploy/sim/run.sh <rastro.yml>` | instalação simulada do template CapRover (rede interna, nomes `srv-captain--*`, frente TLS como o nginx do CapRover): publicação do gateway → banco → API; 403/401; cookie Secure por HTTPS; negativos do broker; privilégios; fila QoS1 sobrevivendo a restart; conteúdo das imagens |
| `deploy/sim/pg-matrix.sh` | bootstrap e restauração do PostgreSQL: superusuário e admin sem superusuário (PG14/PG17), 2ª instalação, papéis de outra instalação recusados, backup, cadeia de versões, schema antigo, recuperação |
| `deploy/sim/browser-http.py` | navegador real: em HTTP puro fora do loopback o visualizador não envia token nem cookie |

O que a simulação NÃO cobre: a substituição de variáveis do próprio CapRover, o `ports:` sob Swarm e o nginx real do CapRover — isso é o QA numa instância de teste.
