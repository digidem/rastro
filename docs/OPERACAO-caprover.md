# Operação — Rastro no CapRover

Instalação do Rastro num CapRover que já tem um PostgreSQL, pelo app one-click `rastro` da loja digidem. O gateway (rádio na USB) continua fora do CapRover, na base.

```
Pi da base: rastro-gateway ──MQTT sobre HTTPS/443 (WebSocket)──▶ nginx do CapRover ─▶ <app>-broker ─▶ <app>-ingest ─▶ PostgreSQL existente
navegador ──HTTPS──▶ nginx do CapRover ─▶ <app> (web) ─▶ <app>-api ─▶ PostgreSQL existente
```

## 0. Antes de começar

- As imagens `communityfirst/rastro-*` da versão escolhida (0.3.0) precisam estar **públicas no Docker Hub**: o CapRover sempre faz `pull` ao implantar (mesmo com a imagem já no servidor) e falha com `error from registry: denied` se não achar. Isso inclui `communityfirst/rastro-pgtools:<versão>-pg17`, usada pelo app de preparo.
- O **DNS curinga** do CapRover (`*.domínio-raiz`) precisa estar configurado: o app web e o download da CA usam `https://<app>.<domínio-raiz>`.
- O gateway conecta em `<app>-mqtt.<domínio-raiz>`: o DNS curinga do CapRover já aponta esse nome para o servidor, e ele entra sozinho no certificado do broker. Um domínio seu extra é opcional (campo do formulário).
- Tenha a **conexão de admin do PostgreSQL numa URL só**: `postgresql://usuario:senha@host:5432/banco`. No CapRover: `postgresql://postgres:<POSTGRES_PASSWORD>@srv-captain--postgres:5432/postgres` (`POSTGRES_PASSWORD` está nas variáveis de ambiente do app do Postgres). Para Postgres externo/gerenciado, acrescente `?sslmode=require` (aceitos: `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`; nenhum outro parâmetro). O `banco` da URL pode ser qualquer banco já existente ao qual o usuário conecte (ex.: o da sua string de conexão atual) — não é o banco do Rastro. O usuário precisa ser **superusuário ou ter `CREATEROLE` + `CREATEDB`**. Caracteres especiais na senha vão em percent-encoding (`@`=`%40`, `:`=`%3A`, `/`=`%2F`, `%`=`%25`, `#`=`%23`). É o único campo obrigatório do formulário.
- Só o app temporário `<app>-setup` recebe a URL (com a senha). Ele publica **apenas host, porta e sslmode** (sem usuário nem senha) num volume compartilhado, `<app>-pgconn` (`/rastro-pgconn/conn.env`), que o ingest e a API leem sozinhos; por isso não há campos de host/porta.
- Rastros de posição são sensíveis: a equipe decide quem tem acesso ao servidor e aos backups dele antes de instalar.

## 1. Adicionar a loja digidem (uma vez)

CapRover → **Apps** → **One-Click Apps/Databases** → role até **3rd party repositories** → cole a URL da loja digidem → **Connect New Repository**.

## 2. Instalar o app

One-Click Apps → **Rastro** → preencha só:

| Campo | Valor |
|---|---|
| App name | o nome escolhido (ex.: `rastro`) |
| Conexão de admin do PostgreSQL | a URL `postgresql://usuario:senha@host:5432/banco[?sslmode=…]` (**obrigatório**, §0) |
| Nome do banco | `rastro` (padrão). Se já houver outro Rastro neste Postgres, use outro nome. |
| Domínio extra do MQTT | vazio (opcional) |

Tudo o mais tem padrão pronto: as senhas MQTT, as senhas dos papéis do Postgres e o token do mapa já vêm **geradas aleatoriamente** no formulário (não precisa mudar nem gerar nada). O certificado TLS do broker também é gerado sozinho, no primeiro start, com `<app>-mqtt.<domínio-raiz>`, o domínio extra (se houver) e os nomes internos. Anote apenas a senha MQTT do `gateway` e o token do mapa (ficam nas variáveis de ambiente dos apps `<app>-broker` e `<app>-api`), e as senhas `maint`/`backup` se for agendar retenção e backup (§6).

O template cria cinco apps: `<app>-setup` (temporário), `<app>-broker`, `<app>-ingest`, `<app>-api` e `<app>` (web).

## 3. Conferir o preparo do PostgreSQL e apagar o app `<app>-setup`

O app `<app>-setup` roda **uma vez** o preparo do Postgres existente e depois fica parado (não reexecuta sozinho). Abra os logs dele:

