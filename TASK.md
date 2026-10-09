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
  (`hora` = rótulo `"08h"` ou `"08h30"`; o passo 2 da redução remove ` {hora}`) exatamente como a seção "Texto" e aplica as reduções em ordem só
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
  `hora: int`, `minuto: int`, `intervalo_s: int`, `ttl_h: int`, `max_idade_h: int`,
  `utc_offset_h: int`, `api_url: str`. Propriedade `rotulo_hora -> str`: `"08h"` quando
  `minuto == 0`, senão `"08h30"` (é o que vai no texto no lugar de `08h`).
- `BARCOS_PADRAO`: os 5 da tabela, na ordem da tabela, sem reserva.
- `carregar(env: Mapping[str, str]) -> ConfigClima`. Qualquer valor inválido →
  `log.error` (logger `rastro_api.clima`) SEM coordenadas e SEM o JSON cru, e
  `ativo=False` (a API sobe normal, só sem previsão). Regras:
  - `RASTRO_CLIMA_ENABLED`: só `"1"` liga (padrão `"0"`).
  - `RASTRO_CLIMA_BARCOS`: ausente ou só espaços → `BARCOS_PADRAO`. Senão tem de ser
    uma lista JSON NÃO vazia de objetos. Cada objeto: `boat_id` e `regional` strings
    não vazias depois de `strip()` (guarde sem os espaços); `boat_id` casa
    `^[a-z0-9-]+$`; sem `boat_id` repetido. `lat` e `lon` opcionais mas **juntos**
    (um sem o outro é inválido); cada um `int` ou `float` (NÃO `bool`), finito
    (`math.isfinite`), `-90 ≤ lat ≤ 90`, `-180 ≤ lon ≤ 180`. Chaves extras são ignoradas.
  - `RASTRO_CLIMA_HORA`: `"HH:MM"` (regex `^\d{2}:\d{2}$`), `00 ≤ HH ≤ 23`,
    `00 ≤ MM ≤ 59` (padrão `"08:00"`).
  - Inteiros (`int(texto)`; texto não inteiro é inválido): `RASTRO_CLIMA_INTERVALO_S`
    (padrão 60, `1..3600`), `RASTRO_CLIMA_TTL_H` (6, `1..12`),
    `RASTRO_CLIMA_MAX_IDADE_H` (48, `1..720`), `RASTRO_CLIMA_UTC_OFFSET_H` (-5, `-12..14`).
  - O último slot (`hora:minuto + (n_barcos-1) * intervalo_s`) tem de cair no MESMO dia
    local (antes de 24:00); senão inválido. Assim cada envio pertence a um único dia.
  - `RASTRO_CLIMA_API_URL`: padrão `clima_previsao.URL_PADRAO`; tem de começar com
    `https://` ou `http://`.
- Testes: padrão (5 barcos, 08:00, `rotulo_hora == "08h"`); `"08:30"` → `"08h30"`;
  JSON custom com e sem reserva; strings com espaços são aparadas; CADA caso inválido
  acima desliga (parametrize), incluindo `lat: true`, `lat: NaN` (via `float("nan")` não
  existe em JSON: use `"lat": 1e999`, que vira `inf`), lista vazia, `"23:59"` com 5
  barcos (atravessa o dia); a mensagem de log nunca contém os números das coordenadas
  (`caplog`).

## T4 — Agenda diária: `api/clima_agenda.py` + consultas

Arquivos: `services/rastro_api/api/clima_agenda.py`, `services/rastro_api/api/queries.py`
(só ACRESCENTAR funções/SQL no fim; não alterar as existentes),
`services/rastro_api/tests/test_clima_agenda.py`.

### Regra de conexões (importante)

psycopg recusa mudar `conn.read_only` com transação aberta, e qualquer SELECT abre uma.
Por isso **cada função de `queries.py` abaixo recebe sua própria conexão**: a agenda faz
`with self.pool.connection() as conn:` separado para (a) leituras, e outro para (b) a
escrita. A busca HTTP acontece FORA de qualquer conexão (não segure conexão do pool
durante até 15 s de rede).

### Consultas novas em `queries.py`

- `ultima_posicao_barco(conn, boat_id) -> dict | None` → `{lat, lon, pos_time}`:
  ```sql
  SELECT v.lat, v.lon, v.pos_time
  FROM boat_devices bd
  JOIN vw_ultima_posicao v ON v.node_num = bd.node_num
  WHERE bd.boat_id = %s AND bd.valid_to IS NULL AND v.pos_time IS NOT NULL
  ORDER BY v.pos_time DESC
  LIMIT 1
  ```
- `clima_ja_enfileirado(conn, boat_id, desde) -> bool`: existe `chat_outbox` com
  `boat_id = %s AND created_by = 'clima' AND created_at >= %s`. Só leitura.
