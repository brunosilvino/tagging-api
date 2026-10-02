CREATE TABLE IF NOT EXISTS map_collection (
    map_id TEXT NOT NULL,
    map_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT map_collection_map_event_key UNIQUE (map_id, map_version, event_id)
) PARTITION BY HASH (map_id);

CREATE TABLE IF NOT EXISTS map_collection_p0
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 0);
CREATE TABLE IF NOT EXISTS map_collection_p1
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 1);
CREATE TABLE IF NOT EXISTS map_collection_p2
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 2);
CREATE TABLE IF NOT EXISTS map_collection_p3
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 3);
CREATE TABLE IF NOT EXISTS map_collection_p4
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 4);
CREATE TABLE IF NOT EXISTS map_collection_p5
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 5);
CREATE TABLE IF NOT EXISTS map_collection_p6
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 6);
CREATE TABLE IF NOT EXISTS map_collection_p7
    PARTITION OF map_collection FOR VALUES WITH (MODULUS 8, REMAINDER 7);

CREATE INDEX IF NOT EXISTS map_collection_lookup_idx
    ON map_collection (map_id, map_version);
CREATE INDEX IF NOT EXISTS map_collection_name_idx
    ON map_collection (event_name);
CREATE INDEX IF NOT EXISTS map_collection_event_id_idx
    ON map_collection (event_id);
CREATE INDEX IF NOT EXISTS map_collection_params_idx
    ON map_collection USING GIN (params);

CREATE TABLE IF NOT EXISTS map_taxonomy (
    param_name TEXT PRIMARY KEY,
    data_type TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT false,
    pattern TEXT,
    description TEXT,
    pattern_error TEXT
);
