"""Turn vendor-specific alert payloads into one flat shape.

Every monitoring system names the same three things differently. Prometheus
calls a machine `instance`, Zabbix calls it `host`, a cloud provider calls it
`resourceId`. Normalisation happens here, once, so that neither the classifier
nor the correlator ever has to care where an alert came from.

Pure functions on purpose: no database, no network, fully unit-testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


# Ordered by preference: the first key present wins.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "host": ("host", "hostname", "instance", "node", "server", "resourceId"),
    "service": ("service", "app", "application", "job", "component", "namespace"),
    "message": ("message", "description", "summary", "text", "annotation", "body"),
    "check": ("check", "rule", "alertname", "title", "monitor", "eventType"),
    "severity": ("severity", "level", "priority", "urgency", "criticality"),
    "environment": ("environment", "env", "stage", "cluster"),
    "timestamp": ("timestamp", "time", "startsAt", "occurredAt", "eventTime", "@timestamp"),
}

NORMALIZED_KEYS = tuple(FIELD_ALIASES.keys())


def _first_present(payload: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]

    # Second pass, case-insensitive, so `Host` and `HOST` are not misses.
    lowered = {k.lower(): v for k, v in payload.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val not in (None, ""):
            return val
    return None


def _flatten(payload: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Lift nested dicts one level up so `labels.host` becomes reachable.

    Alertmanager and most cloud webhooks bury the useful fields inside
    `labels` or `annotations`. Depth is capped because alert payloads that go
    deeper than three levels are almost always noise.
    """
    flat: dict[str, Any] = {}
    if depth > 3:
        return flat

    for key, value in payload.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}.", depth + 1))
            # Also expose the leaf name unprefixed if nothing claimed it yet.
            for inner_key, inner_val in value.items():
                if not isinstance(inner_val, (dict, list)):
                    flat.setdefault(inner_key, inner_val)
        elif isinstance(value, list):
            flat[f"{prefix}{key}"] = ", ".join(str(v) for v in value[:10])
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def normalize(source: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Map an arbitrary payload onto the canonical alert shape."""
    flat = _flatten(raw_payload or {})
    flat.update({k: v for k, v in (raw_payload or {}).items()
                 if not isinstance(v, (dict, list))})

    normalized: dict[str, Any] = {"source": source}
    for field, aliases in FIELD_ALIASES.items():
        value = _first_present(flat, aliases)
        normalized[field] = str(value) if value is not None else None

    if not normalized["timestamp"]:
        normalized["timestamp"] = datetime.now(timezone.utc).isoformat()

    # A message is what a human reads first. Build one if the source did not.
    if not normalized["message"]:
        normalized["message"] = " ".join(
            str(p) for p in (normalized["check"], normalized["host"]) if p
        ) or "no message supplied"

    normalized["message"] = normalized["message"][:1000]
    return normalized


def correlation_key(normalized: dict[str, Any], match_fields: list[str]) -> str:
    """Build the grouping key a correlation rule compares on.

    A field that is missing becomes an empty segment rather than being skipped,
    so two alerts only group when they agree on *every* field the rule names.
    """
    return "|".join(str(normalized.get(f) or "") for f in match_fields)