- Espere a linha final `OK: PostgreSQL preparado para o Rastro … — agora APAGUE o app <app>-setup`.
- Antes de apagar, **anote** (variáveis de ambiente dele) `RASTRO_PG_PASSWORD_MAINT` e `RASTRO_PG_PASSWORD_BACKUP`, se for usar retenção/backup.
- **Apague o app `<app>-setup`**: ele guarda a URL (com a senha) de admin do Postgres. Na tela de remoção, **não** marque o volume `<app>-pgconn`: ingest e API leem dele o host/porta do Postgres.
- Se aparecer `ERRO: …` (`RASTRO_PG_ADMIN_URL inválida`, senha de admin errada, host errado, nome já usado por outra coisa…), corrija a variável indicada (ex.: `RASTRO_PG_ADMIN_URL`) no app `<app>-setup` e clique **Save & Restart**. Repetir é seguro.

Salvaguardas para um Postgres em produção:

- Só cria coisas **dedicadas**: o banco `<banco>`, o schema `<banco>` e os papéis `<banco>_owner`, `_ingest`, `_viewer`, `_maint`, `_backup`. Nada fora disso é criado, alterado ou lido.
- **Recusa** nomes que já pertençam a outra coisa (banco existente de outro dono, papéis ligados a outro banco, objetos estranhos dentro do banco) e para com `ERRO`.
- **Nunca apaga** nada (sem `DROP`).
- Usa `lock_timeout=5s`, `statement_timeout=120s` e `idle_in_transaction_session_timeout=60s`: se algo estiver ocupado, desiste em vez de travar as outras cargas.
- Espera o Postgres subir (12 tentativas de 10 s) e exige versão 14 ou mais nova.
- É **idempotente**: rodar de novo reaplica as senhas dos papéis (sincroniza com o app) e a migração aditiva do schema.
- Um admin sem superusuário (Postgres gerenciado) funciona se tiver `CREATEROLE` e `CREATEDB`; sem esses privilégios o preparo para com `ERRO` e uma DICA para usar um usuário com eles (ex.: o superusuário do Postgres).
- Depois do `OK`, o app grava `/rastro-pgconn/conn.env` (só `RASTRO_PG_HOST`, `RASTRO_PG_PORT`, `RASTRO_PG_SSLMODE`). O ingest e a API esperam esse arquivo por até 5 min (`RASTRO_PG_CONN_WAIT_SECS`) se `RASTRO_PG_HOST` não estiver definido; variáveis de ambiente `RASTRO_PG_HOST/PORT/SSLMODE` têm precedência sobre o arquivo.

## 4. Depois de instalar

