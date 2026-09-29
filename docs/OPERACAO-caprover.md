# Operação — Rastro no CapRover

Instalação do Rastro num CapRover que já tem um PostgreSQL, pelo app one-click `rastro` da loja digidem. O gateway (rádio na USB) continua fora do CapRover, na base.

```
Pi da base: rastro-gateway ──MQTT/TLS 8883──▶ <app>-broker ─▶ <app>-ingest ─▶ PostgreSQL existente
navegador ──HTTPS──▶ nginx do CapRover ─▶ <app> (web) ─▶ <app>-api ─▶ PostgreSQL existente
```

## 0. Antes de começar

- As imagens `communityfirst/rastro-*` da versão escolhida precisam estar **públicas no Docker Hub**: o CapRover sempre faz `pull` ao implantar (mesmo com a imagem já no servidor) e falha com `error from registry: denied` se não achar.
- Decida o **nome do app** (ex.: `rastro`). O certificado do broker depende dele; trocar depois exige gerar certificados de novo.
- Tenha um domínio para o MQTT (ex.: `mqtt.exemplo.org`) apontando para o servidor do CapRover.
- Veja o nome interno do seu PostgreSQL no CapRover (ex.: `srv-captain--postgres`). O nome `srv-captain--<app>` funciona em versões antigas e atuais do CapRover (as atuais também aceitam só `<app>`).
- Rastros de posição são sensíveis: a equipe decide quem tem acesso ao servidor e aos backups dele antes de instalar.

## 1. Adicionar a loja digidem (uma vez)

CapRover → **Apps** → **One-Click Apps/Databases** → role até **3rd party repositories** → cole a URL da loja digidem → **Connect New Repository**.

## 2. Preparar o PostgreSQL existente

O script cria um banco dedicado, um schema dedicado e cinco papéis por instalação (`<banco>_owner`, `_ingest`, `_viewer`, `_maint`, `_backup`). Ele é idempotente e recusa mexer num banco que não é do Rastro.

O Postgres do CapRover não tem porta pública, então rode o script por um contêiner na rede do CapRover, no servidor. Use a imagem da MESMA versão major do seu Postgres (`…-pg14`, `-pg15`, `-pg16`, `-pg17`; veja a versão com `SELECT version()`).

Senhas num arquivo, nunca na linha de comando (histórico do shell, `ps`):

```bash
umask 077
cat > rastro-bootstrap.env <<'EOF'
PGHOST=srv-captain--postgres
PGUSER=postgres
PGPASSWORD=<senha do admin>
RASTRO_DB=rastro
RASTRO_PG_PASSWORD_INGEST=<openssl rand -hex 24>
RASTRO_PG_PASSWORD_VIEWER=<openssl rand -hex 24>
RASTRO_PG_PASSWORD_MAINT=<openssl rand -hex 24>
RASTRO_PG_PASSWORD_BACKUP=<openssl rand -hex 24>
EOF
docker run --rm --network captain-overlay-network --env-file rastro-bootstrap.env \
  communityfirst/rastro-pgtools:<versão>-pg<major>
```

- Saída esperada na última linha: `OK: banco 'rastro' pronto (…)`.
- Senhas: só ASCII, 24+ caracteres; use `openssl rand -hex 24` (o formulário do app aceita só letras, números e `_ . ~ -`). Elas vão ao servidor só como verificador SCRAM. Guarde-as num gerenciador de senhas; as de ingest e viewer vão no formulário do app.
- Admin sem superusuário (Postgres gerenciado) funciona se tiver `CREATEROLE` e `CREATEDB`.
- Apague o `rastro-bootstrap.env` depois (`shred -u` ou `rm`).

## 3. Gerar os certificados do broker

Numa máquina da equipe (não no repositório):

```bash
scripts/rastro_gen_certs.sh --cert-dir ~/rastro-certs \
  --san DNS:mqtt.exemplo.org \
  --san DNS:srv-captain--rastro-broker --san DNS:rastro-broker \
  --b64
```

- A chave da CA fica em `~/.local/share/rastro/ca/` — guarde com a equipe; ela assina certificados novos.
- As linhas `RASTRO_TLS_*_B64` vão no formulário do app. A de `SERVER_KEY` é a chave privada do broker: copie direto para o CapRover, sem passar por chat ou e-mail.
- O `ca.crt` vai também para o gateway da base.
- Validade do certificado do servidor: 825 dias. Anote a data de renovação.

Alternativa mais segura que as variáveis B64: montar `ca.crt`, `server.crt` e `server.key` em `/mosquitto/secrets/` do app `<app>-broker` (dono uid 1883, modo 0400) e deixar vazios os campos do certificado e da chave do servidor. O campo da CA continua preenchido: o ingest usa. As variáveis de ambiente do CapRover ficam em texto puro no diretório de dados dele e nos backups.

## 4. Instalar o app

One-Click Apps → **Rastro** → preencha:

