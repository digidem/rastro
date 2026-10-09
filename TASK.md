# TASK — Previsão do tempo diária para os barcos

Branch `feat/clima` (worktree `../rastro-clima`). Cada tarefa abaixo é implementada
por um subagente, revisada e commitada antes da próxima. Todo o código novo fica em
`services/rastro_api/api/` (a API já tem os GRANTs certos: o papel `viewer` lê
`vw_ultima_posicao`/`boat_devices`/`virtual_gateways`/`chat_outbox` e pode inserir em
`chat_outbox`; o papel do chat/ingest NÃO lê coordenadas).

## Decisões do dono (2026-10-09)

- Todo dia, cada barco configurado recebe UMA mensagem de texto no rádio com a
  previsão do dia para a posição dele.
- **Barcos padrão:** os 5 regionais (barco 1 de cada regional). `{REGIONAL}` é o nome
  de exibição:

  | boat_id          | regional     |
  |------------------|--------------|
  | `itui-1`         | Ituí         |
  | `itaquai-1`      | Itaquaí      |
  | `medio-javari-1` | Médio Javari |
  | `curuca-1`       | Curuçá       |
  | `jaquirana-1`    | Jaquirana    |

  A lista muda pela env `RASTRO_CLIMA_BARCOS` (JSON); reiniciar a API aplica. Sem tela web.
- **Fonte:** Open-Meteo (`https://api.open-meteo.com/v1/forecast`, grátis, sem chave).
- **Posição:** último fix do barco, **exata** (decisão do dono: sem arredondar). Fix com
  mais de 48 h → usa o ponto reserva do barco (`lat`/`lon` opcionais no JSON da env);
  sem fix e sem reserva → pula o barco e loga (sem coordenadas no log).
- **Horário:** 08:00 local, UTC−5 fixo (Atalaia do Norte; Brasil sem horário de verão).
  `{DATA}` = `dd/mm`. Os barcos saem **1 minuto um do outro** (barco i às 08:00 + i min).
- **Entrega:** insere em `chat_outbox` (`created_by = 'clima'`), então aparece no
  histórico do chat. Expira 6 h depois do horário do barco.
- **Broadcast aceito:** o canal EVU é compartilhado; todo barco ouve as 5 previsões.
- Coordenadas vão ao Open-Meteo, mas **nunca** aparecem em log, mensagem de erro ou teste
  com valores reais.

### Texto (aprovado)

```
{REGIONAL} – {DATA} 08h | Hoje: {TEMP_MIN}–{TEMP_MAX}°C. {CHUVA}. {TROVOADA}. Vento {VENTO}. {ALERTA}
```

Exemplo: `Médio Javari – 09/10 08h | Hoje: 22–33°C. Chuva à tarde, chance 70%. Trovoada provável. Vento fraco, rajadas 25 km/h. ALERTA: chuva forte`

- Janela do dia: horas locais 06–22 (inclusive).
- `{CHUVA}`: chance máxima de chuva na janela `< 20%` → `Sem chuva prevista`; senão
  `Chuva {PERIODO}, chance {PROB}%`. `{PERIODO}` = período da hora de pico:
  `de manhã` (06–11), `à tarde` (12–17), `à noite` (18–22); `o dia todo` quando os três
  períodos têm chance máxima ≥ 50%.
- `{TROVOADA}`: alguma hora da janela com `weather_code` 95, 96 ou 99 →
  `Trovoada provável`; senão `Sem trovoada`.
- `{VENTO}`: classe pelo vento sustentado máximo da janela: `fraco` (< 20 km/h),
  `moderado` (20–40), `forte` (> 40); seguido de `, rajadas {G} km/h` (rajada máxima da
  janela). Ex.: `Vento fraco, rajadas 25 km/h.`
- `{ALERTA}`: chuva do dia ≥ 30 mm → `chuva forte`; rajada ≥ 50 km/h → `ventania`;
  ambos → `ALERTA: chuva forte e ventania`; nenhum → parte omitida (texto termina em
  `km/h.`).
- Números arredondados para inteiro (`round`).
- **Tamanho medido em BYTES UTF-8** (acento = 2 bytes no rádio). Meta 150–180;
  acima de 180 loga aviso (só o tamanho, nunca o texto). Acima de 200, aplica em ordem
  até caber: (1) remove `Sem trovoada.`; (2) remove ` 08h`; (3) vento vira
  `Vento {G} km/h.`; (4) `{REGIONAL}` cortado em 12 caracteres. Se ainda passar,
  corta no limite de 200 bytes sem quebrar caractere.