1. **HTTPS primeiro.** App `<app>` → *HTTP Settings* → **Enable HTTPS** e **Force HTTPS**. A API responde 403 sem HTTPS e o visualizador não envia credenciais em HTTP.
2. **MQTT pela porta 443 (padrão).** App `<app>-broker` → *HTTP Settings* → **Enable HTTPS** (Let's Encrypt). O broker é um app web com WebSocket ligado (o template já configura porta 9001 e *Websocket Support*): o gateway fala MQTT sobre HTTPS na 443, então **nenhuma porta extra** precisa ser aberta no firewall do provedor. Usuário e senha valem igual (a ACL também). Entre o nginx e o broker o WebSocket trafega sem TLS, só dentro da rede interna do Docker do servidor.
   - Opcional — MQTT/TLS direto na 8883: publique a porta 8883 no app `<app>-broker` (*Port Mapping* `8883:8883`) e abra 8883/TCP no firewall do provedor; nesse caso o gateway usa a CA de `https://<app>.<domínio>/ca.crt`. Portas publicadas pelo Docker passam por fora das regras INPUT do ufw/iptables.
3. **Basemap (automático).** O mapa já vem com um basemap padrão: tiles do OpenStreetMap buscados **pela API** (`/api/osm/…`, com o token; o navegador nunca fala com o OSM, então o IP dos monitores não chega lá, e os tiles ficam num cache em memória). Nada a fazer. Ressalvas: exige que o servidor alcance `tile.openstreetmap.org` (HTTPS) e segue a [política de uso do OSM](https://operations.osmfoundation.org/policies/tiles/) — adequado para poucos usuários. A área que o monitor olha é dado sensível: a API não loga coordenadas de tile (o log de acesso do uvicorn está desligado). Para **desligar** o OSM, defina `RASTRO_OSM_TILES=0` no app `<app>-api` (sem basemap próprio, o mapa mostra só os pontos), ou aponte para um servidor de tiles seu com `RASTRO_OSM_TILE_URL=https://…/{z}/{x}/{y}.png`.
   - **Camadas extras (opcional).** `RASTRO_OVERLAYS_URL` no app `<app>-api` aponta para um `.geojson` ou para uma pasta pública que sirva um `.zip` (ex.: link de compartilhamento do File Browser); cada `.geojson` dentro vira uma camada sobre o mapa base (pontos, linhas e polígonos). A API busca e guarda em cache por 1 h; o navegador só fala com `/api/overlays`. Padrão: a pasta pública da TI Javari (aldeias DSEI-VAJ + contorno FUNAI). Vazio ou `0` desliga.
   - **Opcional — basemap próprio e offline** (PMTiles): copie `basemap.pmtiles` para o volume `<app>-tiles`; quando o arquivo existe, o mapa o usa em vez do OSM (sem nenhuma requisição externa). Permissão de leitura para todos (`chmod a+r`):
     ```bash
     docker run --rm -v captain--<app>-tiles:/t -v "$PWD":/src:ro busybox sh -c 'cp /src/basemap.pmtiles /t/ && chmod a+r /t/basemap.pmtiles'
     ```
     (o nome exato do volume aparece em `docker volume ls`). As fontes dos rótulos já vêm na imagem.
4. Conferir: `https://<app>.<seu-domínio>/api/healthz` → `{"status":"ok",…}`; o mapa pede o token.

## 5. Apontar o gateway da base

No host do gateway (ex.: Raspberry Pi), no arquivo de ambiente da unit `rastro-gateway`:

```
RASTRO_MQTT_TRANSPORT=websockets
RASTRO_MQTT_HOST=<app>-broker.<domínio-raiz>
RASTRO_MQTT_PORT=443
RASTRO_MQTT_USERNAME=gateway
RASTRO_MQTT_PASSWORD=<senha "gateway" do app>
RASTRO_MQTT_TOPIC_PREFIX=rastro
```

Sem `RASTRO_MQTT_CA_CERT`: o certificado do nginx (Let's Encrypt) já é confiável no sistema. A senha `gateway` está em `RASTRO_MQTT_PASSWORD_GATEWAY`, nas variáveis do app `<app>-broker`.

Reinicie a unit. Enquanto o link de satélite cair, o gateway guarda tudo no spool em disco e reenvia na volta.

**Alternativa direta (8883):** com a porta 8883 publicada e aberta (§4), use `RASTRO_MQTT_TRANSPORT=tcp`, `RASTRO_MQTT_HOST=<app>-mqtt.<domínio-raiz>`, `RASTRO_MQTT_PORT=8883` e `RASTRO_MQTT_CA_CERT=/etc/rastro/ca.crt`, baixando a CA pública com `curl -fsS https://<app>.<seu-domínio>/ca.crt | sudo tee /etc/rastro/ca.crt`.

### Alternativa: trazer seu próprio certificado

Se preferir uma CA da equipe (ou um certificado já existente), gere com o script e monte os arquivos no broker; quando há arquivos/variáveis TLS informados, o broker NÃO gera certificado:

```bash
scripts/rastro_gen_certs.sh --cert-dir ~/rastro-certs \
  --san DNS:mqtt.exemplo.org \
  --san DNS:srv-captain--rastro-broker --san DNS:rastro-broker \
  --b64
```

- A chave da CA fica em `~/.local/share/rastro/ca/` — guarde com a equipe; ela assina certificados novos.
- Monte `ca.crt`, `server.crt` e `server.key` em `/mosquitto/secrets/` do app `<app>-broker` (dono uid 1883, modo 0400) ou defina `RASTRO_TLS_CA_B64`, `RASTRO_TLS_SERVER_CRT_B64` e `RASTRO_TLS_SERVER_KEY_B64` no broker (variáveis do CapRover ficam em texto puro no diretório de dados dele e nos backups; prefira os arquivos). Para o ingest confiar na CA própria, coloque o mesmo `ca.crt` no volume `<app>-pki` (o web também o serve em `/ca.crt`).
- Validade do certificado do servidor: 825 dias. Anote a data de renovação.

## 6. Retenção e backup

Não rodam dentro do app. Agende no servidor (cron) ou no Windmill; os dois estão na imagem `rastro-pgtools` (mesma versão major do servidor) e precisam da rede `captain-overlay-network`:

```bash
# backup: pg_dump -Fc -n rastro pelo papel rastro_backup, rotação de 14 cópias
docker run --rm --network captain-overlay-network --user "$(id -u):$(id -g)" \
  -v /srv/rastro-backups:/backups -e RASTRO_BACKUP_DIR=/backups \
  -e RASTRO_PG_HOST=srv-captain--postgres -e RASTRO_PG_DB=rastro --env-file rastro-backup.env \
  --entrypoint /rastro/rastro_backup.sh communityfirst/rastro-pgtools:<versão>-pg<major>
# retenção: por padrão só simula (dry-run); apagar exige confirmação explícita
docker run --rm --network captain-overlay-network \
  -e RASTRO_PG_HOST=srv-captain--postgres -e RASTRO_PG_DB=rastro --env-file rastro-maint.env \
  --entrypoint python3 communityfirst/rastro-pgtools:<versão>-pg<major> /rastro/rastro_retention.py --help
```

`rastro-backup.env` / `rastro-maint.env` (modo 600) têm só `RASTRO_PG_PASSWORD=` do papel correspondente (as senhas `maint`/`backup` anotadas no passo 3). Se o CapRover já faz backup do Postgres inteiro, avalie se precisa deste.

## 7. Restaurar um backup

Num Postgres novo (ou depois de `DROP DATABASE rastro`), com a imagem `-pg<major>` do servidor de DESTINO (e a de origem não pode ser mais nova que ela). O admin e as MESMAS senhas dos papéis (`maint`/`backup` anotadas no passo 3; `ingest`/`viewer` nas variáveis de `<app>-ingest`/`<app>-api`) vão num arquivo `rastro-bootstrap.env` (modo 600) com `PGHOST=srv-captain--postgres`, `PGUSER=postgres`, `PGPASSWORD=<admin>`, `RASTRO_DB=<banco>` e `RASTRO_PG_PASSWORD_{INGEST,VIEWER,MAINT,BACKUP}=…`; apague-o depois:

```bash
docker run --rm --network captain-overlay-network --user "$(id -u):$(id -g)" \
  -v /srv/rastro-backups:/b:ro --env-file rastro-bootstrap.env \
  communityfirst/rastro-pgtools:<versão>-pg<major> --restore /b/rastro-AAAAMMDD.dump
```

`--user` com o seu uid: o dump é gravado com modo 600 e precisa ser legível pelo contêiner. A restauração NÃO migra o schema (o dump volta exatamente como foi feito): se o dump for de uma versão anterior do Rastro, rode o `rastro-pgtools` da versão atual de novo, SEM `--restore`, para aplicar a migração aditiva — senão o ingest sai com código 2 listando o que falta. O script recusa: banco com o schema já existente; dump de outro nome de banco (`RASTRO_DB` precisa ser igual ao da origem); versões fora da cadeia `origem ≤ pg_dump ≤ pg_restore ≤ servidor`; banco de outra instalação. A restauração é em transação única — se falhar, nada fica aplicado. Se o processo cair depois da restauração e antes dos GRANTs, rode o script de novo sem `--restore`.

## 8. Atualizar

Mude a versão (tag) nos apps do CapRover, ou rode `caprover deploy -i communityfirst/rastro-<serviço>:<nova tag> -a <app>-<serviço>` para cada um. Mudanças de schema: rode de novo o preparo — recrie temporariamente o app `<app>-setup` com a URL de admin, ou use `rastro-pgtools` sem `--restore` como no §7 (a migração é aditiva e idempotente).

Atenção: cada execução do preparo REAPLICA as quatro senhas dos papéis. Use sempre as mesmas dos apps `<app>-ingest`/`<app>-api`; senha diferente derruba o ingest e a API até as variáveis serem atualizadas.

## 9. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| broker sai com `ERRO: /mosquitto/data não é gravável` | volume antigo com dono errado: `chown -R 1883:1883` no volume |
| broker sai com `configuração TLS incompleta` | (certificado próprio) só uma ou duas das três variáveis B64 preenchidas |
| ingest sai com código 2 e `banco não está pronto` | o preparo (`<app>-setup`) não deu `OK`, ou nome do banco diferente (a mensagem lista o que falta e o search_path) |
| `<app>-setup` termina com `ERRO: RASTRO_PG_ADMIN_URL inválida (…)` | a URL está mal formada (esquema, host/usuário/senha ausentes, `sslmode` fora da lista, `#`/`@`/`:`/`/` da senha sem percent-encoding); o motivo vem na mensagem, nunca o valor |
| `<app>-setup` termina com `ERRO: o admin precisa ser superusuário ou ter CREATEROLE e CREATEDB` | use na URL o superusuário do Postgres ou um usuário com `CREATEROLE`+`CREATEDB` |
| ingest/API ficam em `aguardando o preparo do Postgres publicar a conexão` | o `<app>-setup` ainda não deu `OK` (ou foi apagado antes dele); ao esgotar os 5 min o ingest sai com código 2 e a API falha ao subir |
| `<app>-setup` termina com `ERRO:` | leia a mensagem/DICA nos logs; corrija a variável e use Save & Restart (§3) |
| ingest espera o `ca.crt` | broker ainda não gerou a CA no volume `<app>-pki` — veja os logs do broker |
| ingest sai com código 3 | Postgres inalcançável por 120 s (host/rede) |
| gateway: `certificate verify failed` | (8883) nome usado pelo gateway não está no SAN, ou `ca.crt` errado; (443) HTTPS do app `<app>-broker` ainda não ativado |
| gateway: falha de WebSocket / 502 | app `<app>-broker` sem *Websocket Support* / porta 9001, ou variável `RASTRO_MQTT_WEBSOCKETS=1` ausente |
| mapa mostra "Este endereço não usa HTTPS" | HTTPS não ativado no app web |
