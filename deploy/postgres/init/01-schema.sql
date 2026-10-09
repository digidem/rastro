-- Rastro — schema v1 (sem PostGIS/TimescaleDB — decisão D4).
-- Coordenadas: graus×1e7 em INTEIROS (exatamente o que latitudeI/longitudeI entregam —
-- sem perda em ponto flutuante na escrita) + colunas double precision GERADAS para leitura.
-- Papéis e GRANTs: deploy/postgres/bootstrap-existing.sh (roda este arquivo no schema dedicado).

CREATE TABLE IF NOT EXISTS nodes (
    node_num      BIGINT PRIMARY KEY,              -- packet "from" (int)
    node_id       TEXT NOT NULL UNIQUE,            -- packet "fromId", ex. '!0badf00d' (a lib injeta; pode chegar atrasado)
    friendly_name TEXT,                            -- nome amigável opcional (arquivo de nomes da frota)
    fleet_id      TEXT,                            -- id do dispositivo no arquivo de nomes, quando houver
    hw_model      TEXT,                            -- opcional, do registry
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS positions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_num      BIGINT NOT NULL REFERENCES nodes(node_num),
    -- relógio do DISPOSITIVO na tomada do fix (packet decoded.position.time). FALLBACK:
    -- quando `time` está ausente, usa-se a hora de recebimento no gateway E registra-se
    -- time_source='gateway'.
    pos_time      TIMESTAMPTZ NOT NULL,
    time_source   TEXT NOT NULL DEFAULT 'device' CHECK (time_source IN ('device','gateway')),
    lat_i         BIGINT NOT NULL,                 -- graus × 1e7 (latitudeI)
    lon_i         BIGINT NOT NULL,                 -- graus × 1e7 (longitudeI)
    lat           DOUBLE PRECISION GENERATED ALWAYS AS (lat_i / 10000000.0) STORED,
    lon           DOUBLE PRECISION GENERATED ALWAYS AS (lon_i / 10000000.0) STORED,
    altitude_m    INTEGER,
    sats_in_view  INTEGER,
    hop_limit     SMALLINT,
    snr           REAL,
    rssi          INTEGER,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(), -- relógio do GATEWAY (independente do relógio do dispositivo)
    ingest_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- DEDUPE (D6): a ÚNICA restrição que torna replays QoS-1 e re-publicações do spool
    -- inofensivas, e que serve às consultas de trilha por nó (btree nó+tempo).
    CONSTRAINT positions_dedupe UNIQUE (node_num, pos_time),
    -- Sanidade de coordenadas: faixa válida e nunca "Null Island" (0,0) — defesa em
    -- profundidade; o filtro da ponte (Fase 3) também rejeita fix sem coordenadas.
    CONSTRAINT positions_coords_sane CHECK (
        lat_i BETWEEN -900000000 AND 900000000
        AND lon_i BETWEEN -1800000000 AND 1800000000
        AND NOT (lat_i = 0 AND lon_i = 0)
    )
);

-- Qualidade do fix do firmware (migração 03, aditiva e idempotente; anuláveis).
-- Mesmo bloco de deploy/postgres/migrate-03-qualidade.sh.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS pdop REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS hdop REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS ground_speed_ms REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS ground_track_deg REAL;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS precision_bits SMALLINT;

CREATE TABLE IF NOT EXISTS device_telemetry (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_num      BIGINT NOT NULL REFERENCES nodes(node_num),
    telem_time    TIMESTAMPTZ NOT NULL,            -- mesma regra de fallback de positions.pos_time
    time_source   TEXT NOT NULL DEFAULT 'device' CHECK (time_source IN ('device','gateway')),
    battery_level REAL,                            -- %  (device_metrics.battery_level)
    voltage       REAL,                            -- V  (device_metrics.voltage)
    channel_util  REAL,                            -- device_metrics.channel_utilization
    air_util_tx   REAL,                            -- device_metrics.air_util_tx
    uptime_s      INTEGER,                         -- device_metrics.uptime_seconds
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT telemetry_dedupe UNIQUE (node_num, telem_time)
);

-- Índices de tempo: BRIN — custo quase nulo em tabelas append-only, escolha certa antes
-- de qualquer particionamento (D4).
CREATE INDEX IF NOT EXISTS positions_pos_time_brin    ON positions       USING brin (pos_time);
CREATE INDEX IF NOT EXISTS positions_received_at_brin ON positions       USING brin (received_at);
CREATE INDEX IF NOT EXISTS telemetry_telem_time_brin  ON device_telemetry USING brin (telem_time);
-- Varreduras por nó ordenadas no tempo são servidas pelos btrees das UNIQUE — sem índice extra.

-- Superfície voltada ao monitor em v1 (o "monitor's screen" do TODO item 4 termina aqui).
-- Escolha documentada (plano §4 autoriza): LATERAL ... LIMIT 1 já garante uma linha por nó
-- com fix — DISTINCT ON é desnecessário; bateria vem da telemetria MAIS RECENTE via
-- LEFT JOIN LATERAL (nó sem telemetria continua aparecendo, battery NULL).
CREATE OR REPLACE VIEW vw_ultima_posicao AS
SELECT n.node_num,
       n.node_id,
       COALESCE(n.friendly_name, n.node_id) AS nome,
       p.pos_time, p.time_source, p.lat, p.lon,
       p.altitude_m, p.sats_in_view,
       b.battery,
       p.received_at
FROM nodes n
JOIN LATERAL (
    SELECT q.pos_time, q.time_source, q.lat, q.lon, q.altitude_m, q.sats_in_view, q.received_at
    FROM positions q
    WHERE q.node_num = n.node_num
    ORDER BY q.pos_time DESC
    LIMIT 1
) p ON true
LEFT JOIN LATERAL (
    SELECT t.battery_level AS battery
    FROM device_telemetry t
    WHERE t.node_num = n.node_num
    ORDER BY t.telem_time DESC
    LIMIT 1
) b ON true;
