# Development log

Kept so that any session - human or agent - can pick the project up without re-reading the whole tree.

## Status: MVP complete, not yet run end to end

| Area | State | Verified |
|---|---|---|
| PostgreSQL schema | done | yes, applied against PostgreSQL 16 |
| Correlation SQL | done | yes, `db/verify_correlation.sql`, 5 assertions pass |
| Worker pure logic | done | yes, 14 unit tests pass offline |
| NestJS API | written | **no** - npm registry was unreachable in the authoring sandbox |
| Python worker DB layer | written | **no** - psycopg2 could not be installed there |
| Dashboard | written | **no** - needs the API running |
| Docker Compose | written | **no** - no Docker daemon in the authoring sandbox |

**First thing to do on a machine with working npm, pip and Docker:**

```bash
cp .env.example .env
docker compose up --build
python db/seed/send_alerts.py --count 60
open http://localhost:3000
```

Expect a first run to surface small integration issues - a missing import, a container name, a path in the static file serving. That is the point of running it.

Known risk areas, in order of likelihood:

1. `ServeStaticModule` path. The build copies `public/` to `dist/public`; `app.module.ts` resolves `join(__dirname, 'public')`. If the dashboard 404s, check where `nest build` actually put the assets.
2. `exclude: ['/api/{*splat}']` uses the Express 5 wildcard syntax. On an older Nest/Express pairing this may need to be `'/api/(.*)'`.
3. The dynamic `key_expr` in `db.find_matching_incident` is string-built from `match_fields`. It is fed from the database, not from user input, but it is the one place worth a second look.

## Decisions taken

- **No ORM.** The JOIN that builds an incident view and the transaction that links an alert are the interesting parts; an ORM hides both.
- **PostgreSQL as the queue** via `FOR UPDATE SKIP LOCKED`. Correct for this volume, removes Redis and Kafka from the dependency list.
- **Offline mock LLM provider by default** so the repository runs with zero keys. Providers talk to REST APIs directly - no vendor SDKs.
- **Correlation rules stored as rows**, not code, so behaviour is tunable without a redeploy.
- **Dedup at ingest, correlation in the worker.** Dedup is a cheap fingerprint lookup and belongs on the hot path; correlation needs the classification result.
- **Dashboard served by the API.** One less host to run for the default setup.

## Next steps

1. Run the stack, fix whatever the first integration pass surfaces.
2. Push, confirm the three CI jobs go green.
3. Screenshot the dashboard with ~60 seeded alerts, add it to the README.
4. Then, optionally: Alertmanager webhook receiver, feedback loop, suppression windows.
