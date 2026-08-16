"""Worker entry point.

Loop: claim a batch of new alerts, normalise, classify with the configured LLM
provider, correlate into incidents. PostgreSQL is the queue - one less moving
part than Redis or Kafka, and `FOR UPDATE SKIP LOCKED` makes it correct under
multiple replicas.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

import db
from correlator import correlate
from llm import get_provider
from normalizer import normalize


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-5s %(name)s :: %(message)s",
)
log = logging.getLogger("worker")

BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "20"))
POLL_INTERVAL = float(os.getenv("WORKER_POLL_SEC", "3"))

_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received, finishing current batch", signum)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def process_batch(conn, provider) -> int:
    alerts = db.fetch_pending(conn, BATCH_SIZE)
    if not alerts:
        return 0

    for alert in alerts:
        alert_id = alert["id"]
        try:
            normalized = normalize(alert["source"], alert["raw_payload"])
            cls = provider.classify(normalized)
            db.save_classification(conn, alert_id, normalized, cls)

            result = correlate(conn, alert_id, normalized, cls, provider)
            log.debug("alert %s -> %s", alert_id, result)

        except Exception as exc:  # noqa: BLE001 - a bad alert must not kill the loop
            log.exception("alert %s failed", alert_id)
            try:
                db.mark_failed(conn, alert_id, str(exc))
            except Exception:
                log.error("could not mark alert %s as failed", alert_id)

    return len(alerts)


def main() -> int:
    provider = get_provider()
    log.info(
        "worker starting :: provider=%s batch=%s poll=%ss",
        provider.name, BATCH_SIZE, POLL_INTERVAL,
    )

    # The API usually wins the race to the database on a cold `compose up`.
    for attempt in range(1, 31):
        try:
            with db.connection() as conn:
                conn.cursor().execute("SELECT 1")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("database not ready (%s/30): %s", attempt, exc)
            time.sleep(2)
    else:
        log.error("database never became reachable, giving up")
        return 1

    processed = 0
    with db.connection() as conn:
        while _running:
            try:
                count = process_batch(conn, provider)
                processed += count
                if count:
                    log.info("processed %s alert(s), %s total", count, processed)
                else:
                    time.sleep(POLL_INTERVAL)
            except Exception:  # noqa: BLE001
                log.exception("batch failed, backing off")
                time.sleep(POLL_INTERVAL * 2)

    log.info("worker stopped after %s alerts", processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