## Convenções (ler antes de codar)

- Identificadores, comentários, logs e docstrings em **português** (veja o código vizinho).
- Só biblioteca padrão para HTTP (`urllib.request`, como `api/osm.py`). Nenhuma
  dependência nova.
- Testes em `services/rastro_api/tests/test_clima_*.py`, sem rede e sem Postgres
  (use fakes, como `tests/test_api_chat.py` faz com conexões falsas).
- Dados de teste: só coordenadas fictícias (ex.: lat -5.0, lon -70.0).
- NUNCA logar coordenadas, URL com coordenadas ou texto da mensagem.
- Gate de cada tarefa, rodado em `services/rastro_api/` do worktree:
  `RASTRO_API_TOKEN=x /home/luandro/Dev/digidem/rastro/services/rastro_api/.venv/bin/pytest -q -p no:cacheprovider`
  (base: 66 passando, 38 pulados). Tudo tem de passar.
- Não mexa em arquivos fora dos listados na tarefa. Não faça commit. O revisor commita.

---

## T1 — Busca e resumo da previsão: `api/clima_previsao.py`

Arquivos: `services/rastro_api/api/clima_previsao.py`, `services/rastro_api/tests/test_clima_previsao.py`.

- `montar_url(lat: float, lon: float, base: str = URL_PADRAO) -> str` com os parâmetros:
  `latitude`, `longitude`,
  `hourly=precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m`,
  `daily=temperature_2m_min,temperature_2m_max,precipitation_sum`,
  `timezone=America/Eirunepe`, `forecast_days=1`, `wind_speed_unit=kmh`
  (use `urllib.parse.urlencode`). `URL_PADRAO = "https://api.open-meteo.com/v1/forecast"`;
  a base pode vir da env `RASTRO_CLIMA_API_URL`.
- `buscar(lat, lon, *, base=None, timeout=15.0, abrir=urllib.request.urlopen) -> dict`:
  GET, lê JSON. Qualquer falha (rede, HTTP != 200, JSON inválido) levanta
  `PrevisaoErro` com mensagem SEM URL e SEM coordenadas (ex.: `"Open-Meteo: HTTP 503"`,
  `"Open-Meteo: TimeoutError"`). `abrir` é injetável para teste.
- `@dataclass(frozen=True) class Resumo`: `temp_min: int`, `temp_max: int`,
  `chance_chuva: int`, `periodo: str | None` (`"de manhã"`, `"à tarde"`, `"à noite"`,
  `"o dia todo"`; `None` quando `chance_chuva < 20`), `trovoada: bool`,
  `vento_kmh: int`, `rajada_kmh: int`, `chuva_mm: float`.
- `resumir(dados: dict) -> Resumo`: aplica as regras da seção "Texto". `hourly.time`
  vem como `"2026-10-09T06:00"` (hora local, pois pedimos `timezone`); filtre horas
  06–22. Valores `None` nas listas são ignorados; janela sem nenhum valor → 0 (para
  chuva/vento) e `trovoada=False`. Temperaturas e `precipitation_sum` vêm de `daily`
  (índice 0); ausentes → `PrevisaoErro("Open-Meteo: resposta incompleta")`.
  Empate de pico: a primeira hora vence.
- Testes: URL contém todos os parâmetros; `buscar` com `abrir` falso (sucesso, HTTP 500,
  timeout, JSON inválido) e mensagem de erro sem `-5.0`/`-70.0`; `resumir` cobre cada
  período, `o dia todo`, chance < 20 → `periodo=None`, trovoada 95/96/99 e 95 fora da
  janela (ex.: 03:00) ignorada, `None` nas listas, `daily` ausente.

## T2 — Composição do texto: `api/clima_texto.py`

Arquivos: `services/rastro_api/api/clima_texto.py`, `services/rastro_api/tests/test_clima_texto.py`.

- `LIMITE_BYTES = 200`, `META_BYTES = 180`.
- `compor(regional: str, dia: datetime.date, hora: str, r: Resumo) -> str` monta o texto
  (`hora` = `"08h"`) exatamente como a seção "Texto" e aplica as reduções em ordem só
  enquanto `len(texto.encode("utf-8")) > LIMITE_BYTES`. Corte final por bytes sem
  quebrar caractere UTF-8. Acima de `META_BYTES` no fim: `log.warning` com o tamanho
  (nunca o texto). Logger: `logging.getLogger("rastro_api.clima")`.
