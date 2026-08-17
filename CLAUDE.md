# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Alert triage service: ingests raw monitoring alerts (Prometheus, Zabbix, Falco, arbitrary JSON), normalizes them, classifies each with an LLM, and correlates related alerts into incidents. NestJS API for ingestion/queries, Python worker for the classify/correlate loop, PostgreSQL for storage *and* as the work queue (`FOR UPDATE SKIP LOCKED`, no Redis/Kafka).

Ingestion (API) and classification (worker) are deliberately decoupled: `POST /api/alerts` just validates, dedups, and writes — it never blocks on a model call. The worker polls the `alerts` table for `status = 'new'` rows.

The whole pipeline has been run end to end against a real Postgres (API + worker + dashboard, not just unit tests) — see `docs/PROGRESS.md` for what was checked and how.

## Commands

```bash
# Run the full stack
cp .env.example .env
docker compose up --build
python db/seed/send_alerts.py --count 60   # generate synthetic alerts through the real API

# API (apps/api)
cd apps/api
npm ci                  # or npm install if package-lock.json is out of date
npm run start:dev       # nest start --watch
npm run build            # nest build
npm test                 # jest --passWithNoTests — there are currently no API unit tests (see Testing gaps below)

# Worker (apps/worker)
cd apps/worker
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v   # pure-logic tests: normalizer + classification, no DB/network
python -m unittest tests.test_pipeline -v # run a single test module

# Correlation SQL, against a real Postgres (asserts 3 incidents, one escalated P2->P1)
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/verify_correlation.sql
```

CI (`.github/workflows/ci.yml`) runs three independent jobs on every push: worker unit tests, API build, and schema-apply + correlation verification against a real Postgres service container. There's no single "run everything" command, and no CI job exercises the API and worker together against each other — that gap is called out in `docs/PROGRESS.md`'s next steps.

**Without Docker**: start a local Postgres, apply `db/init/001_schema.sql`, then run the API and worker directly as above. Both default `DATABASE_URL` to `postgres://triage:triage@localhost:5432/triage` when the env var is unset, so a Postgres instance with a `triage`/`triage`/`triage` role/password/database needs no further config on either side.

## Architecture

```
Monitoring source --POST /api/alerts--> NestJS API --write--> PostgreSQL
                                                                   |
                                                      claim batch (FOR UPDATE SKIP LOCKED)
                                                                   v
                                                            Python worker
                                                     normalize -> classify (LLM) -> correlate
                                                                   |
                                                          write incident links
                                                                   v
                                                     GET /api/incidents <-- PostgreSQL
```

- `apps/api/src/alerts/` — ingestion endpoint, fingerprint-based dedup (`DEDUP_WINDOW_SEC`), alert queries.
- `apps/api/src/incidents/` — read/query side: list, summary, single-incident view (assembles all linked alerts via `JOIN`), status transitions.
- `apps/api/src/common/db.module.ts` / `db.service.ts` — raw `pg` pool, no ORM.
- `apps/worker/main.py` — the poll loop: claim batch -> normalize -> classify -> correlate -> repeat. A single alert's failure is caught, logged, and marked `failed` in the DB; it never kills the loop.
- `apps/worker/normalizer.py` — maps wildly different vendor payload shapes into one normalized schema, and builds the "correlation key" used to group alerts. Note: the normalized JSON never contains a `category` key — category only ever exists as the separate `alerts.category` column, set by the classifier, not the normalizer. Code that rebuilds a correlation key outside Python (e.g. in SQL) has to read category from that column, not from JSON — getting this wrong is a real bug that shipped once (see `docs/PROGRESS.md`).
- `apps/worker/correlator.py` — the rule engine. Tries `correlation_rules` (from the DB) narrowest-time-window first; first rule that finds a matching open incident wins. If none match, opens a new incident and asks the LLM provider to title/summarize it.
- `apps/worker/llm/` — provider abstraction (`base.py: LLMProvider`). Every provider implements `classify()` and `summarize()` and returns the same shape (`Classification`, `(title, summary)`). `base.py: get_provider()` picks a provider from the `LLM_PROVIDER` env var (`mock` · `gemini` · `openai` · `anthropic`), re-exported via `llm/__init__.py`. Providers talk to vendor REST APIs directly — no vendor SDKs in `requirements.txt`.
- `db/init/001_schema.sql` — applied automatically on first container boot (mounted into `/docker-entrypoint-initdb.d`). Source of truth for the data model: `alerts`, `incidents`, `alert_incident` (join table), `correlation_rules`. A hosted Postgres (e.g. Render) has no equivalent auto-apply hook — see `docs/DEPLOYMENT.md`.
- `db/seed/send_alerts.py` — synthetic alert generator that posts through the real HTTP API (not direct DB inserts) so it exercises the full path.

### Correlation rules (data, not code)

Rules live in the `correlation_rules` table so behavior changes without a redeploy:

| Rule | Window | Matches on |
|---|---|---|
| `same-host-same-category` | 15 min | category + host |
| `same-service-any` | 30 min | service |
| `category-wide-fallback` | 10 min | category |

Two invariants worth knowing before touching this code:
- A missing field becomes an **empty key segment**, not a skip — otherwise two alerts each missing a *different* field would wrongly collapse into one incident.
- An incident's severity can only escalate (worst-alert-wins) — a P1 landing in a P3 incident bumps it to P1, never the reverse.

### Adding an LLM provider

Implement `classify(alert) -> Classification` and `summarize(alerts) -> (title, summary)` in a new file under `apps/worker/llm/`, subclassing `LLMProvider`, then register the choice in `get_provider()`. Use `LLMProvider._extract_json()` to pull JSON out of a model response that may be wrapped in prose or code fences, and `Classification.sanitized()` to clamp category/severity to the valid enums before trusting model output.

## Deploying

`render.yaml` deploys the existing Dockerfiles unchanged to Render: free API service, free Postgres, and a `starter` ($7/mo) worker — Render has no free background-worker tier. Full walkthrough including the one manual step (applying the schema once, since a managed Postgres has no `docker-entrypoint-initdb.d` equivalent) is in `docs/DEPLOYMENT.md`.

## Deliberate trade-offs (don't "fix" these)

- **No ORM anywhere.** Raw SQL in `apps/api/src/*/*.service.ts` and `apps/worker/db.py` is intentional — the incident-assembly `JOIN` and the alert-to-incident linking transaction are the interesting parts of this codebase and stay readable in plain SQL.
- **PostgreSQL as the queue**, via `SELECT ... FOR UPDATE SKIP LOCKED`. Correct for this volume and removes a dependency; not meant to scale past it.
- **`mock` is the default LLM provider** — a deterministic offline keyword classifier, not a model stand-in. The stack must run with zero API keys.
- **Dedup happens at ingest (API), correlation happens in the worker** — dedup is a cheap fingerprint check that belongs on the hot path; correlation needs the classification result first.
- **The API and worker are deployed as separate services**, even where that costs money (Render's worker tier). Folding them into one process would be a real architecture change, not a config one — see `docs/PROGRESS.md`'s "Decisions taken" if reconsidering this.

## Testing gaps (known, not yet closed)

- The API (`apps/api`) has no unit tests — `npm test` passes trivially (`--passWithNoTests`). `AlertsService`'s fingerprinting/dedup and `IncidentsService.findAll`'s dynamic `WHERE`-clause builder are the most valuable things to cover if adding tests here.
- No CI job runs the API and worker together against each other. The three existing jobs each test one piece in isolation; the two real bugs found and fixed in this project's history were both about how pieces interact (see `docs/PROGRESS.md`), and none of the existing CI jobs would have caught either one.
