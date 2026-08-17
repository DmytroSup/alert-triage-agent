# Deploying a public demo (Render)

`render.yaml` at the repo root is a [Render Blueprint](https://render.com/docs/blueprint-spec) that deploys the same three services as `docker-compose.yml` - the API, the worker, and Postgres - using the existing Dockerfiles unchanged. No code changes are needed to deploy; this file only adds the platform config.

**Cost**: the API and database are on Render's free plan. Render has no free tier for background workers, so the worker is pinned to `starter` (**$7/month**). See "Limits to know about" below before committing to this for anything beyond a demo.

---

## 1. Deploy the blueprint

1. Push this repo to GitHub if it isn't already (it is - `origin/main`).
2. In the [Render dashboard](https://dashboard.render.com), **New** -> **Blueprint**, and point it at this repository.
3. Render reads `render.yaml` and shows three resources to create: `alert-triage-db`, `alert-triage-api`, `alert-triage-worker`. Confirm and deploy.
4. You'll be prompted for the env vars marked `sync: false` - `INGEST_API_KEY` and the three LLM API keys. Leave them blank for the default no-key `mock` demo, exactly like running locally with an empty `.env`.

The first build takes a few minutes (Render builds both Dockerfiles from scratch). The worker will crash-loop until step 2 is done, because the database Render just created has no schema yet - that's expected.

## 2. Apply the schema once

Unlike `docker-compose.yml`, which mounts `db/init/` into `/docker-entrypoint-initdb.d` so Postgres applies it automatically on first boot, a Render-managed Postgres has no equivalent hook. Run it once, manually, against the new database:

1. On the `alert-triage-db` page in the Render dashboard, copy the **External Connection String**.
2. From your machine:
   ```bash
   psql "<external connection string>" -v ON_ERROR_STOP=1 -f db/init/001_schema.sql
   ```
3. Optionally sanity-check the correlation logic against the same rules used in CI:
   ```bash
   psql "<external connection string>" -v ON_ERROR_STOP=1 -f db/verify_correlation.sql
   ```
   This runs inside a transaction that rolls back, so it's safe to run even after real data exists.

Once the schema is applied, the worker service (which was crash-looping) recovers on its next restart - Render restarts a crashed background worker automatically.

## 3. Verify

```bash
curl https://alert-triage-api.onrender.com/api/alerts/stats
# -> {"total":"0","pending":"0","processed":"0","failed":"0"}

python db/seed/send_alerts.py --api https://alert-triage-api.onrender.com --count 60
```

Then open `https://alert-triage-api.onrender.com` - same single-page dashboard as local, served the same way (the "no separate frontend host" trade-off in the README holds in production too).

Your actual service URLs are whatever Render assigned them (visible on each service's dashboard page); `alert-triage-api.onrender.com` above assumes the default naming from `render.yaml`.

## Limits to know about

- **Free Postgres expires 30 days after creation**, with a 14-day grace period to upgrade before the data is deleted. Fine for a demo you'll refresh periodically; not for anything meant to stay up unattended. A cheap `basic-256mb` plan removes the expiry.
- **The free API service spins down after 15 minutes of no traffic** and cold-starts (a several-second delay) on the next request. The worker, being on a paid plan, does not spin down.
- **Free Postgres storage is capped at 1GB.** The seed script and normal demo traffic stay well under this; a sustained high-volume ingest would not.

## Updating the deployment

Render Blueprints auto-deploy on every push to the branch they were created from (`main`), for both the API and worker services, same as the `api-build` and `worker-tests` CI jobs already run on every push. A schema change still needs its migration run manually against the hosted database, the same as step 2 above.
