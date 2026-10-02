CREATE TEMP TABLE map_taxonomy_import (
    param_name TEXT,
    data_type TEXT,
    required TEXT,
    pattern TEXT,
    description TEXT,
    pattern_error TEXT
);

COPY map_taxonomy_import
FROM '/docker-entrypoint-initdb.d/map_taxonomy.csv'
WITH (FORMAT csv, HEADER true, NULL '');

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