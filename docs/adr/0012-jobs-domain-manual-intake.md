# ADR-0012 — Jobs Domain, Manual Intake & Normalization Foundation (WP2.1-A)

**Status:** Accepted  
**Date:** 2026-10-07  
**Decision:** D-054 (`docs/DECISIONS.md`)  

## Context

WP1.3 (ADR-0011, D-053) delivered Resume Import and is verified on `main`. WP2.1 is the first phase of Opportunity Discovery & Jobs Ingestion (`docs/DELIVERY_ORDER.md`, `docs/IMPLEMENTATION_PLAN.md`). Following architectural review and decision gate approval, WP2.1 was divided into two distinct checkpoints:
- **WP2.1-A:** Jobs Domain, Manual Intake & Normalization Foundation (this ADR);
- **WP2.1-B:** External Hosted ATS Connectors (Greenhouse, Lever, Ashby) & Ingestion Scheduler.

WP2.1-A covers two cohesive slices:
1. **A1:** Canonical Jobs Domain, Alembic Migration 0005, Manual Paste Intake (`POST /api/v1/jobs/intake/paste`), Deterministic Normalization, Provenance, and Job Details (`GET /api/v1/jobs/{jobId}`);
2. **A2:** Safe URL Intake (`POST /api/v1/jobs/intake/url`), `SafeHttpClient` SSRF security boundary, and HTML/JSON-LD structured extraction.

### Explicit Out-of-Scope Boundaries
- External hosted ATS connectors (Greenhouse, Lever, Ashby) and ingestion scheduler/Celery Beat (deferred to WP2.1-B);
- Opportunity Matching Engine (deferred to WP2.2);
- Public Jobs Feed (`GET /api/v1/jobs`) (deferred to WP2.3);
- WhatsApp conversational intake and mobile UI screens (deferred to respective channel checkpoints);
- Production database mutations or live deployments.

## Domain Model & Storage (Migration 0005)

### 1. Company Identity
A `Company` entity represents an employing organization:
- `id` (UUID PK);
- `name` (VARCHAR(255) NOT NULL);
- `name_normalized` (VARCHAR(255) NOT NULL, non-unique lookup index);
- `domain` (VARCHAR(255) NULL, canonical verified corporate domain only);
- `created_at`, `updated_at` (TIMESTAMPTZ NOT NULL DEFAULT `now()`).

**Invariants:**
- `Company.domain` is strictly the proven corporate apex/host (e.g. `acme.com`).
- ATS multi-tenant hostnames (such as `boards.greenhouse.io`, `jobs.lever.co`, `ashbyhq.com`, `jobs.workable.com`, `gupy.io`, `linkedin.com`, `indeed.com`) must NEVER become `Company.domain` and are sanitized to `NULL`.
- Strong identity is defined solely by exact normalized corporate domain when proven:
  ```sql
  CREATE UNIQUE INDEX uq_companies_domain
  ON companies (domain)
  WHERE domain IS NOT NULL;
  ```
- If domain is `NULL`, two companies with the same name coexist as distinct entities to prevent premature or erroneous merging.

### 2. Job Entity
A `Job` entity stores the current state of an opportunity:
- `id` (UUID PK);
- `company_id` (UUID FK NOT NULL -> `companies.id`);
- `created_by_user_id` (UUID FK NULL -> `users.id` ON DELETE SET NULL, tracks user ownership for manual intakes);
- `title` (VARCHAR(255) NOT NULL), `title_normalized` (VARCHAR(255) NOT NULL);
- `location_raw` (VARCHAR(255) NULL), `location_normalized` (VARCHAR(255) NULL);
- `workplace_type` (VARCHAR(32) NOT NULL, CHECK in `'remote'`, `'hybrid'`, `'on_site'`, `'unknown'`);
- `description_raw` (TEXT NOT NULL), `description_cleaned` (TEXT NOT NULL);
- `similarity_fingerprint` (CHAR(64) NOT NULL, non-unique index);
- `status` (VARCHAR(32) NOT NULL, CHECK in `'active'`, `'unavailable'`, `'closed'`, `'expired'`, `'unknown'`);
- `provenance_summary` (JSONB NOT NULL);
- `expires_at` (TIMESTAMPTZ NULL);
- `created_at`, `updated_at` (TIMESTAMPTZ NOT NULL DEFAULT `now()`).

**Ownership & Authorization Invariants:**
- Manual intakes (`manual_paste`, `manual_url`) are user-owned: `created_by_user_id` is set to the authenticated `user.id`.
- Future globally-discovered ATS jobs (WP2.1-B) may have `created_by_user_id = NULL`.
- `GET /api/v1/jobs/{jobId}` enforces owner scope: only the owner (`job.created_by_user_id == current_user.id`) can retrieve the job. Unauthorized/cross-user requests return HTTP 404 `JOB_NOT_FOUND` to prevent resource enumeration.

