# Deployment — TapJob

**Status:** Staging runtime contract defined & authorized for WhatsApp Live Gate (ADR-0016, D-058; companion to foundational ADR-0006, D-050).

This file is the operational companion to ADR-0006 and ADR-0016: the ADRs record *why* each decision was made; this file is the *how*, kept current as the contract evolves.

## 1. Topology

For local development and headless Compose runs:
- owner's Linux server, Docker Compose;
- `api`, `worker`, `migrate` (one shared backend image, D-043);
- PostgreSQL, Redis (same-host Compose services, no public port);
- Cloudflare Tunnel or Traefik reverse proxy;
- GitHub is canonical; GitHub Actions is CI/CD;
- no Kubernetes.

For the **WhatsApp Live Gate staging environment** (ADR-0016, D-058):
- **Runtime control plane:** EasyPanel (v2.36.0+) running on the owner's Linux server, managing isolated services in project `julia`;
- **Public ingress:** EasyPanel Traefik HTTPS ingress (with Let's Encrypt / automated TLS), routing to `tapjob-api` on internal container port `8000`. Staging domain: `https://tapjob-api-julia.gdxvyl.easypanel.host` (`GET /ready` returns HTTP 200 `{"status":"ok","service":"api","dependencies":{"postgresql":"ok","redis":"ok"}}`);
- **Services:** dedicated `tapjob-postgres` (PostgreSQL 16, persistent volume, daily local backup `0 0 * * *`), `tapjob-redis` (Redis 7, persistent volume, password protected), `tapjob-api`, `tapjob-worker` (Celery worker, concurrency=2), `tapjob-beat` (Celery beat, persistent volume mounted at `/data/celerybeat`, `--schedule=/data/celerybeat/celerybeat-schedule`), and one-shot `tapjob-migrate` (completed through Alembic head revision 0013, stopped with zero ongoing footprint);
- **Database Driver Contract:** backend runtime is synchronous SQLAlchemy and requires `DATABASE_URL` with `postgresql+psycopg://` scheme (psycopg 3). `postgresql+asyncpg://` is strictly prohibited for database connection strings;
- **Object Storage (R2):** bucket `tapjob-staging-resume-imports` (Cloudflare R2, private, Standard storage, bucket-scoped Object Read & Write credential, no public r2.dev exposure; smoke test PUT/GET/content-match/delete verified PASS with 0 remaining objects);
- **Integration boundary:** Evolution API runs in project `koglim` (`https://evo.koglim.com.br`), with networks unbridged. Outbound calls use HTTPS; inbound webhooks route via Traefik HTTPS to TapJob API (unconfigured/disabled in Evolution, fail-closed HTTP 404 in TapJob);
- **n8n:** strictly auxiliary and outside the synchronous messaging path.

## 2. Environments

`local` / `staging` / `production`. Staging and production never share a database, secrets, or
object-storage namespace. The same image **digest** is expected to cross both — only configuration
(the `--env-file` supplied at deploy time) differs per environment.

## 3. Build once, promote the same artifact

```text
commit reviewed
  -> CI (.github/workflows/ci.yml)
  -> build backend image ONCE (.github/workflows/release-image.yml, manual/workflow_dispatch)
  -> push to GHCR, capture the immutable digest (registry/repo@sha256:...)
  -> staging: docker compose -f infra/compose.deploy.yaml --env-file staging.env up
  -> verification (§5)
  -> production: the SAME digest, --env-file production.env
```

Never: `git pull` + `docker build` on the server. Never `:latest` as a release identity. Never a
different build per environment. The canonical repository (`ghcr.io/ricardo081683/tapjob-backend`)
is fixed directly in `infra/compose.deploy.yaml`'s image template, not read from an environment
variable (2026 review, Finding 2) — the only variable part an operator ever supplies is
`BACKEND_IMAGE_DIGEST`, a bare `sha256:<64 lowercase hex characters>` with no repository or tag
slot at all, so a mutable tag or a foreign repository is structurally impossible to substitute,
not merely discouraged by convention. `tools/deploy/validate_image_ref.mjs`'s `validateImageDigest`
is the small, deterministic gate for that value — run it (or let CI's `deploy-contract` job run
it, which also proves `api`/`worker`/`migrate` all resolve to the identical image) against any
`BACKEND_IMAGE_DIGEST` value before using it:

```bash
node tools/deploy/validate_image_ref.mjs --digest "$BACKEND_IMAGE_DIGEST"
```

### 3.1 Producing a release image (manual, not yet executed)

`.github/workflows/release-image.yml` is `workflow_dispatch`-only. To produce a release image:

1. From the GitHub Actions UI — `workflow_dispatch` only, no free-form ref input (2026 review,
   Finding 1): the workflow always builds whatever commit is at the tip of `main` and fails its
   own preflight check if triggered against any other ref, including via the API.
2. Read the run's step summary for the `registry/repo@sha256:...` reference it produced, and the
   bare `sha256:...` digest alone — that bare digest, verbatim, is `BACKEND_IMAGE_DIGEST` below. A
   commit-SHA tag is also pushed alongside, for human discovery only; it is never what a deploy
   references.

## 4. Deploying a digest to an environment

```bash
BACKEND_IMAGE_DIGEST=sha256:<digest> \
  docker compose -f infra/compose.deploy.yaml --env-file <environment>.env up --detach
```

This is the one canonical deploy command — `infra/compose.deploy.yaml`'s image template already
names the fixed canonical repository (`ghcr.io/ricardo081683/tapjob-backend`); `BACKEND_IMAGE_DIGEST`
supplies only the digest, never a repository or a tag (§3 above).

`<environment>.env` holds that environment's `APP_ENV`, `DATABASE_URL` (must use `postgresql+psycopg://`),
`REDIS_URL`, `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `R2_ENDPOINT_URL`/`R2_BUCKET`/
`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` (WP1.3, docs/adr/0011-resume-import.md "object storage"
— required in every environment this file describes; `infra/compose.deploy.yaml` itself refuses to
resolve without all four, and `tapjob_api.resume_import.storage.create_storage_adapter` fails fast
at process startup as a second, independent gate if they are ever unset or only partially set),
optional `SENTRY_DSN` — held by the operator, outside this repository, never committed (§7).

Worker concurrency is explicitly set to `--concurrency 2` in `infra/compose.deploy.yaml`.
Celery Beat uses persistent schedule storage on volume `beat-schedule-data` mounted at
`/data/celerybeat`, started with `--schedule=/data/celerybeat/celerybeat-schedule`.
Beat enforces least privilege by receiving only broker and runtime settings (`APP_ENV`, `LOG_LEVEL`, `REDIS_URL`, `SENTRY_DSN`), with no access to database, R2, or third-party messaging secrets.

Ordering is enforced by `infra/compose.deploy.yaml`'s own `depends_on`, not by operator discipline:
`migrate` must complete successfully on the given digest before `api`/`worker` start.

## 5. Acceptance / verification

Before considering a deploy to an environment complete:

- `migrate` exited `0` (Compose reports it `service_completed_successfully`, schema at Alembic head revision `0013`);
- `api`'s `/ready` reports healthy (PostgreSQL + Redis reachable, ADR-0002; verified internal HTTP 200 and external HTTPS 200);
- `worker` responds to `celery -A tapjob_worker.main:app inspect ping` (the same check
  `infra/compose.deploy.yaml`'s own healthcheck already runs);
- `beat` running with persistent scheduler without permission errors;
- R2 object storage verified with smoke test (PUT/GET/content-match/delete PASS);
- Webhook endpoint verified fail-closed (HTTP 404 RESOURCE_NOT_FOUND when `WHATSAPP_WEBHOOK_ENABLED=false`).

This is an infrastructure/readiness check, not a product smoke test — no product feature exists yet
for this checkpoint to validate.

## 6. Rollback

**Promote the previous known-good digest. Never rebuild old code.**

```bash
BACKEND_IMAGE_DIGEST=sha256:<previous-digest> \
  docker compose -f infra/compose.deploy.yaml --env-file <environment>.env up --detach
```

If the current database schema is incompatible with the previous digest's code (a migration moved
forward in a way the old code cannot handle), **do not** run an automatic `alembic downgrade`.
Treat this as a blocked rollback requiring a separate, explicitly reviewed decision — this is a
deliberate limitation (ADR-0006 §B7/B8), not an oversight: an automatic destructive downgrade is a
materially riskier default than a blocked rollback that waits for a human.

Record, per release: source commit SHA, image digest, environment, deployment timestamp, previous
digest, migration revision, and result — a pipeline run/step-summary record is sufficient; no new
database table is required for this.

## 7. Secrets

No secret lives in `infra/compose.deploy.yaml`, `.github/workflows/release-image.yml`,
`infra/docker/backend.Dockerfile`, a build arg, or an image label. Every environment-specific value
is a required (`:?`), unset-by-default environment variable an operator supplies via `--env-file`,
held outside this repository. `EXPO_PUBLIC_*` values remain public by convention
(`docs/ENGINEERING_STANDARDS.md` §3.3) and are unrelated to this backend deployment contract.

## 8. EasyPanel

Under ADR-0016 (D-058), EasyPanel is authorized as the **runtime/deployment control plane** on the owner's Linux server for staging (project `julia`).

May:
- manage service lifecycles (start, restart, stop, health monitoring);
- configure isolated service definitions (`tapjob-postgres`, `tapjob-redis`, `tapjob-api`, `tapjob-worker`, `tapjob-beat`, `tapjob-migrate`);
- manage Traefik ingress routing, TLS certificates, and domain mappings;
- execute one-shot migrations;
- inspect runtime logs and container metrics.

May **not**:
- be the source of truth for application code (images MUST come from GHCR immutable digests);
- build images from source on the server host;
- mutate existing non-TapJob services in project `julia` or any other project;
- bridge or attach project `julia` to project `koglim` networks;
- hold undocumented configuration that silently drifts from what this repository documents.

## 9. Ingress & Reverse Proxy (Traefik / Cloudflare)

Under ADR-0016 (D-058):
- **Staging (WhatsApp Live Gate):** EasyPanel Traefik HTTPS ingress is authorized as the public ingress path, terminating TLS via automated certificate resolvers (e.g. Let's Encrypt) and proxying to `tapjob-api` on internal container port `8000`. Staging domain is `https://tapjob-api-julia.gdxvyl.easypanel.host`. Cloudflare Tunnel is no longer mandatory for this staging environment.
- **Production / Custom DNS:** Cloudflare Tunnel and custom DNS remain approved ingress mechanisms when commercial production is provisioned.
- Under both ingress modes, container services (`api`, `postgres`, `redis`, `worker`) never publish raw host TCP ports.

## 10. Observability

Unchanged: structured JSON logs (`docs/ENGINEERING_STANDARDS.md` §18) carry, for
worker log lines, the TapJob correlation id plus Celery task id/root id/parent id/task name/retries
(`docs/adr/0006-worker-correlation-deployment-cd-contract.md` Part A) — never task `args`/`kwargs`.
Sentry remains the accepted technical-observability tool (D-028); nothing here changes that.

## 11. Staging Live Gate Boundaries

This deployment gate operates under strict constraints:
- `APP_ENV = staging` (never production);
- `WHATSAPP_REAL_SENDS = 0` (no real WhatsApp messages sent);
- `EVOLUTION_WEBHOOK_ENABLED = NO` (Evolution webhook remains unconfigured and disabled; `GET /webhook/find/TapJob` returns `null`);
- `WHATSAPP_WEBHOOK_ENABLED = false` in TapJob (`POST /webhooks/whatsapp` returns HTTP 404 fail-closed);
- `n8n` is strictly outside the synchronous messaging path;
- Hard blockers: GHCR pull auth (`ricardoo081`) and R2 object storage (`tapjob-staging-resume-imports`) verified before starting application workloads;
- `DATABASE_BACKUP_OFFSITE = NOT_CONFIGURED`: daily local backups configured on local disk destination; offsite backup replication is not configured for staging. This is not a blocker for the staging live canary, but is an explicit commercial-production readiness requirement before handling paid/customer production traffic.