- `enfileirar_clima(conn, boat_id, texto, expires_at, desde, intervalo_s) -> int | None`:
  conexão recém-tirada do pool, mesmo padrão de `insert_outbox_message` (vira
  `read_only = False`, `with conn.transaction():`, restaura `read_only` no `finally`).
  Dentro da transação, nesta ordem:
  1. `SELECT pg_advisory_xact_lock(hashtext('rastro-clima'))`
  2. já existe linha `clima` deste barco com `created_at >= desde` → devolve `None`;
  3. espaçamento PERSISTENTE (vale entre reinícios e entre duas instâncias da API):
     ```sql
     SELECT coalesce(max(created_at) > clock_timestamp() - make_interval(secs => %s), false) AS cedo
     FROM chat_outbox
     WHERE created_by = 'clima'
     ```
     (parâmetro: `intervalo_s`); `cedo` True → devolve `None`. Use `clock_timestamp()`
     (hora real depois do lock), não `now()` (hora do início da transação).
     Limitação aceita: `created_at` da linha nova vem do default `now()` (o papel viewer
     não pode escrever essa coluna), então o espaçamento pode encurtar no máximo pelo
     tempo de espera no lock (milissegundos).
  4. `INSERT ... (boat_id, text, created_by, expires_at) VALUES (%s, %s, 'clima', %s) RETURNING id`
     → devolve o id.

### `clima_agenda.py`

- `class AgendaClima(pool, cfg: ConfigClima, *, buscar=clima_previsao.buscar, agora=None, parar: threading.Event | None = None)`
  (`agora` padrão `lambda: datetime.now(timezone.utc)`; `parar` padrão `threading.Event()`).
- Estado em memória (só otimização; a verdade está no banco):
  `self._pulados: dict[str, date]` (boat_id → dia local em que foi pulado) e
  `self._tentar_depois: dict[str, datetime]`. Entrada de outro dia é ignorada (e pode
  ser apagada), então a virada do dia limpa sozinha.
- `rodar_uma_vez() -> int` (0 ou 1 enfileirado). `tz = timezone(timedelta(hours=cfg.utc_offset_h))`,
  `agora_utc = agora()`, `local = agora_utc.astimezone(tz)`, `hoje = local.date()`,
  `base = local.replace(hour=cfg.hora, minute=cfg.minuto, second=0, microsecond=0)`,
  `desde = local.replace(hour=0, minute=0, second=0, microsecond=0)` (aware; o psycopg
  converte). Percorre os barcos na ordem; processa **no máximo um** por chamada.
  No início de CADA barco: `self.parar.is_set()` → retorna 0.
  1. `slot = base + timedelta(seconds=i * cfg.intervalo_s)`;
     `expira = slot + timedelta(hours=cfg.ttl_h)`. Pula se `local < slot` ou
     `local >= expira` (o consumidor expira na igualdade).
  2. Pula se `self._pulados.get(boat_id) == hoje` ou `self._tentar_depois.get(boat_id, mínimo) > agora_utc`.
  3. Conexão de leitura: `clima_ja_enfileirado(conn, boat_id, desde)` → pula se True;
     senão `ultima_posicao_barco`. Fecha a conexão.
  4. Posição: fix ausente ou `agora_utc - pos_time > max_idade_h` → `reserva`; sem as
     duas → `log.warning("clima: %s sem posição recente; pulado hoje", boat_id)`,
     `self._pulados[boat_id] = hoje`, segue para o próximo barco.
  5. HTTP fora de conexão: `dados = buscar(lat, lon, base=cfg.api_url)`;
     `texto = compor(regional, hoje, cfg.rotulo_hora, resumir(dados))`.
     `PrevisaoErro` → `log.warning("clima: %s falhou: %s", boat_id, str(erro))`.
     Qualquer OUTRA exceção → loga SÓ o tipo (`type(erro).__name__`), nunca `str()`
     (pode conter URL com coordenadas). Nos dois casos
     `self._tentar_depois[boat_id] = agora_utc + 5 min` e segue para o próximo barco.
  6. Depois do HTTP: se `self.parar.is_set()` → retorna 0 sem escrever. Recalcule
     `agora()` e o `local`; se `local.date() != hoje` (a busca atravessou a meia-noite)
     ou `local >= expira` → retorna 0 sem escrever.
  7. Conexão NOVA de escrita: `id = enfileirar_clima(conn, boat_id, texto, expira, desde, cfg.intervalo_s)`.
     `None` → retorna 0 (outra instância enviou, ou espaçamento ainda não venceu: a
     próxima chamada tenta de novo). Senão
     `log.info("clima: previsão enfileirada para %s (id=%s, %d bytes)", boat_id, id, len(texto.encode()))`
     e retorna 1.
  - Erro de banco (qualquer exceção nos passos 3 ou 7) → `log.error` só com o tipo e
    retorna 0.
