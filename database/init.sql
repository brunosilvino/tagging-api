\set ON_ERROR_STOP on

BEGIN;

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

CREATE TEMP TABLE map_collection_import (
    map_version TEXT,
    map_id TEXT,
    event_name TEXT,
    page_path TEXT,
    title TEXT,
    section TEXT,
    component TEXT,
    label TEXT,
    outbound TEXT,
    event_id TEXT
);

\copy map_collection_import FROM 'map_collection.csv' WITH (FORMAT csv, HEADER true, DELIMITER ',', NULL '')

INSERT INTO map_collection (map_id, map_version, event_id, event_name, params)
SELECT
    map_id,
    map_version,
    event_id,
    event_name,
    jsonb_strip_nulls(jsonb_build_object(
        'page_path', NULLIF(page_path, ''),
        'title', NULLIF(title, ''),
        'section', NULLIF(section, ''),
        'component', NULLIF(component, ''),
        'label', NULLIF(label, ''),
        'outbound', NULLIF(outbound, '')::boolean
    ))
FROM map_collection_import
WHERE NULLIF(map_id, '') IS NOT NULL
  AND NULLIF(map_version, '') IS NOT NULL
  AND NULLIF(event_name, '') IS NOT NULL
  AND NULLIF(event_id, '') IS NOT NULL
ON CONFLICT (map_id, map_version, event_id) DO UPDATE SET
    event_name = EXCLUDED.event_name,
    params = EXCLUDED.params,
    created_at = now();

CREATE TEMP TABLE map_taxonomy_import (
    param_name TEXT,
    data_type TEXT,
    required TEXT,
    pattern TEXT,
    description TEXT,
    pattern_error TEXT
);

\copy map_taxonomy_import FROM 'map_taxonomy.csv' WITH (FORMAT csv, HEADER true, NULL '')

INSERT INTO map_taxonomy (param_name, data_type, required, pattern, description, pattern_error)
SELECT
    param_name,
    data_type,
    lower(required) = 'true',
    NULLIF(pattern, ''),
    NULLIF(description, ''),
    NULLIF(pattern_error, '')
FROM map_taxonomy_import
WHERE NULLIF(param_name, '') IS NOT NULL
ON CONFLICT (param_name) DO UPDATE SET
    data_type = EXCLUDED.data_type,
    required = EXCLUDED.required,
    pattern = EXCLUDED.pattern,
    description = EXCLUDED.description,
    pattern_error = EXCLUDED.pattern_error;

COMMIT;