# Segurança e dados sensíveis

Modelo de ameaça e controles do Rastro. Premissa: **rastros de posição podem ser sensíveis** (monitoramento territorial, pessoas em campo). O Rastro mantém tudo em infraestrutura controlada por quem o opera; nada vai para mapas públicos, servidores MQTT públicos do Meshtastic ou serviços de terceiros.

## 1. Modelo de ameaça (resumo)

| Adversário | O que o Rastro impede |
|---|---|
| Observador na rede entre gateway e broker | ler posições em claro (TLS obrigatório, verificação sempre ligada) |
| Contêiner comprometido (ex.: web) | pular para o banco com privilégio de escrita |
| Credencial de um serviço vazada | ler ou apagar mais do que aquele serviço precisa (papéis mínimos) |
| Quem copia o repositório | obter senhas, chaves, certificados ou coordenadas (nada disso é versionado) |
| Acesso ao navegador depois do uso | reaproveitar o token (token só em memória; sessão por cookie `HttpOnly`) |
| Viewer servido em HTTP | enviar token ou cookie em claro (o viewer não envia credencial fora de HTTPS; a API recusa `/api` sem HTTPS quando `RASTRO_REQUIRE_HTTPS=1`) |

Fora de escopo: adversário com o rádio em mãos; host do gateway ou do servidor comprometido (quem administra o host administra os dados).

## 2. Controles

### Transporte
- **Broker TLS-only na 8883** — não existe listener 1883 nem websocket. CA própria gerada por `scripts/rastro_gen_certs.sh`, com a chave da CA fora de qualquer árvore git (`--out-dir`, padrão `~/.local/share/rastro/ca`).
- Clientes (gateway e ingest) sempre verificam o certificado do broker; não existe modo "TLS sem verificação".
- **ACL no broker** — `gateway` só publica, `ingest` só lê, sob o prefixo de tópico configurado; o resto é negado.

### Banco (papéis por instalação, prefixo = nome do banco)
- `<db>_owner` (sem login) é dono do schema dedicado `<db>`.
- `<db>_ingest`: só insere em `positions`/`device_telemetry` (SELECT apenas nas colunas do alvo de conflito — não lê rastros) e atualiza colunas específicas de `nodes`.
- `<db>_viewer`: só SELECT; a API abre sessão `read_only`.
- `<db>_maint` (retenção) e `<db>_backup` (`pg_dump`) separados.
- Senhas enviadas ao servidor como verificador SCRAM, nunca em texto puro.
- `CHECK` contra coordenada 0,0 e fora de faixa; `UNIQUE(node_num, pos_time)` deduplica reentregas.

### API e viewer
- Token Bearer comparado com `compare_digest`; sem token → 401. Checagem de HTTPS antes da de token.
- Token nunca persiste no navegador e nunca entra no bundle de produção.
- 401 limpa o estado e para o polling.
- CSP estrita no proxy (sem inline, sem terceiros); basemap e fontes servidos do mesmo domínio (tiles do OSM passam pela API autenticada, que não loga coordenadas de tile; ver docs/OPERACAO-caprover.md §4).

### Repositório
- `.gitignore` cobre `.env`, `passwd`, certificados, CA, backups e tiles.
- Coordenadas de teste ficam no oceano; ids de nó de teste são sintéticos.

### Operação
- Backup `pg_dump` só do schema dedicado, com `umask 077` e rotação.
- Retenção roda em dry-run por padrão; apagar exige confirmação explícita.
- Unit systemd do gateway endurecida (`ProtectSystem=strict`, `DeviceAllow` só para a serial, `StateDirectory` próprio).

## 3. CapRover

- Variáveis de ambiente do CapRover ficam em texto puro no diretório de dados dele e nos backups dele. Para o TLS do broker, prefira arquivos montados em `/mosquitto/secrets/`; os valores `RASTRO_TLS_*_B64` são a alternativa.
- Instalar o app já publica o viewer em `<app>.<domínio>`: ative HTTPS e "Force HTTPS" antes do primeiro acesso.
- A porta 8883 é publicada em todas as interfaces; libere no firewall só ela.
- Apps na mesma rede overlay do CapRover conseguem forjar `X-Forwarded-Proto`; o token continua exigido.

## 4. Ao reportar um incidente

Não coloque payload, coordenada, chave ou URL de canal em issue ou chat. Descreva o nó pelo nome curto, a janela de tempo e o sintoma.
