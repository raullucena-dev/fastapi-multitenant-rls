# Deploying the public demo (Cloud Run + Neon)

This guide deploys the API as a public demo on **Google Cloud Run**, backed by
**Neon** (managed PostgreSQL 16, free tier). Isolation works exactly as locally: RLS with
`FORCE`, the `NULLIF` policies and the `SECURITY DEFINER` login lookup. The API connects
as the unprivileged `app_user`.

```mermaid
flowchart LR
    B[Browser] -->|same origin| FE[pos-demo-react<br/>nginx on Cloud Run]
    FE -->|/api/*| API[pos-api<br/>Cloud Run service]
    API -->|app_user, RLS| DB[(Neon<br/>PostgreSQL 16)]
    J1[Job: migrate] -->|app_owner| DB
    J2[Job: reset-demo] -->|app_owner| DB
    S[Cloud Scheduler<br/>nightly] --> J2
```

Placeholders used below: `PROJECT_ID`, `REGION=us-east1`. Run the commands in Cloud
Shell or anywhere with `gcloud` and `psql` (≥ 15).

**No real secret ever goes into a repository, an image or this file.** Values are
generated locally and stored only in Secret Manager.

## 0. Variables

```bash
export PROJECT_ID=raul-portfolio-demo      # your project
export REGION=us-east1
export REPO=portfolio                      # Artifact Registry repository
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/fastapi-multitenant-rls"
export TAG=$(git rev-parse --short HEAD)
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com
```

## 1. Database: roles and database on Neon

Neon's owner role (`neondb_owner`, a member of `neon_superuser`) has `CREATEROLE` and
`CREATEDB` but is **not** a superuser. `scripts/bootstrap_roles.sql` is written for
exactly that. It never mentions `SUPERUSER`/`BYPASSRLS` (a non-superuser may not), it
verifies the resulting role attributes, and it grants itself `SET` on `app_owner` so that
it can create a database owned by `app_owner`. It is idempotent and rotates the two
passwords on every run. CI runs the whole test suite against roles created this way (the
`managed` bootstrap variant).

```bash
# Owner connection string from the Neon console (direct, not "-pooler"), e.g.
# postgresql://neondb_owner:...@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
read -rs NEON_OWNER_URL && export NEON_OWNER_URL

export APP_OWNER_PASSWORD=$(openssl rand -base64 30 | tr -d '/+=')
export APP_USER_PASSWORD=$(openssl rand -base64 30 | tr -d '/+=')
export APP_DATABASES=app
psql "$NEON_OWNER_URL" -f scripts/bootstrap_roles.sql
```

The script creates `app_owner` (migrations and jobs), `app_user` (the API) and
`app_auth` (NOLOGIN, owns the login function), plus the database `app` owned by
`app_owner`. Neon requires strong passwords for roles created with SQL; the
`openssl rand` values above meet that.

Build the two SQLAlchemy URLs from the same host. Use the **direct** endpoint for both;
see the note on Neon's pooler at the end.

```bash
export DB_HOST=ep-xxx.us-east-1.aws.neon.tech      # host part of NEON_OWNER_URL
export DATABASE_URL="postgresql+psycopg://app_user:$APP_USER_PASSWORD@$DB_HOST/app?sslmode=require"
export MIGRATIONS_DATABASE_URL="postgresql+psycopg://app_owner:$APP_OWNER_PASSWORD@$DB_HOST/app?sslmode=require"
```

## 2. Secrets

```bash
printf %s "$DATABASE_URL"            | gcloud secrets create database-url --data-file=-
printf %s "$MIGRATIONS_DATABASE_URL" | gcloud secrets create migrations-database-url --data-file=-
openssl rand -base64 48 | tr -d '\n' | gcloud secrets create secret-key --data-file=-
unset NEON_OWNER_URL APP_OWNER_PASSWORD APP_USER_PASSWORD DATABASE_URL MIGRATIONS_DATABASE_URL
```

To rotate later, re-run the bootstrap with new passwords and add a new secret version
(`gcloud secrets versions add ...`).

Two service accounts keep least privilege. The API never sees the owner credentials.

```bash
gcloud iam service-accounts create pos-api --display-name "pos-api runtime"
gcloud iam service-accounts create pos-jobs --display-name "pos-api jobs (owner role)"
export API_SA="pos-api@$PROJECT_ID.iam.gserviceaccount.com"
export JOBS_SA="pos-jobs@$PROJECT_ID.iam.gserviceaccount.com"

for s in database-url secret-key; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member "serviceAccount:$API_SA" --role roles/secretmanager.secretAccessor
done
gcloud secrets add-iam-policy-binding migrations-database-url \
  --member "serviceAccount:$JOBS_SA" --role roles/secretmanager.secretAccessor
```

## 3. Build the image

```bash
gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
gcloud builds submit --tag "$IMAGE:$TAG"
```

## 4. Migrations as a one-off Cloud Run Job

The same image runs `alembic upgrade head` with `MIGRATIONS_DATABASE_URL`:

```bash
gcloud run jobs deploy migrate \
  --image "$IMAGE:$TAG" --region "$REGION" \
  --service-account "$JOBS_SA" \
  --set-secrets MIGRATIONS_DATABASE_URL=migrations-database-url:latest \
  --command alembic --args upgrade,head \
  --max-retries 0 --task-timeout 300
gcloud run jobs execute migrate --region "$REGION" --wait
```