- `iniciar(passo_s: float = 30.0) -> threading.Thread`: thread daemon `rastro-clima`;
  laço `while not self.parar.is_set(): try rodar_uma_vez() except Exception → log.error(tipo); self.parar.wait(passo_s)`.
- Testes (pool falso cujas conexões **levantam erro se `read_only` mudar com
  "transação aberta"**, i.e. depois de qualquer `execute` fora de `transaction()`;
  `agora` controlável; `buscar` falso):
  antes das 08:00 nada; 08:00 → só barco 0; 08:01 → barco 1; 08:10 com os 5 atrasados →
  um por chamada; o barco 4 (slot 08:04) ainda sai às 14:03:59 e não às 14:04:00;
  `clima_ja_enfileirado` True → `buscar` não é chamado; fix velho com reserva usa a
  reserva; fix velho sem reserva pula o dia, não loga coordenadas, e volta a ser tentado
  no dia seguinte (dois dias consecutivos no mesmo objeto); `PrevisaoErro` → espera 5 min;
  exceção genérica cuja mensagem contém `"-5.0"` e uma URL → log tem só o tipo;
  `parar` setado durante o `buscar` → nada escrito; `expires_at == slot + 6 h`;
  `desde` = meia-noite local (05:00 UTC); `enfileirar_clima` → `None` retorna 0 sem erro;
  duas `AgendaClima` sobre o mesmo pool falso cujo `enfileirar_clima` simula o lock
  (estado compartilhado) → nunca duas inserções com menos de `intervalo_s`.
  `buscar` que avança o relógio de 23:59:50 para 00:00:10 → nada escrito; `parar`
  setado → a chamada seguinte não chama `buscar` para nenhum barco.
  Testes das três consultas chamando a função REAL de `queries.py` com uma conexão falsa
  roteirizada (devolve linhas por SQL): ordem lock → checagem do barco → checagem do
  espaçamento → INSERT; barco já enviado hoje → `None` sem INSERT; `cedo` True →
  `None` sem INSERT; `cedo` False (inclui tabela vazia) → id; `read_only` restaurado
  também quando o `execute` levanta exceção. A conexão falsa zera o estado de
  "transação aberta" ao sair de `transaction()`.

### Limitação aceita (não implementar)

Espaçar as inserções não garante espaçar a transmissão: se o `rastro-chat` ficar fora do
ar, as 5 linhas acumulam e saem juntas quando ele volta (o `claim_outbox` pega até 10).
Fica registrado na lição do AGENTS.md (T5).

## T5 — Ligar na API e documentar

Arquivos: `services/rastro_api/api/main.py`, `services/rastro_api/tests/test_clima_main.py`,
`AGENTS.md`, `docs/native-ingest-design.md`.

- No `lifespan` de `main.py`: antes do `try:` que já existe, `agenda = None` e
  `thread = None`. DENTRO desse `try:` (antes do `yield`):
  `cfg = clima_config.carregar(os.environ)`; se `cfg.ativo`, `agenda = AgendaClima(pool, cfg)`
  e `thread = agenda.iniciar()`; log `"previsão do tempo: ligada (%d barcos, %s, UTC%+d)"`
  (barcos, `cfg.rotulo_hora`, offset) ou `"previsão do tempo: desligada"`. Assim uma falha
  ao ligar a agenda ainda fecha o pool.
  No `finally`, ANTES de `pool.close()`: se `agenda`, `agenda.parar.set()`; se `thread`,
  `thread.join(timeout=20)` (espera limitada; não é garantia de término, pois o timeout
  HTTP é por operação); se a thread ainda estiver viva, `log.warning`. `pool.close()`
  sempre roda (envolva o encerramento da agenda em `try/finally`).
- Teste: com `RASTRO_CLIMA_ENABLED` ausente a agenda não é criada (monkeypatch de
  `AgendaClima` para detectar); com `"1"` é criada, `iniciar` chamado, e no shutdown
  `parar` é setado antes de `pool.close()` (monkeypatch do `ConnectionPool` para
  registrar a ordem). Use `TestClient(app)` como context manager para rodar o lifespan.
- `AGENTS.md`: nova lição **12. Previsão do tempo diária** (curta, no estilo das outras):
  roda como thread na API (papel viewer: lê posição, insere em `chat_outbox`); envs
  `RASTRO_CLIMA_*` e padrões; desligada por padrão; idempotência e espaçamento de 1 min
  garantidos no banco (advisory lock + `created_by='clima'`); uma tentativa enfileirada
  por barco por dia (sem garantia de recebimento); tamanho medido em bytes; posição
  exata vai ao Open-Meteo por decisão do dono (2026-10-09); limitação: chat fora do ar
  acumula e envia junto.
- `docs/native-ingest-design.md`: perto de `RASTRO_CHAT_TTL_SECS`, uma linha citando as
  envs `RASTRO_CLIMA_*` e apontando para a lição 12 do AGENTS.md.
