import json
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import Json, RealDictCursor


DATABASE_URL = os.environ.get("DATABASE_URL")
MAP_PARTITIONS = 8


@contextmanager
def connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(481123)")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS map_collection (
                    map_id TEXT NOT NULL,
                    map_version TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    params JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (map_id, map_version, event_id)
                ) PARTITION BY HASH (map_id)
            """)
            for partition in range(MAP_PARTITIONS):
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS map_collection_p{partition}
                    PARTITION OF map_collection
                    FOR VALUES WITH (MODULUS {MAP_PARTITIONS}, REMAINDER {partition})
                """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS map_taxonomy (
                    param_name TEXT PRIMARY KEY,
                    data_type TEXT NOT NULL,
                    required BOOLEAN NOT NULL DEFAULT false,
                    pattern TEXT,
                    description TEXT,
                    pattern_error TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS map_collection_lookup_idx ON map_collection (map_id, map_version)")
            cursor.execute("CREATE INDEX IF NOT EXISTS map_collection_name_idx ON map_collection (event_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS map_collection_event_id_idx ON map_collection (event_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS map_collection_params_idx ON map_collection USING GIN (params)")


def health_check():
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1


def replace_map(map_id, map_version, events):
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM map_collection WHERE map_id = %s AND map_version = %s",
                (str(map_id), str(map_version)),
            )
            for event_id, event in events.items():
                cursor.execute(
                    """
                    INSERT INTO map_collection (map_id, map_version, event_id, event_name, params)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (map_id, map_version, event_id) DO UPDATE SET
                        event_name = EXCLUDED.event_name,
                        params = EXCLUDED.params,
                        created_at = now()
                    """,
                    (
                        str(map_id),
                        str(map_version),
                        str(event_id),
                        event.get("event_name"),
                        Json(event.get("params", {})),
                    ),
                )


def _event(row):
    return {
        "event_id": row["event_id"],
        "metadata": {
            "map_id": row["map_id"],
            "map_version": row["map_version"],
        },
        "event_name": row["event_name"],
        "params": row["params"] or {},
    }


def get_event(event_id, map_id=None):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM map_collection WHERE event_id = %s"
            values = [str(event_id)]
            if map_id is not None:
                query += " AND map_id = %s"
                values.append(str(map_id))
            query += " ORDER BY created_at DESC LIMIT 1"
            cursor.execute(query, values)
            row = cursor.fetchone()
            return _event(row) if row else None


def get_map(map_id, map_version=None):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM map_collection WHERE map_id = %s"
            values = [str(map_id)]
            if map_version:
                query += " AND map_version = %s"
                values.append(str(map_version))
            else:
                query += " AND map_version = (SELECT MAX(map_version) FROM map_collection WHERE map_id = %s)"
                values.append(str(map_id))
            query += " ORDER BY event_id"
            cursor.execute(query, values)
            return [_event(row) for row in cursor.fetchall()]


def find_by_parameter(parameter_name, parameter_value):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM map_collection
                WHERE params @> %s::jsonb
                ORDER BY map_id, map_version, event_id
                """,
                (json.dumps({parameter_name: parameter_value}),),
            )
            return [_event(row) for row in cursor.fetchall()]


def find_by_event_name(event_name, map_id=None, map_version=None):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM map_collection WHERE event_name = %s"
            values = [event_name]
            if map_id is not None:
                query += " AND map_id = %s"
                values.append(str(map_id))
            if map_version is not None:
                query += " AND map_version = %s"
                values.append(str(map_version))
            query += " ORDER BY map_id, map_version, event_id"
            cursor.execute(query, values)
            return [_event(row) for row in cursor.fetchall()]


def clear_events():
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM map_collection")
            return cursor.rowcount


def taxonomy_rules():
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM map_taxonomy ORDER BY param_name")
            return cursor.fetchall()