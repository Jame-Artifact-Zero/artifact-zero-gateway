"""Read and write Artifact Zero memory buckets in PostgreSQL RDS."""

import json
import uuid

import db as database


def read(user_id: str, bucket_key: str) -> dict:
    """Read one memory bucket by user ID and bucket key."""
    conn = database.db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bucket_json
                FROM memory_buckets
                WHERE user_id = %s AND bucket_key = %s
                ORDER BY updated_at DESC
                LIMIT 1
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
                SELECT id
                FROM memory_buckets
                WHERE user_id = %s AND bucket_key = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, bucket_key),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    UPDATE memory_buckets
                    SET bucket_json = %s::jsonb, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (json.dumps(data or {}), row[0]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO memory_buckets (id, user_id, bucket_key, bucket_json, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, NOW())
                    """,
                    (str(uuid.uuid4()), user_id, bucket_key, json.dumps(data or {})),
                )
        conn.commit()
    finally:
        conn.close()
