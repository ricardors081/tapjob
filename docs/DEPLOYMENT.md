# Deployment — TapJob

**Status:** Contract defined (WP0.2-B Checkpoint B, `docs/adr/0006-worker-correlation-deployment-cd-contract.md`, D-050). No environment has been deployed to by this checkpoint — this is the reproducible contract a future operator follows, not a record of a deployment that happened.

This file is the operational companion to ADR-0006: the ADR records *why* each decision was made; this file is the *how*, kept current as the contract evolves.

## 1. Topology

Unchanged from `docs/ARCHITECTURE.md` §11 and `docs/FOUNDATION_DECISIONS.md` §2:

- owner's Linux server, Docker Compose;
- `api`, `worker`, `migrate` (one shared backend image, D-043);
- PostgreSQL, Redis (same-host Compose services, no public port);
- Cloudflare Tunnel as the only public ingress;
- EasyPanel as an operations/visualization UI only (§6 below);
- GitHub is canonical; GitHub Actions is CI/CD;
- no Kubernetes.

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

`<environment>.env` holds that environment's `APP_ENV`, `DATABASE_URL`, `REDIS_URL`,
`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, `R2_ENDPOINT_URL`/`R2_BUCKET`/
`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` (WP1.3, docs/adr/0011-resume-import.md "object storage"
— required in every environment this file describes; `infra/compose.deploy.yaml` itself refuses to
resolve without all four, and `tapjob_api.resume_import.storage.create_storage_adapter` fails fast
at process startup as a second, independent gate if they are ever unset or only partially set),
optional `SENTRY_DSN` — held by the operator, outside this repository, never committed (§7).

Ordering is enforced by `infra/compose.deploy.yaml`'s own `depends_on`, not by operator discipline:
`migrate` must complete successfully on the given digest before `api`/`worker` start.

## 5. Acceptance / verification

Before considering a deploy to an environment complete:

- `migrate` exited `0` (Compose reports it `service_completed_successfully`);
- `api`'s `/ready` reports healthy (PostgreSQL + Redis reachable, ADR-0002);
- `worker` responds to `celery -A tapjob_worker.main:app inspect ping` (the same check
  `infra/compose.deploy.yaml`'s own healthcheck already runs).

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

May: visualize running containers, assist day-to-day operations, start/restart a service per a
future runbook addition.

May **not**: be the source of truth for configuration; be the only place holding Compose/env
values; be a build location; hold configuration that silently drifts from what this repository
documents. If `infra/compose.deploy.yaml` plus an environment's `--env-file` are not, by
themselves, sufficient to reproduce what is running, that is a contract violation to fix, not an
acceptable operational shortcut.

## 9. Cloudflare

Cloudflare Tunnel remains the only approved public ingress path. No tunnel is configured, no DNS is
changed, and no Cloudflare account/resource is touched by this checkpoint — `api` in
`infra/compose.deploy.yaml` simply never publishes a host port, which is the repository-side half
of this boundary. Configuring the tunnel itself is a separate, later operator action.

## 10. Observability

Unchanged: structured JSON logs (`docs/ENGINEERING_STANDARDS.md` §18) now additionally carry, for
worker log lines, the TapJob correlation id plus Celery task id/root id/parent id/task name/retries
(`docs/adr/0006-worker-correlation-deployment-cd-contract.md` Part A) — never task `args`/`kwargs`.
Sentry remains the accepted technical-observability tool (D-028); nothing here changes that.

## 11. What this checkpoint explicitly does NOT do

No real deploy. No SSH to the server. No EasyPanel configuration change. No Cloudflare DNS/tunnel
change. No GHCR push (the release workflow exists but has not been run). No production/staging
environment setup. No backup implementation. No R2 implementation. No Kubernetes/Terraform/Ansible/
Helm/AWS. No product endpoint, database schema, or product Celery task. No idempotency-key
middleware. WP1.x remains **not authorized**.