### 3. Job Origin
A `JobOrigin` entity captures a specific source observation:
- `id` (UUID PK);
- `job_id` (UUID FK NOT NULL -> `jobs.id` ON DELETE CASCADE);
- `provider` (VARCHAR(64) NOT NULL);
- `external_id` (VARCHAR(255) NULL);
- `source_url` (VARCHAR(2048) NULL), `canonical_url` (VARCHAR(2048) NULL);
- `observation_status` (VARCHAR(32) NOT NULL, CHECK in `'active'`, `'unavailable'`, `'closed'`, `'expired'`, `'unknown'`);
- `consecutive_not_found_count` (INTEGER NOT NULL DEFAULT 0);
- `first_observed_at`, `last_observed_at` (TIMESTAMPTZ NOT NULL DEFAULT `now()`);
- `provenance_metadata` (JSONB NOT NULL DEFAULT '{}');
- `created_at`, `updated_at` (TIMESTAMPTZ NOT NULL DEFAULT `now()`).

**Invariants:**
- `external_id` is nullable. It is never fabricated for `manual_paste` or `manual_url` intakes.
- Strong source identity applies only when `external_id` is present:
  ```sql
  CREATE UNIQUE INDEX uq_job_origins_provider_external_id
  ON job_origins (provider, external_id)
  WHERE external_id IS NOT NULL;
  ```
- Two manual paste origins with `external_id = NULL` safely coexist without constraint violation.

### 4. Status Consensus & Liveness
- Non-authoritative origins (`manual_paste` with observation status `unknown`) have zero weight for market liveness.
- If only `manual_paste` is linked, `Job.status = unknown`. This reflects that TapJob has not verified market availability, but does not block matching.
- When authoritative origins exist:
  - If at least one authoritative origin is `active` -> `Job.status = active`.
  - If all authoritative origins are `closed` -> `Job.status = closed`.
  - If all authoritative origins are `unavailable` -> `Job.status = unavailable`.
  - If `expires_at` has passed -> `Job.status = expired`.

### 5. Idempotency Model (ADR-0004 Realization)
To prevent duplicate resource creation and concurrent races:
- Table `idempotency_keys` with PK `(user_id, key, operation)`:
  - `status` (`PENDING` or `COMPLETED`);
  - `request_fingerprint` (CHAR(64) SHA-256);
  - `response_status` (INTEGER NULL), `response_body` (JSONB NULL);
  - `resource_id` (UUID NULL);
  - `reservation_token` (UUID NOT NULL, fencing token rotated on initial reservation and stale takeover);
  - `created_at`, `expires_at` (24-hour TTL).
- Check constraint `ck_idempotency_keys_status_coherence`:
  - `status = 'PENDING'` requires `response_status IS NULL AND (response_body IS NULL OR response_body = 'null'::jsonb)`.
  - `status = 'COMPLETED'` requires `response_status IS NOT NULL AND response_body IS NOT NULL AND response_body != 'null'::jsonb`.
- **Durable PENDING Reservation in Independent Transaction:**
  - `reserve_idempotency_key` executes in a short-lived, dedicated session that COMMITS `PENDING` with a fresh `reservation_token` to PostgreSQL before long network/ingestion operations begin.
  - This ensures concurrent requests immediately observe `PENDING` in PostgreSQL under standard Read Committed isolation and receive HTTP 409 `CONCURRENT_REQUEST_IN_PROGRESS` without blocking or waiting.
- **Fencing Token & Atomic Stale Takeover (`IDEMPOTENCY_PENDING_STALE_SECONDS = 120`):**
  - If a worker crashes or hangs while `PENDING`, any reservation older than 120s is treated as stale.
  - A new request performs an atomic conditional `UPDATE ... WHERE created_at < stale_threshold RETURNING reservation_token`, rotating `reservation_token` to a new UUID.
  - Exactly one concurrent caller wins takeover; losing callers detect the state and receive 409 or replay.
  - `complete_idempotency_key` and `release_pending_idempotency_key` strictly require the matching `reservation_token`. If ownership was overtaken, 0 rows are updated, raising `IdempotencyOwnershipLostError` and preventing an old owner from corrupting or releasing a new owner's reservation.
