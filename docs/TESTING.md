# Testing guide

Three layers, cheapest first. Layers 1 and 2 need no running stack.

---

## Layer 1. Pure-logic unit tests

No database, no network, no dependencies beyond the standard library.

```bash
cd apps/worker
python -m unittest discover -s tests -v
```

14 tests covering:

- Normalising a Prometheus/Alertmanager payload (nested `labels` and `annotations`)
- Normalising a flat Zabbix payload
- Case-insensitive field lookup (`HOST` and `host` both resolve)
- Missing fields producing `None` instead of a crash
- Synthesising a message when the source did not send one
- Correlation keys staying stable and not collapsing two different hosts
- The mock classifier landing on the right category for security, infra and network alerts
- A declared severity in the payload overriding keyword guessing
- Classification output always staying inside the enum, whatever the input

---

## Layer 2. Correlation SQL against a real database

This is the part worth checking after any schema or rule change. It runs the exact statements `apps/worker/db.py` issues, then asserts the resulting grouping. Everything happens inside a transaction that rolls back, so it is safe against a database with real data.

```bash
export DATABASE_URL=postgres://triage:triage@localhost:5432/triage
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/verify_correlation.sql
```

Expected output:

```
 id | category | severity | alert_count |         title
----+----------+----------+-------------+-----------------------
  1 | infra    | P1       |           3 | infra issue on web-01
  2 | infra    | P2       |           1 | auto: db-01
  3 | security | P1       |           1 | auto: web-01

 3 incidents | incident 1 groups 3 alerts | incident 1 escalated to P1 | all 5 alerts correlated | 5 links created
-------------+----------------------------+----------------------------+-------------------------+-----------------
 t           | t                          | t                          | t                       | t
```

Five alerts, three incidents. Three alerts on `web-01` in the `infra` category merge; the `db-01` alert stays separate because the host differs; the `web-01` security alert stays separate because the category differs. Incident 1 escalates from P2 to P1 when the critical alert joins.

---

## Layer 3. End-to-end through the HTTP API

### Start the stack

```bash
cp .env.example .env
docker compose up --build
```

Wait for `API listening on http://localhost:3000` and `worker starting :: provider=mock`.

### Option A - Postman

Import `postman/alert-triage-agent.postman_collection.json` and run the requests top to bottom.

| # | Request | What it proves |
|---|---|---|
| 1 | alert stats | API is up, database reachable |
| 2 | disk critical on `web-01` | Ingest works, alert opens an incident |
| 3 | memory warning on `web-01` | Same host and category, attaches to incident from #2 |
| 4 | prometheus nested payload | A different payload shape still normalises onto the same host |
| 5 | security alert on `web-01` | Same host, different category, opens its own incident |
| 6 | duplicate of #2 | Fingerprint dedup returns `duplicate`, worker never sees it |
| 7 | missing `payload` | Validation returns `400` |
| 8 | list incidents | Roughly two incidents from five alerts, worst severity first |
| 9 | one incident | Rolled-up alerts, each with its classification reason |
| 10 | summary | Aggregate counters |
| 11 | acknowledge | Status transition |

Requests 2 through 7 also carry assertions, so **Run collection** gives you a pass/fail report.

**Give the worker 3-5 seconds** between the ingest requests and request 8. Classification is asynchronous by design.

### Option B - curl

```bash
# 1. Send an alert
curl -X POST localhost:3000/api/alerts \
  -H 'content-type: application/json' \
  -d '{"source":"zabbix","payload":{"host":"web-01","severity":"critical","check":"DiskSpace","message":"disk usage on web-01 exceeded 95%"}}'
# -> {"id":1,"status":"accepted","fingerprint":"...","message":"alert accepted, queued for classification"}

# 2. Same host, same category, a few seconds later
curl -X POST localhost:3000/api/alerts \
  -H 'content-type: application/json' \
  -d '{"source":"zabbix","payload":{"host":"web-01","severity":"warning","check":"MemoryHigh","message":"memory usage elevated on web-01"}}'

# 3. Read back the grouped result
curl -s localhost:3000/api/incidents | python3 -m json.tool
# -> one incident, alert_count 2, severity P1
```

### Option C - bulk seed

```bash
python db/seed/send_alerts.py --count 200
```

Posts through the real endpoint, so it exercises validation, dedup and the guard. The seed uses a fixed random seed, so the same run always produces the same alerts.

### Watch it happen

```bash
docker compose logs -f worker
```

```
worker starting :: provider=mock batch=20 poll=3s
correlator :: alert 1 opened incident 1 (P1)
correlator :: alert 2 attached to incident 1 via rule 'same-host-same-category'
worker :: processed 2 alert(s), 2 total
```

---

## Testing against a real model

```bash
# .env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key
```

```bash
docker compose up -d --build worker
python db/seed/send_alerts.py --count 20
```

Compare `classification_reason` on the resulting alerts against a `mock` run. The mock says `keyword match on 'infra' (2 hits)`; a model explains itself in a sentence. Both stay inside the same enum, because `Classification.sanitized()` clamps the output either way.

---

## Resetting

```bash
docker compose down -v   # drops the volume, schema reapplies on next boot
docker compose up --build
```

Without Docker:

```bash
psql "$DATABASE_URL" -c "TRUNCATE alert_incident, incidents, alerts RESTART IDENTITY CASCADE;"
```

---

## Troubleshooting

**Dashboard shows "API unreachable".** The API container is not up. `docker compose logs api`.

**Alerts stay in `new` forever.** The worker is not running or cannot reach the database. `docker compose logs worker` - a healthy start prints `worker starting :: provider=mock`.

**Everything lands in one incident.** Expected if every alert shares a host and category. Vary `host` in the payload, or check `correlation_rules` - a rule matching on `category` alone groups aggressively by design.

**`401` on ingest.** `INGEST_API_KEY` is set in `.env` but the request has no matching `x-api-key` header. In Postman, enable the header in request 2 and fill the `apiKey` collection variable.

**Everything is classified `unknown` / `P4`.** With a real provider this usually means the API call is failing - the code degrades to `unknown` rather than crashing. Look for `call failed` in `classification_reason`.
