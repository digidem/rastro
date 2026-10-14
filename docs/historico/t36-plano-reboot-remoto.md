# T36 — Reiniciar/desligar remoto por nó (M2): plano

Regras aprovadas (H6, 2026-10-09): nós solares/fixos só reiniciam; desligar só com tripulação por perto; factory reset remoto nunca.

## Caminho do comando
API (`services/rastro_api`, endpoint autenticado) -> fila (tabela de saída, como `chat_outbox`) -> publicador (gateway virtual / `rastro-chat`) -> broker -> malha LoRa -> rádio (AdminMessage do Meshtastic: `reboot_seconds` / `shutdown_seconds`).

## O que já existe
- Fila de saída e publicador para texto (`chat_outbox`, `services/rastro_gateway/chat/outbox.py`).
- Cripto/envelope nativo (`native/crypto.py`, `native/envelope.py`).
- NOVO (esta execução): `services/rastro_gateway/native/remote_cmd.py` com modelo, validação e política (reboot/shutdown; factory reset recusado como `bad_action`; shutdown só em barco com tripulação por perto; desligado por padrão, `RASTRO_REMOTE_CMD_ENABLED=1`). Testes: `tests/test_native_remote_cmd.py`. Não envia nada.

Códigos de erro (`CommandError.code`), para a API futura mapear: `disabled`, `bad_node`, `bad_action`, `bad_crew`, `shutdown_denied_kind`, `shutdown_denied_crew`.

## O que falta
1. Tipo do nó (barco/solar/fixo) e "tripulação por perto": hoje não há fonte. Definir (registry da frota ou proximidade entre nós) e alimentar `NodeKind`/`crew_nearby` no servidor, nunca confiar no cliente.
2. Endpoint na API + autorização do operador + auditoria (quem, quando, nó, ação).
3. Codificação do AdminMessage e canal/chave de admin no rádio.
4. Publicação do comando e confirmação (ack/uptime após reboot).

## Depende do caminho de comando pela malha (tarefas 4/5, NÃO provado)
Itens 3 e 4: o rádio aceitar admin vindo do gateway virtual pela malha, com a ACL atual (cada barco só lê o tópico do próprio gateway virtual), exige prova em bancada. Qualquer mudança de MQTT/ACL/tópicos/derivação/esquema exige plano aprovado pelo dono (AGENTS.md seção 5). O item 1 e a API (item 2) podem andar sem hardware.
