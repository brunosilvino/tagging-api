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

COPY map_collection_import
FROM '/docker-entrypoint-initdb.d/map_collection.csv'
WITH (FORMAT csv, HEADER true, DELIMITER ',', NULL '');

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