# Development log

Kept so that any session - human or agent - can pick the project up without re-reading the whole tree.

## Status: first integration pass done, real `docker compose` run still outstanding

| Area | State | Verified |
|---|---|---|
| PostgreSQL schema | done | yes, applied against PostgreSQL 16 |
| Correlation SQL | done | yes, `db/verify_correlation.sql` rewritten to match the `db.py` fix below; assertions reasoned through by hand, not re-run against a live Postgres (see note) |
| Worker pure logic | done | yes, 14 unit tests pass offline (`python -m unittest discover -s tests -v`) |
| Worker DB layer (`db.py`) | fixed | partially - installs and imports cleanly (`psycopg2-binary` builds fine), one real bug found and fixed by reading it against the schema and the harness (see below); never run against a live Postgres |
| NestJS API | fixed | partially - `npm install` and `npm run build` both succeed, one real bug found and fixed (see below); never booted against a live Postgres |
| Dashboard | written | **no** - needs the API running against a database |
| Docker Compose | written | **no** - this session's sandbox has no Docker, no WSL, and no `psql` on PATH, so `docker compose up --build` could not be executed here |

**This is still the first thing to do on a machine with working Docker:**

```bash
cp .env.example .env
docker compose up --build
python db/seed/send_alerts.py --count 60
open http://localhost:3000
```

The three risk areas below were the ones this log predicted. Two were real bugs and are now fixed; one turned out to already be correct. **None of this was confirmed against a live container run** - only by reading the compiled output, the resolved dependency versions, and the schema. Treat the fixes as reasoned-through, not battle-tested, until someone runs the stack for real.

1. ~~`ServeStaticModule` path.~~ **Not a bug.** Checked directly: `nest build` (per `nest-cli.json`'s `assets` config) copies `public/` to `dist/public`, and `dist/app.module.js`'s `join(__dirname, 'public')` resolves to that same `dist/public`. Confirmed by inspecting the actual `dist/` output after `npm run build`.
2. `exclude: ['/api/{*splat}']` used the Express 5 wildcard object syntax. **This was a real bug**: the API's resolved `express@4.22.1` pulls in `path-to-regexp@3.3.0`, which has no idea what `{*splat}` means and compiles it as a literal string - it can never match a real path, so nothing was ever excluded from the SPA catch-all. Confirmed with a standalone Node script calling the installed `path-to-regexp` directly. Fixed in `apps/api/src/app.module.ts` by switching to the v3-compatible `'/api/(.*)'`, which the same script confirmed does match `/api/alerts/` and friends.
3. The dynamic `key_expr` in `db.find_matching_incident`. **This was a real bug, and the more serious one.** `apps/worker/normalizer.py` never puts a `category` key into the `normalized` JSONB - category only exists as the separate `alerts.category` column, set later by the classifier. But `find_matching_incident` was reading `a.normalized->>'category'`, which is always `NULL`, while the Python-side key it's compared against (`correlator.py`) does include the real category. For the two rules that key on category (`same-host-same-category`, `category-wide-fallback` - two of three rules), the SQL-side key and the Python-side key could never match, so those rules could never attach an alert to an existing incident; every alert would open a new incident instead. `db/verify_correlation.sql` didn't catch this because its seed data hand-injected a fake `"category"` key straight into the `normalized` JSONB, which the real worker never does - the harness was testing a shape of data the pipeline can't actually produce. Fixed in `apps/worker/db.py` (read `a.category::text` instead of `a.normalized->>'category'` when a rule's match field is `category`) and updated `db/verify_correlation.sql` to seed `normalized` without a fake category key and to build its inline key the same way, so it now exercises the real bug instead of masking it. Assertions were re-checked by hand-tracing the five seeded alerts through the corrected query; still needs an actual `psql -f db/verify_correlation.sql` run to be sure.

**What still needs a real run**, in priority order:
1. `docker compose up --build`, watch both `api` and `worker` logs for anything that doesn't fit the picture above.
2. `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/verify_correlation.sql` - re-confirm the five assertions against real Postgres now that the query changed.
3. `python db/seed/send_alerts.py --count 60`, then check `http://localhost:3000` actually groups them into a handful of incidents (this is also the first real exercise of the `same-host-same-category` and `category-wide-fallback` rules against live data, since fix #3 above was never run, only reasoned through).

## Decisions taken

- **No ORM.** The JOIN that builds an incident view and the transaction that links an alert are the interesting parts; an ORM hides both.
- **PostgreSQL as the queue** via `FOR UPDATE SKIP LOCKED`. Correct for this volume, removes Redis and Kafka from the dependency list.
- **Offline mock LLM provider by default** so the repository runs with zero keys. Providers talk to REST APIs directly - no vendor SDKs.
- **Correlation rules stored as rows**, not code, so behaviour is tunable without a redeploy.
- **Dedup at ingest, correlation in the worker.** Dedup is a cheap fingerprint lookup and belongs on the hot path; correlation needs the classification result.
- **Dashboard served by the API.** One less host to run for the default setup.

## Next steps

1. Run the stack for real on a machine with Docker (this session's sandbox had none) and confirm the three fixes above actually hold up against a live Postgres.
2. Push, confirm the three CI jobs go green - the `schema` job in particular now exercises the rewritten `verify_correlation.sql`.
3. Screenshot the dashboard with ~60 seeded alerts, add it to the README.
4. Then, optionally: Alertmanager webhook receiver, feedback loop, suppression windows.
