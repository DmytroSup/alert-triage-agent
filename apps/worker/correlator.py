"""Decide whether an alert joins an existing incident or opens a new one.

The rule engine is deliberately small. Rules are tried narrowest window first,
so `same-host-same-category` (15 min) gets a chance before the catch-all
`category-wide-fallback` (10 min but only matches on category). The first rule
that finds an open incident wins.
"""

from __future__ import annotations

import logging
from typing import Any

import db
from normalizer import correlation_key

log = logging.getLogger("correlator")

SEVERITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


def is_more_severe(candidate: str, current: str) -> bool:
    return SEVERITY_ORDER.get(candidate, 4) < SEVERITY_ORDER.get(current, 4)


def correlate(conn, alert_id: int, normalized: dict[str, Any], cls, provider) -> dict:
    """Attach the alert to an incident, creating one if nothing matches.

    Returns a small dict describing what happened, which the caller logs.
    """
    rules = db.load_rules(conn)

    for rule in rules:
        match_fields = rule["match_fields"]
        if isinstance(match_fields, str):
            import json

            match_fields = json.loads(match_fields)

        # A rule that matches on a field this alert does not have would group
        # unrelated alerts under a shared empty key. Skip it instead.
        if any(not normalized.get(f) for f in match_fields if f != "category"):
            continue

        key = correlation_key(
            {**normalized, "category": cls.category}, match_fields
        )

        incident_id = db.find_matching_incident(
            conn, cls.category, match_fields, key, rule["time_window_sec"]
        )

        if incident_id:
            db.attach_to_incident(conn, alert_id, incident_id)
            db.escalate_incident(conn, incident_id, cls.severity)
            log.info(
                "alert %s attached to incident %s via rule '%s'",
                alert_id, incident_id, rule["name"],
            )
            return {
                "action": "attached",
                "incident_id": incident_id,
                "rule": rule["name"],
            }

    title, summary = provider.summarize(
        [{**normalized, "category": cls.category, "severity": cls.severity}]
    )
    incident_id = db.create_incident(
        conn, alert_id, title, summary, cls.category, cls.severity
    )
    log.info("alert %s opened incident %s (%s)", alert_id, incident_id, cls.severity)

    return {"action": "created", "incident_id": incident_id, "rule": None}
