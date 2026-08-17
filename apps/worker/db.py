"""All database access for the worker.

Kept in one module so the SQL is reviewable in a single place, and so the
processing modules stay pure and testable.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras


DSN = os.getenv("DATABASE_URL", "postgres://triage:triage@localhost:5432/triage")


@contextmanager
def connection():
    conn = psycopg2.connect(DSN)
    try:
        yield conn
    finally:
        conn.close()


def fetch_pending(conn, batch_size: int) -> list[dict[str, Any]]:
    """Claim a batch of unprocessed alerts.

    `FOR UPDATE SKIP LOCKED` is what makes it safe to run several worker
    replicas against the same table: each one takes a disjoint slice instead of
    fighting over the same rows.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            WITH claimed AS (
                SELECT id
                  FROM alerts
                 WHERE status = 'new'
                 ORDER BY received_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE alerts a
               SET status = 'processing'
              FROM claimed c
             WHERE a.id = c.id
            RETURNING a.id, a.source, a.raw_payload, a.received_at
            """,
            (batch_size,),
        )
        rows = cur.fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def save_classification(conn, alert_id: int, normalized: dict, cls) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE alerts
               SET normalized = %s::jsonb,
                   category = %s::alert_category,
                   severity = %s::severity_level,
                   classification_reason = %s,
                   status = 'classified',
                   processed_at = now()
             WHERE id = %s
            """,
            (
                json.dumps(normalized),
                cls.category,
                cls.severity,
                cls.reason,
                alert_id,
            ),
        )
    conn.commit()


def load_rules(conn) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, name, time_window_sec, match_fields
                 FROM correlation_rules
                WHERE enabled
                ORDER BY time_window_sec ASC"""
        )
        return [dict(r) for r in cur.fetchall()]


def find_matching_incident(
    conn, category: str, match_fields: list[str], key: str, window_sec: int
) -> int | None:
    """Look for an open incident whose alerts share this correlation key.

    The key is recomputed in SQL from each alert's normalized JSON so that the
    rule definition stays the single source of truth - change the rule row and
    behaviour changes without a redeploy.

    `category` is not part of the normalized JSON - the normalizer never sets
    it, only the classifier does - so it has to be read from the `alerts.category`
    column instead of `normalized->>'category'`, which would always be NULL.
    """
    segments = [
        "a.category::text" if f == "category" else f"COALESCE(a.normalized->>'{f}', '')"
        for f in match_fields
    ]
    key_expr = " || '|' || ".join(segments)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT i.id
              FROM incidents i
              JOIN alert_incident ai ON ai.incident_id = i.id
              JOIN alerts a          ON a.id = ai.alert_id
             WHERE i.status = 'open'
               AND i.category = %s::alert_category
               AND i.updated_at > now() - (%s || ' seconds')::interval
               AND ({key_expr}) = %s
             GROUP BY i.id
             ORDER BY i.updated_at DESC
             LIMIT 1
            """,
            (category, window_sec, key),
        )
        row = cur.fetchone()
    return row[0] if row else None


def attach_to_incident(conn, alert_id: int, incident_id: int) -> None:
    """Link an alert to an existing incident and bump its counters.

    Both writes plus the alert status change happen in one transaction: an
    alert must never be counted in an incident it is not linked to.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO alert_incident (alert_id, incident_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (alert_id, incident_id),
        )
        cur.execute(
            """UPDATE incidents
                  SET alert_count = alert_count + 1
                WHERE id = %s""",
            (incident_id,),
        )
        cur.execute(
            "UPDATE alerts SET status = 'correlated' WHERE id = %s", (alert_id,)
        )
    conn.commit()


def create_incident(
    conn, alert_id: int, title: str, summary: str, category: str, severity: str
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO incidents (title, summary, category, severity, alert_count)
               VALUES (%s, %s, %s::alert_category, %s::severity_level, 1)
               RETURNING id""",
            (title, summary, category, severity),
        )
        incident_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO alert_incident (alert_id, incident_id)
               VALUES (%s, %s)""",
            (alert_id, incident_id),
        )
        cur.execute(
            "UPDATE alerts SET status = 'correlated' WHERE id = %s", (alert_id,)
        )
    conn.commit()
    return incident_id


def escalate_incident(conn, incident_id: int, severity: str) -> None:
    """An incident is as severe as its worst alert, never less."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE incidents
                  SET severity = %s::severity_level
                WHERE id = %s
                  AND %s::severity_level < severity""",
            (severity, incident_id, severity),
        )
    conn.commit()


def mark_failed(conn, alert_id: int, reason: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE alerts
                  SET status = 'failed',
                      classification_reason = %s,
                      processed_at = now()
                WHERE id = %s""",
            (reason[:500], alert_id),
        )
    conn.commit()


def incident_alerts(conn, incident_id: int) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT normalized FROM alerts a
                 JOIN alert_incident ai ON ai.alert_id = a.id
                WHERE ai.incident_id = %s
                ORDER BY a.received_at
                LIMIT 20""",
            (incident_id,),
        )
        return [r["normalized"] for r in cur.fetchall() if r["normalized"]]