- **Crash-Gap Elimination via Atomic Single Business Commit:**
  - URL intake performs external network fetching and extraction BEFORE opening any business database transaction.
  - The business entity creation (`Company`, `Job`, `JobOrigin`) and idempotency transition to `COMPLETED` share the EXACT SAME database transaction and single `COMMIT`.
  - If the transaction fails, neither the Job nor the `COMPLETED` status persists. If it succeeds, both persist atomically. It is impossible for a persisted Job to exist alongside a still-`PENDING` reservation.
- **Release on Failure:**
  - In normal error cases occurring before the business commit, the endpoint releases the `PENDING` reservation with its `reservation_token` so clients can retry cleanly.
- Same key with mismatched payload returns HTTP 422 `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`.
- Completed request replays the stored response (HTTP 201).

## Deterministic Normalization & Text Discipline

All normalization is purely algorithmic and deterministic (no LLM, no external network calls):
1. **Text Cleanup:** HTML tags (scripts, styles, nav) stripped; entities unescaped; Unicode NFKC normalized; whitespace collapsed.
2. **Workplace Classification:** Keyword matching in pt-BR and English across title, location, and description (`remote`, `hybrid`, `on_site`, fallback `unknown`).
3. **Similarity Fingerprint:** SHA-256 composite of `company_id`, `title_normalized`, `location_normalized`, and description hash. The fingerprint is strictly non-unique telemetry/candidate signal and never triggers automated merging.
4. **Untrusted Input Security:** Prompt injection text is treated as inert content. It is preserved without evaluation or execution.

## Safe URL Intake & SSRF Defense (`SafeHttpClient`)

URL intake connects to external servers and is a critical security boundary:
1. **Pre-resolution DNS:** Resolves A and AAAA records upfront via `asyncio.get_running_loop().getaddrinfo`.
2. **IP Safety Validation:** Evaluates ALL returned addresses. If ANY address is non-global, private (RFC 1918), loopback, link-local, multicast, cloud metadata (`169.254.169.254`, `fd00:ec2::254`), CGNAT (`100.64.0.0/10`), or broadcast, the destination is rejected (`JobUrlDestinationForbiddenError`).
3. **Connection Pinning:** Pinned custom transport (`PinnedAsyncHTTPTransport`) forces socket connection directly to the pre-validated IP, completely eliminating DNS rebinding TOCTOU attacks.
4. **TLS SNI & Host Header Preservation:** Despite connecting to the pinned IP, the original hostname is preserved in the HTTP Host header and TLS SNI extension. Strict certificate verification remains permanently enabled (`verify=True`, `check_hostname=True`).
5. **Environment Proxy Isolation:** Configured with `trust_env=False`, completely ignoring ambient `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` variables.
6. **Manual Redirect Bounding:** Automatic redirects are disabled. Manual loop permits at most 3 redirects (`MAX_REDIRECTS=3`). Each hop undergoes scheme validation, DNS pre-resolution, IP allowlist checks, and connection pinning.
7. **Streaming Safety Bounds:** Enforces `MAX_JOB_URL_DECODED_BYTES = 2_097_152` (2 MiB) on decoded/uncompressed bytes. Decompression bombs (gzip/deflate/brotli) are aborted immediately upon stream expansion.
8. **Raw Body Retention:** Raw HTML is discarded immediately in memory after extraction (`DISCARD_AFTER_EXTRACTION`). No external HTML is persisted to the database or object storage.
9. **Dynamic Page Fallback:** JavaScript-only SPAs with no extractable body fail fast with HTTP 422 `JOB_PAGE_REQUIRES_MANUAL_PASTE`.

## Endpoints & Rate Limiting

- `POST /api/v1/jobs/intake/paste`: Authenticated, rate limited at 30 req/h per user.
- `POST /api/v1/jobs/intake/url`: Authenticated, rate limited at 15 req/h per user.
- `GET /api/v1/jobs/{jobId}`: Authenticated retrieval of normalized job representation.
- `AccountAuditEvent`: Recorded with allowlisted metadata (`job_id`, `company_id`, `provider`), strictly omitting raw job descriptions or sensitive tokens.

## Source Reuse Traceability

Reuse conforms to `docs/SOURCE_REUSE_LEDGER.md`:
- `wardoep/jobbot` (SHA `5932b3782412aeda4ed492df7c788b1248121ecb`): Text cleanup, role noise removal, workplace classification (ADAPT).
- `Gsync/jobsync` (SHA `ef3d7eec5c9aebaa1c6e03fc3ddd62d7dca8e37f`): Similarity fingerprint and candidate duplicate detection heuristics (PORT).
- `roysahar11/appliable` (SHA `2a900e892420039d228ceeb63f03b0480eb72fe4`): Dedupe test fixtures and validation cases (PORT).