For later releases, follow the rule from ADR 0005 and the postmortem: **deploy code
before a migration that restricts it**. For additive migrations, run `migrate` first and
then deploy the service.

## 5. The API service

```bash
gcloud run deploy pos-api \
  --image "$IMAGE:$TAG" --region "$REGION" \
  --service-account "$API_SA" \
  --set-secrets DATABASE_URL=database-url:latest,SECRET_KEY=secret-key:latest \
  --set-env-vars ENVIRONMENT=production,COOKIE_SECURE=true,DEMO_MODE=true,DB_POOL_SIZE=3,DB_MAX_OVERFLOW=2,LOG_LEVEL=INFO \
  --cpu 1 --memory 512Mi --concurrency 40 \
  --min-instances 0 --max-instances 2 \
  --allow-unauthenticated
export API_URL=$(gcloud run services describe pos-api --region "$REGION" --format 'value(status.url)')
```

- Cloud Run injects `PORT`. The image runs one uvicorn worker (`WEB_CONCURRENCY=1`),
  which suits 1 vCPU: sync endpoints already run in a thread pool, and the DB pool
  (3 + 2 overflow per instance, pre-ping, recycled every 300 s) caps concurrency. Two
  instances therefore open at most 10 connections to Neon.
- `ENVIRONMENT=production` refuses to start without `COOKIE_SECURE=true` and a real
  `SECRET_KEY`.
- The session cookie is `HttpOnly; Secure; SameSite=Lax`. The frontend reaches the API
  through its own nginx on the same origin, so no CORS configuration is needed.
- `--min-instances 0` scales to zero; the first request after idling takes 1–3 s.

## 6. Demo data: reset job and nightly schedule

```bash
gcloud run jobs deploy reset-demo \
  --image "$IMAGE:$TAG" --region "$REGION" \
  --service-account "$JOBS_SA" \
  --set-secrets MIGRATIONS_DATABASE_URL=migrations-database-url:latest \
  --set-env-vars DEMO_MODE=true \
  --command python --args=-m,app.cli,reset-demo \
  --max-retries 1 --task-timeout 600
gcloud run jobs execute reset-demo --region "$REGION" --wait
```

`reset-demo` wipes **every** tenant and reseeds "Demo Store North" and "Demo Store
South": 25 products each, stock history and two weeks of sales. It refuses to run unless
`DEMO_MODE=true`, so it cannot be pointed at real data by accident. The credentials it
creates are shown by `GET /demo/info` (only in demo mode).

Nightly trigger at 04:00 Paraguay time:

```bash
gcloud iam service-accounts create pos-scheduler --display-name "demo reset scheduler"
export SCHED_SA="pos-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
gcloud run jobs add-iam-policy-binding reset-demo --region "$REGION" \
  --member "serviceAccount:$SCHED_SA" --role roles/run.invoker

gcloud scheduler jobs create http reset-demo-nightly \
  --location "$REGION" \
  --schedule "0 4 * * *" --time-zone "America/Asuncion" \
  --http-method POST \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/reset-demo:run" \
  --oauth-service-account-email "$SCHED_SA"
```

## 7. Smoke test

```bash
curl -fsS "$API_URL/health"
curl -fsS "$API_URL/demo/info"
curl -fsS -c jar -H 'content-type: application/json' \
  -d '{"username":"north.cashier","password":"demo1234"}' "$API_URL/auth/login"
curl -fsS -b jar "$API_URL/products?limit=5"          # only Demo Store North
```

## Demo mode, in short

| Concern | Normal mode | `DEMO_MODE=true` |
| --- | --- | --- |
| Failed logins | Account locked for 15 min after 5 failures | Only the **client IP + username** is throttled, for 60 s (`DEMO_LOCKOUT_SECONDS`). The shared demo accounts can't be locked by visitors. |
| Rate limiting | Off (`RATE_LIMIT_ENABLED` to force it on) | Per IP: 10 logins/min, 60 writes/min (configurable) |
| `GET /demo/info` | 404 | Demo stores, usernames and password |
| `reset-demo` | Refuses to run | Wipes and reseeds |

Limits and throttles are kept **in memory, per instance**: with two instances, each keeps
its own counters. That is fine for a demo; strict global limits would need a shared store.

## Notes and limits

- **Client IP behind proxies.** uvicorn runs with `--proxy-headers` and
  `FORWARDED_ALLOW_IPS=*`, so the client address comes from `X-Forwarded-For`, which
  Cloud Run's front end sets. A client can prepend its own values to that header, so
  per-IP throttling is best-effort. It protects the demo accounts from casual lockout,
  not from a determined attacker. A production deployment should list its real proxy
  addresses in `FORWARDED_ALLOW_IPS`.
- **Neon pooler.** The API sets the tenant with `set_config(..., true)`, which is
  transaction-local and therefore compatible with PgBouncer transaction pooling. However,
  psycopg prepares repeated statements, and the migrations use `SET LOCAL ROLE`. The
  simplest correct setup is the **direct** endpoint with a small pool, as above.
- **Cost.** With scale-to-zero and Neon's free tier, the demo should stay at USD 0.
  Setting a USD 5 budget alert is still recommended.