| Campo | Valor |
|---|---|
| App name | o nome escolhido no passo 0 |
| Versão | a tag publicada, sem `v` (ex.: `0.1.0`) — a mesma para as 4 imagens |
| Senhas MQTT | deixe as geradas; anote a do `gateway` |
| TLS B64 | as três linhas do passo 3 (ou só a da CA, se montar os arquivos do servidor) |
| PostgreSQL | host, porta, banco (`rastro`), usuários `rastro_ingest`/`rastro_viewer` e senhas do passo 2 |
| Token do mapa | deixe o gerado; é o que libera o mapa |

Depois de instalar:

1. **HTTPS primeiro.** App `<app>` → *HTTP Settings* → **Enable HTTPS** e **Force HTTPS**. A API responde 403 sem HTTPS e o visualizador não envia credenciais em HTTP.
2. **Firewall:** a porta `8883/TCP` fica aberta em todas as interfaces. Portas publicadas pelo Docker passam por fora das regras INPUT do ufw/iptables; para restringir origem, use o firewall do provedor ou regras em `DOCKER-USER`.
3. **Basemap:** copie o `.pmtiles` e a pasta `glyphs/` para o volume `<app>-tiles`:
   ```bash
   docker run --rm -v captain--<app>-tiles:/t -v "$PWD":/src:ro busybox cp -r /src/basemap.pmtiles /src/glyphs /t/
   ```
   (o nome exato do volume aparece em `docker volume ls`).
4. Conferir: `https://<app>.<seu-domínio>/api/healthz` → `{"status":"ok",…}`; o mapa pede o token.

## 5. Apontar o gateway da base

No host do gateway (ex.: Raspberry Pi), no arquivo de ambiente da unit `rastro-gateway`:

```
RASTRO_MQTT_HOST=mqtt.exemplo.org
RASTRO_MQTT_PORT=8883
RASTRO_MQTT_USERNAME=gateway
RASTRO_MQTT_PASSWORD=<senha "gateway" do app>
RASTRO_MQTT_CA_CERT=/etc/rastro/ca.crt
RASTRO_MQTT_TOPIC_PREFIX=rastro
```

Reinicie a unit. Enquanto o link de satélite cair, o gateway guarda tudo no spool em disco e reenvia na volta.

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

`rastro-backup.env` / `rastro-maint.env` (modo 600) têm só `RASTRO_PG_PASSWORD=` do papel correspondente. Se o CapRover já faz backup do Postgres inteiro, avalie se precisa deste.

## 7. Restaurar um backup

Num Postgres novo (ou depois de `DROP DATABASE rastro`), com a imagem `-pg<major>` do servidor de DESTINO (e a de origem não pode ser mais nova que ela). Use as MESMAS senhas do formulário do app no `rastro-bootstrap.env`:

```bash
docker run --rm --network captain-overlay-network --user "$(id -u):$(id -g)" \
  -v /srv/rastro-backups:/b:ro --env-file rastro-bootstrap.env \
  communityfirst/rastro-pgtools:<versão>-pg<major> --restore /b/rastro-AAAAMMDD.dump
```

`--user` com o seu uid: o dump é gravado com modo 600 e precisa ser legível pelo contêiner. A restauração NÃO migra o schema (o dump volta exatamente como foi feito): se o dump for de uma versão anterior do Rastro, rode o `rastro-pgtools` da versão atual de novo, SEM `--restore`, para aplicar a migração aditiva — senão o ingest sai com código 2 listando o que falta. O script recusa: banco com o schema já existente; dump de outro nome de banco (`RASTRO_DB` precisa ser igual ao da origem); versões fora da cadeia `origem ≤ pg_dump ≤ pg_restore ≤ servidor`; banco de outra instalação. A restauração é em transação única — se falhar, nada fica aplicado. Se o processo cair depois da restauração e antes dos GRANTs, rode o script de novo sem `--restore`.

## 8. Atualizar

Mude a versão (tag) nos 4 apps do CapRover, ou rode `caprover deploy -i communityfirst/rastro-<serviço>:<nova tag> -a <app>-<serviço>` para cada um. Mudanças de schema: rode o `rastro-pgtools` da nova versão sem `--restore` (a migração é aditiva e idempotente).

Atenção: cada execução do bootstrap REAPLICA as quatro senhas do arquivo. Use sempre as mesmas do formulário do app; senha diferente derruba o ingest e a API até o formulário ser atualizado.

## 9. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| broker sai com `ERRO: /mosquitto/data não é gravável` | volume antigo com dono errado: `chown -R 1883:1883` no volume |
| broker sai com `configuração TLS incompleta` | só uma ou duas das três variáveis B64 preenchidas |
| ingest sai com código 2 e `banco não está pronto` | bootstrap não rodou, ou banco/usuário errados no formulário (a mensagem lista o que falta e o search_path) |
| ingest sai com código 3 | Postgres inalcançável por 120 s (host/rede) |
| gateway: `certificate verify failed` | nome usado pelo gateway não está no SAN, ou `ca.crt` errado |
| mapa mostra "Este endereço não usa HTTPS" | HTTPS não ativado no app web |