- Use `–` (en dash, U+2013) depois do regional e entre as temperaturas, como no modelo.
- Testes: o exemplo da seção "Texto" sai idêntico; sem chuva; sem alerta (termina em
  `km/h.`); alerta só ventania; ambos; cada passo de redução (regional enorme força os
  passos 1–4, verifique a ordem e que passos posteriores não rodam quando já cabe);
  resultado nunca passa de 200 bytes nem quebra caractere (regional com só `ç`/`í`);
  aviso logado acima de 180 (use `caplog`) sem o texto.

## T3 — Configuração: `api/clima_config.py`

Arquivos: `services/rastro_api/api/clima_config.py`, `services/rastro_api/tests/test_clima_config.py`.

- `@dataclass(frozen=True) class BarcoClima`: `boat_id: str`, `regional: str`,
  `reserva: tuple[float, float] | None` (lat, lon).
- `@dataclass(frozen=True) class ConfigClima`: `ativo: bool`, `barcos: tuple[BarcoClima, ...]`,
  `hora: int` (0–23), `minuto: int`, `intervalo_s: int`, `ttl_h: int`,
  `max_idade_h: int`, `utc_offset_h: int`, `api_url: str`.
- `BARCOS_PADRAO`: os 5 da tabela, na ordem da tabela, sem reserva.
- `carregar(env: Mapping[str, str]) -> ConfigClima`:
  - `RASTRO_CLIMA_ENABLED` (padrão `"0"`; só `"1"` liga).
  - `RASTRO_CLIMA_BARCOS`: JSON lista de `{"boat_id", "regional", "lat"?, "lon"?}`.
    Ausente/vazia → `BARCOS_PADRAO`. Inválida (JSON ruim, não-lista, item sem
    `boat_id`/`regional`, `lat` sem `lon` ou fora de ±90/±180, boat_id repetido) →
    `log.error` SEM coordenadas e `ativo=False` (a API sobe normal, só sem previsão).
  - `RASTRO_CLIMA_HORA` `"HH:MM"` (padrão `"08:00"`), `RASTRO_CLIMA_INTERVALO_S` (60),
    `RASTRO_CLIMA_TTL_H` (6), `RASTRO_CLIMA_MAX_IDADE_H` (48), `RASTRO_CLIMA_UTC_OFFSET_H`
    (-5), `RASTRO_CLIMA_API_URL` (URL padrão do T1). Valor inválido → `log.error` e
    `ativo=False`.
- Testes: padrão; JSON custom com e sem reserva; cada caso inválido desliga; a
  mensagem de log de JSON inválido não contém coordenadas (`caplog`).

## T4 — Agenda diária: `api/clima_agenda.py` + consultas

Arquivos: `services/rastro_api/api/clima_agenda.py`, `services/rastro_api/api/queries.py`
(só ACRESCENTAR funções/SQL no fim; não alterar as existentes),
`services/rastro_api/tests/test_clima_agenda.py`.

Consultas novas em `queries.py`:

- `ultima_posicao_barco(conn, boat_id) -> dict | None` → `{lat, lon, pos_time}` do fix
  mais novo entre os nós com vínculo aberto do barco:
  ```sql
  SELECT v.lat, v.lon, v.pos_time
  FROM boat_devices bd
  JOIN vw_ultima_posicao v ON v.node_num = bd.node_num
  WHERE bd.boat_id = %s AND bd.valid_to IS NULL AND v.pos_time IS NOT NULL
  ORDER BY v.pos_time DESC
  LIMIT 1
  ```
- `enfileirar_clima(conn, boat_id, texto, expires_at, desde) -> int | None`: numa
  transação READ WRITE (mesmo padrão de `insert_outbox_message`, inclusive restaurar
  `read_only`): `SELECT pg_advisory_xact_lock(hashtext('rastro-clima'))`; se já existe
  `chat_outbox` com `boat_id = %s AND created_by = 'clima' AND created_at >= %s`
  (`desde`) → devolve `None`; senão insere (`created_by='clima'`) e devolve o `id`.
  O lock + checagem tornam o envio idempotente mesmo com 2 instâncias da API.
- `clima_ja_enfileirado(conn, boat_id, desde) -> bool` (mesma checagem, só leitura;
  usada para não buscar previsão à toa).

`clima_agenda.py`:

- `class AgendaClima(pool, cfg: ConfigClima, *, buscar=clima_previsao.buscar, agora=None)`
  (`agora` padrão `lambda: datetime.now(timezone.utc)`).
