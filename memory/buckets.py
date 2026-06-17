"""Read and write Artifact Zero memory buckets in PostgreSQL RDS."""

import json

import db as database


def read(user_id: str, bucket_key: str) -> dict:
    """Read one memory bucket by user ID and bucket key."""
    conn = database.db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data
                FROM memory_buckets
                WHERE user_id = %s AND bucket_key = %s
                """,
                (user_id, bucket_key),
            )
            row = cur.fetchone()
            if not row:
                return {}
            data = row[0]
            if isinstance(data, str):
                return json.loads(data)
            return data or {}
    finally:
        conn.close()


def write(user_id: str, bucket_key: str, data: dict) -> None:
    """Upsert one memory bucket by user ID and bucket key."""
    conn = database.db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_buckets (user_id, bucket_key, data, updated_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (user_id, bucket_key)
                DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (user_id, bucket_key, json.dumps(data or {})),
            )
        conn.commit()
    finally:
        conn.close()
