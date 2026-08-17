# Development log

Kept so that any session - human or agent - can pick the project up without re-reading the whole tree.

## Status: verified end to end against a real Postgres, ready to deploy

| Area | State | Verified |
|---|---|---|
| PostgreSQL schema | done | yes, applied against a real PostgreSQL 16 instance |
| Correlation SQL | done | yes, `db/verify_correlation.sql` - all 5 assertions pass against real Postgres |
| Worker pure logic | done | yes, 14 unit tests pass offline (`python -m unittest discover -s tests -v`) |
| Worker DB layer (`db.py`) | done | yes - ran the real worker process against a real database, watched it classify and correlate live traffic |
| NestJS API | done | yes - ran `node dist/main.js` against a real database; ingest, dedup, validation, incident queries and status transitions all exercised over real HTTP |
| Dashboard | done | yes - confirmed visually (headless Chrome screenshot) showing real grouped incidents |
| Docker Compose | not run this session | no Docker in this sandbox; instead ran Postgres as a native Windows service and `api`/`worker` as plain local processes against it. Same schema, same code, same `DATABASE_URL` shape - `docker compose up --build` should behave identically, but hasn't been run literally |
| Public deployment (Render) | configured, not deployed | `render.yaml` + `docs/DEPLOYMENT.md` written; actual account/deploy click-through is a manual step for whoever owns the Render account |

**The two real bugs from the previous entry are now confirmed fixed, not just reasoned through:**

1. `apps/api/src/app.module.ts` - `exclude: ['/api/(.*)']` (was the Express-5-only `{*splat}` syntax, which the resolved `path-to-regexp@3.3.0` could never match). Confirmed live: `GET /api/incidents` etc. return real JSON, not the dashboard's `index.html`.
2. `apps/worker/db.py: find_matching_incident` - reads `a.category::text` instead of `a.normalized->>'category'` for the `category` match field (the normalizer never puts category into the JSON; only the classifier sets the separate column). Confirmed live: seeding 60 alerts produced correlator log lines like `alert 30 attached to incident 2 via rule 'category-wide-fallback'` - the exact rule this bug used to silently disable.

### How the live run went

Local setup (no Docker in this sandbox): installed PostgreSQL 16 as a native Windows service via `winget`, created the `triage`/`triage`/`triage` role and database (matching `.env.example` exactly, no config changes needed), applied `db/init/001_schema.sql`, then ran `db/verify_correlation.sql` for real - all 5 assertions passed. Then ran the built API (`node dist/main.js`) and the worker (`python main.py`) as plain background processes against that same database (both already default to `postgres://triage:triage@localhost:5432/triage` when `DATABASE_URL` is unset, so zero env configuration was needed).

Seeded 60 alerts via `db/seed/send_alerts.py`: 45 accepted, 15 deduplicated (expected - the seed's fixed random seed produces repeats). The worker correlated them down to **4 open incidents** (`alert_count` 7, 13, 13, 12 - summing to the 45 accepted), visible in a real screenshot of the dashboard. Spot-checked the rest of the HTTP surface: `POST /api/alerts` with no `payload` returns `400`; posting the same fingerprint twice returns `accepted` then `duplicate`; `PATCH /api/incidents/:id/status` transitions correctly.

## Decisions taken

- **No ORM.** The JOIN that builds an incident view and the transaction that links an alert are the interesting parts; an ORM hides both.
- **PostgreSQL as the queue** via `FOR UPDATE SKIP LOCKED`. Correct for this volume, removes Redis and Kafka from the dependency list.
- **Offline mock LLM provider by default** so the repository runs with zero keys. Providers talk to REST APIs directly - no vendor SDKs.
- **Correlation rules stored as rows**, not code, so behaviour is tunable without a redeploy.
- **Dedup at ingest, correlation in the worker.** Dedup is a cheap fingerprint lookup and belongs on the hot path; correlation needs the classification result.
- **Dashboard served by the API.** One less host to run for the default setup.
- **Render worker on a paid plan, not folded into the API process.** Render has no free tier for background workers (only Starter, $7/mo, and up). Keeping `api` and `worker` as separate deployed services matches the existing architecture and Dockerfiles exactly, at the cost of not being fully free to host. See `docs/DEPLOYMENT.md`.

## Next steps

1. **Test coverage gap: the API has zero unit tests** (`jest --passWithNoTests` - the script runs, but there is nothing to run). `AlertsService.deriveFingerprint`, the dedup query, and `IncidentsService.findAll`'s dynamic `WHERE`-clause builder are the parts most worth covering - they're the only non-trivial logic on the API side, and the kind of string-built SQL that's easy to get subtly wrong (see the correlation bug this session found on the worker side).
2. **No CI job runs the full pipeline.** The three CI jobs (worker unit tests, API build, schema-apply) each check a slice; none of them would have caught either bug fixed this session, because both were about how the pieces interact (Express route matching, worker SQL vs. worker DB writes), not any single piece in isolation. A fourth job that boots `docker compose`, runs the seed script, and asserts on `GET /api/incidents/summary` would close that gap and is exactly what this session did manually.
3. **Deploy the Render blueprint for real** (`render.yaml`, `docs/DEPLOYMENT.md`) - requires someone with dashboard access to click through account creation and blueprint deploy; not something a coding session can do unattended.
4. Then, optionally, the original roadmap: Alertmanager/Grafana webhook receivers, a feedback loop (human re-categorisation becomes a few-shot example), suppression windows, Prometheus metrics on triage latency.
