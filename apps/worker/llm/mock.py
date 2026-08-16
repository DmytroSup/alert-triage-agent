"""Deterministic offline provider.

This exists so that `docker compose up` produces a working demo with no API
key and no network. It is a keyword classifier, not a model - and the README
says so plainly. Anyone evaluating the repository can see the full pipeline
run end to end, then point LLM_PROVIDER at a real model to compare.
"""

from __future__ import annotations

from typing import Any

from .base import Classification, LLMProvider


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": (
        "unauthorized", "auth", "login", "breach", "intrusion", "malware",
        "firewall", "denied", "privilege", "certificate", "tls", "cve",
    ),
    "network": (
        "packet", "latency", "timeout", "unreachable", "dns", "bgp", "link",
        "bandwidth", "interface", "route",
    ),
    "infra": (
        "cpu", "memory", "disk", "node", "host", "container", "pod", "reboot",
        "temperature", "power", "storage", "swap",
    ),
    "application": (
        "exception", "http 5", "500", "502", "503", "error rate", "queue",
        "deploy", "endpoint", "response time", "database", "query",
    ),
}

SEVERITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "P1": ("outage", "down", "critical", "unreachable", "breach", "data loss"),
    "P2": ("degraded", "high", "saturated", "failing", "error rate", "denied"),
    "P3": ("warning", "elevated", "slow", "retry", "threshold"),
}


class MockProvider(LLMProvider):
    name = "mock"

    def classify(self, alert: dict[str, Any]) -> Classification:
        haystack = " ".join(
            str(v) for v in alert.values() if isinstance(v, (str, int, float))
        ).lower()

        category, hits = "unknown", 0
        for cat, words in CATEGORY_KEYWORDS.items():
            score = sum(1 for w in words if w in haystack)
            if score > hits:
                category, hits = cat, score

        severity = "P4"
        for sev, words in SEVERITY_KEYWORDS.items():
            if any(w in haystack for w in words):
                severity = sev
                break

        # An explicit severity in the payload always wins over guessing.
        declared = str(alert.get("severity", "")).lower()
        if declared in ("critical", "crit", "fatal"):
            severity = "P1"
        elif declared in ("error", "major"):
            severity = "P2"
        elif declared in ("warning", "warn", "minor"):
            severity = "P3"
        elif declared in ("info", "informational", "debug"):
            severity = "P4"

        reason = (
            f"keyword match on '{category}' ({hits} hits), "
            f"severity from {'payload' if declared else 'keywords'}"
        )
        return Classification(category, severity, reason).sanitized()

    def summarize(self, alerts: list[dict[str, Any]]) -> tuple[str, str]:
        first = alerts[0] if alerts else {}
        host = first.get("host") or first.get("service") or "unknown target"
        category = first.get("category", "unknown")
        count = len(alerts)

        title = f"{category} issue on {host}"[:80]
        summary = (
            f"{count} correlated alert(s) from {host}, first seen "
            f"{first.get('timestamp', 'recently')}. "
            f"Primary signal: {first.get('message', 'no message')}."
        )
        return title, summary[:800]
