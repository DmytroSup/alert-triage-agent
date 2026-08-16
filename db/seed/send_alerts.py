#!/usr/bin/env python3
"""Push synthetic alerts through the real ingestion endpoint.

Uses the HTTP API rather than writing to the database directly, so running the
seed also exercises validation, fingerprint dedup and the guard - the same path
a real monitoring system would take. Standard library only.

    python db/seed/send_alerts.py --count 60
    python db/seed/send_alerts.py --count 200 --api http://localhost:3000
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


HOSTS = ["web-01", "web-02", "db-primary", "db-replica", "edge-gw-1", "k8s-node-3"]
SERVICES = ["checkout", "orders", "auth", "search", "billing"]

TEMPLATES = [
    # (source, severity, message template, extra fields)
    ("prometheus", "critical", "error rate above 5% on {service}", {"alertname": "HighErrorRate"}),
    ("prometheus", "warning", "response time p99 degraded on {service}", {"alertname": "SlowResponses"}),
    ("zabbix", "critical", "disk usage on {host} exceeded 95%", {"check": "DiskSpace"}),
    ("zabbix", "warning", "memory usage elevated on {host}", {"check": "MemoryHigh"}),
    ("zabbix", "critical", "host {host} is unreachable, complete outage", {"check": "HostDown"}),
    ("falco", "critical", "unauthorized privilege escalation detected on {host}", {"rule": "PrivEsc"}),
    ("falco", "error", "login attempt denied by firewall from unknown source", {"rule": "AuthDenied"}),
    ("netflow", "error", "packet loss and dns timeout on interface eth0", {"check": "LinkQuality"}),
    ("netflow", "warning", "bandwidth saturated on edge link", {"check": "Bandwidth"}),
    ("app-logs", "error", "unhandled exception, http 500 from {service} endpoint", {"alertname": "Http5xx"}),
    ("app-logs", "info", "deploy completed for {service}", {"alertname": "DeployDone"}),
]


def build_alert(rng: random.Random) -> dict:
    source, severity, template, extra = rng.choice(TEMPLATES)
    host = rng.choice(HOSTS)
    service = rng.choice(SERVICES)

    ts = datetime.now(timezone.utc) - timedelta(seconds=rng.randint(0, 600))

    # Alertmanager-style nesting for prometheus, flat for everything else, so
    # the normalizer gets exercised on both shapes.
    if source == "prometheus":
        payload = {
            "labels": {"instance": host, "job": service, **extra},
            "annotations": {"description": template.format(host=host, service=service)},
            "startsAt": ts.isoformat(),
            "severity": severity,
        }
    else:
        payload = {
            "host": host,
            "service": service,
            "severity": severity,
            "message": template.format(host=host, service=service),
            "timestamp": ts.isoformat(),
            **extra,
        }

    return {"source": source, "payload": payload}


def post(api: str, body: dict, api_key: str | None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/alerts",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"x-api-key": api_key} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.getenv("API_URL", "http://localhost:3000"))
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=42, help="fixed for reproducible demos")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    api_key = os.getenv("INGEST_API_KEY") or None

    accepted = duplicate = failed = 0
    for i in range(args.count):
        status, body = post(args.api, build_alert(rng), api_key)
        if status == 202 and body.get("status") == "accepted":
            accepted += 1
        elif status == 202:
            duplicate += 1
        else:
            failed += 1
            if failed <= 3:
                print(f"  ! {status} {body.get('error', body)}", file=sys.stderr)
        time.sleep(args.delay)

    print(f"sent {args.count}: {accepted} accepted, {duplicate} deduplicated, {failed} failed")
    if failed == args.count:
        print("nothing got through - is the API running on " + args.api + " ?", file=sys.stderr)
        return 1
    print("the worker picks these up within a few seconds; open " + args.api + " to watch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