- `rodar_uma_vez() -> int` (quantos enfileirou, 0 ou 1). Com `tz = timezone(timedelta(hours=cfg.utc_offset_h))`
  e `local = agora().astimezone(tz)`:
  1. Envia **no máximo um** barco por chamada, e só se já passaram `cfg.intervalo_s` desde
     o último envio desta instância (garante o espaçamento de 1 min mesmo quando a API
     reinicia às 08:10 com os 5 atrasados).
  2. Para cada barco `i` na ordem da config: `slot = local.replace(hora, minuto, 0, 0) + i*intervalo_s`.
     Pula se `local < slot` ou `local > slot + ttl_h`. `desde` = 00:00 local do dia, em UTC.
  3. Pula se `clima_ja_enfileirado`, ou se o barco está em espera de nova tentativa
     (`self._tentar_depois[boat_id] > agora`).
  4. Posição: `ultima_posicao_barco`; idade > `max_idade_h` ou ausente → `reserva`;
     sem nada → `log.warning("clima: %s sem posição recente; pulado hoje", boat_id)`,
     marca o barco como pulado no dia (não tenta de novo até o dia seguinte).
  5. `buscar(lat, lon, base=cfg.api_url)` → `resumir` → `compor(regional, local.date(), f"{cfg.hora:02d}h", r)`.
     `PrevisaoErro`/qualquer exceção → `log.warning` com `boat_id` e o TIPO/mensagem do
     erro (já sem coordenadas) e `_tentar_depois[boat_id] = agora + 5 min`.
  6. `enfileirar_clima(..., expires_at = slot (UTC) + ttl_h, desde)`; se devolveu id,
     `log.info("clima: previsão enfileirada para %s (id=%s, %d bytes)", ...)` e retorna 1.
  - Conexão: `with self.pool.connection() as conn:` por barco processado.
- `iniciar(stop: threading.Event, passo_s: float = 30.0) -> threading.Thread`: thread
  daemon `rastro-clima` que chama `rodar_uma_vez()` a cada `passo_s` até `stop`;
  exceção inesperada vira `log.error` com o tipo e o laço segue.
- Testes (pool/conexão falsos que gravam as consultas; `agora` fixo; `buscar` falso):
  antes das 08:00 nada; 08:00 → só o barco 0; 08:00:30 na mesma instância → nada
  (intervalo); 08:01 → barco 1; reinício às 08:10 → um por chamada, respeitando
  intervalo; depois de 08:00 + 6 h → nada; já enfileirado → não chama `buscar`; fix
  velho com reserva usa a reserva; fix velho sem reserva pula o dia e não loga
  coordenadas; `buscar` falhando → espera 5 min e tenta de novo; `expires_at` = slot + 6 h;
  `desde` = meia-noite local em UTC (05:00 UTC); `enfileirar_clima` devolvendo `None`
  (outra instância enviou) → retorna 0 sem erro.

## T5 — Ligar na API e documentar

Arquivos: `services/rastro_api/api/main.py`, `services/rastro_api/tests/test_clima_main.py`,
`AGENTS.md`, `docs/native-ingest-design.md`.

- No `lifespan` de `main.py`: depois de abrir o pool, `cfg = clima_config.carregar(os.environ)`;
  se `cfg.ativo`, cria `AgendaClima(pool, cfg)` e `iniciar(stop)`; log
  `"previsão do tempo: ligada (%d barcos, %02d:%02d UTC%+d)"` ou `"previsão do tempo: desligada"`.
  No `finally`, `stop.set()` e `join(timeout=5)` ANTES de `pool.close()`.
- Teste: com `RASTRO_CLIMA_ENABLED` ausente a agenda não é criada (monkeypatch de
  `AgendaClima` para detectar); com `"1"` é criada e parada no shutdown. Sem Postgres:
  siga o padrão de testes existentes que sobem o app sem banco, ou monkeypatch do
  `ConnectionPool`.
- `AGENTS.md`: nova lição **12. Previsão do tempo diária** (curta, no estilo das outras):
  onde roda (thread na API, papel viewer), envs `RASTRO_CLIMA_*` e padrões, idempotência
  por `created_by='clima'` + advisory lock, espaçamento de 1 min, tamanho em bytes, posição
  exata vai ao Open-Meteo por decisão do dono (2026-10-09), desligada por padrão.
- `docs/native-ingest-design.md`: perto de `RASTRO_CHAT_TTL_SECS`, uma linha citando as
  envs `RASTRO_CLIMA_*` e apontando para a lição 12.
