-- Rastro — schema v2: ingest nativo Meshtastic + chat (WP-B).
-- Aditivo e estritamente idempotente (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- Executável múltiplas vezes sem erro e sem perda de dados.

-- 1. Extensões na tabela positions (rastreamento de pacote, gateway e integridade de tempo)
ALTER TABLE positions ADD COLUMN IF NOT EXISTS packet_id BIGINT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS gateway_num BIGINT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS time_flag TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ;

-- 2. Envelopes brutos recebidos via MQTT nativo (para reprocessamento / auditoria)
CREATE TABLE IF NOT EXISTS raw_envelopes (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic       TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT '',
    gateway_num BIGINT,
    from_num    BIGINT,
    packet_id   BIGINT,
    duplicate   BOOLEAN NOT NULL DEFAULT false,
    raw         BYTEA NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_envelopes_received_at_idx ON raw_envelopes (received_at);

-- 3. Registro de pacotes únicos vistos na malha (deduplicação entre gateways)
CREATE TABLE IF NOT EXISTS packet_seen (
    from_num      BIGINT NOT NULL,
    packet_id     BIGINT NOT NULL,
    first_gateway BIGINT,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_num, packet_id)
);

-- 4. Status de operação e contagem de uplinks por gateway
CREATE TABLE IF NOT EXISTS gateway_status (
    gateway_num BIGINT PRIMARY KEY,
    last_uplink TIMESTAMPTZ NOT NULL DEFAULT now(),
    uplinks     BIGINT NOT NULL DEFAULT 0
);

-- 5. Informações cadastrais e nomes dos nós da malha (NodeInfo)
CREATE TABLE IF NOT EXISTS node_info (
    node_num   BIGINT PRIMARY KEY,
    long_name  TEXT NOT NULL DEFAULT '',
    short_name TEXT NOT NULL DEFAULT '',
    hw_model   TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. Mensagens de texto / chat (com indicação de alerta / emergência)
CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    boat_id     TEXT,
    from_num    BIGINT NOT NULL,
    packet_id   BIGINT NOT NULL,
    text        TEXT NOT NULL,
    is_alert    BOOLEAN NOT NULL DEFAULT false,
    observed_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chat_messages_dedupe UNIQUE (from_num, packet_id)
);
CREATE INDEX IF NOT EXISTS chat_messages_received_at_idx ON chat_messages (received_at);

-- 7. Fila de saída (outbox) para envio de mensagens do escritório para os barcos
CREATE TABLE IF NOT EXISTS chat_outbox (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    boat_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sending', 'sent', 'expired', 'failed')),
    sent_at     TIMESTAMPTZ,
    packet_id   BIGINT,
    error       TEXT,
    -- Locação durável do claim (FIX-5): worker detém a linha por até 60 s
    lease_until TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS chat_outbox_status_idx ON chat_outbox (status, expires_at);

-- Locação do claim em instalações anteriores (aditivo e idempotente: FIX-5)
ALTER TABLE chat_outbox ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
-- Valor 'sending' em instalações anteriores ao lease: derruba e recria o CHECK
-- idêntico ao CREATE acima (idempotente — reexecutar recria o mesmo constraint).
ALTER TABLE chat_outbox DROP CONSTRAINT IF EXISTS chat_outbox_status_check;
ALTER TABLE chat_outbox ADD CONSTRAINT chat_outbox_status_check
    CHECK (status IN ('queued', 'sending', 'sent', 'expired', 'failed'));

-- 8. Cadastro de gateways virtuais e vínculos de dispositivos com barcos
CREATE TABLE IF NOT EXISTS virtual_gateways (
    boat_id          TEXT PRIMARY KEY,
    gateway_id       TEXT NOT NULL UNIQUE,
    virtual_node_num BIGINT NOT NULL,
    active           BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS boat_devices (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_num   BIGINT NOT NULL,
    boat_id    TEXT NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS boat_devices_node_idx ON boat_devices (node_num, valid_from, valid_to);

-- 9. Telemetria de energia dos nós (bateria e tensão)
CREATE TABLE IF NOT EXISTS node_power (
    node_num      BIGINT PRIMARY KEY,
    battery_level REAL,
    voltage       REAL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 10. Estado de alertas monitorados
CREATE TABLE IF NOT EXISTS alert_state (
    key           TEXT PRIMARY KEY,
    node_num      BIGINT,
    kind          TEXT NOT NULL,
    since         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_notified TIMESTAMPTZ,
    cleared_at    TIMESTAMPTZ
);

-- 10b. Resumo de atividade para o ciclo de alertas (posicao_parada etc.).
-- O papel ingest NÃO lê coordenadas (defesa em profundidade: ingest comprometido
-- não exfiltra trilhas). Esta função SECURITY DEFINER lê positions com o dono e
-- devolve só tempos e o DESLOCAMENTO (m, haversine) entre os 2 fixes mais recentes.
CREATE OR REPLACE FUNCTION node_activity_summary()
RETURNS TABLE (
    node_num BIGINT, first_seen DOUBLE PRECISION, last_seen DOUBLE PRECISION,
    fix_time DOUBLE PRECISION, prev_time DOUBLE PRECISION, displacement_m DOUBLE PRECISION
)
LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
    SELECT n.node_num,
           EXTRACT(EPOCH FROM n.first_seen)::double precision,
           EXTRACT(EPOCH FROM n.last_seen)::double precision,
           EXTRACT(EPOCH FROM u.pos_time)::double precision,
           EXTRACT(EPOCH FROM v.pos_time)::double precision,
           CASE WHEN v.pos_time IS NULL THEN NULL ELSE
             2 * 6371000.0 * asin(sqrt(
               power(sin(radians(u.lat - v.lat) / 2), 2)
               + cos(radians(v.lat)) * cos(radians(u.lat))
                 * power(sin(radians(u.lon - v.lon) / 2), 2)))
           END
    FROM nodes n
    LEFT JOIN LATERAL (
        SELECT p.lat, p.lon, p.pos_time FROM positions p
        WHERE p.node_num = n.node_num ORDER BY p.pos_time DESC LIMIT 1
    ) u ON true
    LEFT JOIN LATERAL (
        SELECT p.lat, p.lon, p.pos_time FROM positions p
        WHERE p.node_num = n.node_num AND p.pos_time < u.pos_time
        ORDER BY p.pos_time DESC LIMIT 1
    ) v ON true
$fn$;
DO $$
BEGIN
    EXECUTE format('ALTER FUNCTION node_activity_summary() SET search_path = pg_catalog, %I, pg_temp', current_schema());
END
$$;
REVOKE ALL ON FUNCTION node_activity_summary() FROM PUBLIC;

-- 11. Grants canônicos (mesmo conjunto dos scripts de deploy — manter idênticos).
-- Papéis: <db>_ingest / <db>_viewer / <db>_maint / <db>_backup, onde <db> = nome
-- do banco = nome do schema (convenção do bootstrap-existing.sh). Idempotente e
-- condicional à existência de cada papel; standalone (sem papéis) é no-op. O
-- bootstrap REVOCA tudo e reconcede exatamente este conjunto (seção 8).
-- CONFERÊNCIA CANÔNICA: os três caminhos de deploy concedem o MESMO conjunto —
-- conferido por tests/test_native_db.py::test_grants_identicos_entre_caminhos
-- (diff normalizado dos GRANTs dos três arquivos de deploy).
DO $$
DECLARE
    esquema     TEXT := current_schema();
    ingest_role TEXT := current_database() || '_ingest';
    viewer_role TEXT := current_database() || '_viewer';
    maint_role  TEXT := current_database() || '_maint';
    backup_role TEXT := current_database() || '_backup';
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ingest_role) THEN
        -- ingest: grava/deduplica/consulta (o ciclo de alertas lê nodes/positions)
        EXECUTE format('GRANT INSERT, SELECT, UPDATE ON %I.raw_envelopes, %I.packet_seen, %I.gateway_status, %I.node_info, %I.node_power, %I.chat_messages, %I.alert_state TO %I', esquema, esquema, esquema, esquema, esquema, esquema, esquema, ingest_role);
        -- Config do chat (gateways virtuais e vínculos de dispositivos): o processo de
        -- chat usa o papel ingest e semeia estas tabelas no boot a partir de
        -- RASTRO_NATIVE_NODES (tabelas de config, sem coordenadas — não abre posições).
        EXECUTE format('GRANT INSERT, SELECT, UPDATE ON %I.virtual_gateways, %I.boat_devices TO %I', esquema, esquema, ingest_role);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.boat_devices_id_seq TO %I', esquema, ingest_role);
        EXECUTE format('GRANT SELECT, UPDATE ON %I.chat_outbox TO %I', esquema, ingest_role);
        -- Sequências: IDENTITY exige USAGE (nextval) nos INSERTs com id gerado
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.raw_envelopes_id_seq, %I.chat_messages_id_seq TO %I', esquema, esquema, ingest_role);
        -- Suporte legado (mesmo papel grava legado + nativo): colunas que o ingest
        -- lê de fato (ON CONFLICT do legado / node_activity do ciclo de alertas)
        EXECUTE format('GRANT INSERT ON %I.positions, %I.device_telemetry, %I.nodes TO %I', esquema, esquema, esquema, ingest_role);
        EXECUTE format('GRANT UPDATE (node_id, friendly_name, fleet_id, last_seen, updated_at) ON %I.nodes TO %I', esquema, ingest_role);
        EXECUTE format('GRANT SELECT (node_num, pos_time) ON %I.positions TO %I', esquema, ingest_role);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %I.node_activity_summary() TO %I', esquema, ingest_role);
        EXECUTE format('GRANT SELECT (node_num, telem_time) ON %I.device_telemetry TO %I', esquema, ingest_role);
        EXECUTE format('GRANT SELECT (node_num, node_id, friendly_name, fleet_id) ON %I.nodes TO %I', esquema, ingest_role);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = viewer_role) THEN
        -- viewer: leitura das tabelas nativas + legado; INSERT limitado no outbox
        EXECUTE format('GRANT SELECT ON %I.chat_messages, %I.chat_outbox, %I.alert_state, %I.virtual_gateways, %I.node_info, %I.node_power, %I.gateway_status, %I.boat_devices TO %I', esquema, esquema, esquema, esquema, esquema, esquema, esquema, esquema, viewer_role);
        EXECUTE format('GRANT SELECT ON %I.nodes, %I.positions, %I.device_telemetry, %I.vw_ultima_posicao TO %I', esquema, esquema, esquema, esquema, viewer_role);
        EXECUTE format('GRANT INSERT (boat_id, text, created_by, expires_at) ON %I.chat_outbox TO %I', esquema, viewer_role);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.chat_outbox_id_seq TO %I', esquema, viewer_role);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = maint_role) THEN
        -- maint: retenção e limpeza (legado + nativo, idêntico ao bootstrap §8)
        EXECUTE format('GRANT SELECT, DELETE ON %I.nodes, %I.positions, %I.device_telemetry TO %I', esquema, esquema, esquema, maint_role);
        EXECUTE format('GRANT SELECT, DELETE ON %I.raw_envelopes, %I.packet_seen, %I.gateway_status, %I.node_info, %I.chat_messages, %I.chat_outbox, %I.virtual_gateways, %I.boat_devices, %I.node_power, %I.alert_state TO %I', esquema, esquema, esquema, esquema, esquema, esquema, esquema, esquema, esquema, esquema, maint_role);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = backup_role) THEN
        -- backup: leitura completa do schema (idêntico ao bootstrap §8)
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', esquema, backup_role);
        EXECUTE format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', esquema, backup_role);
    END IF;
END
$$;
