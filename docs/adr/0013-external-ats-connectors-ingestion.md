# ADR-0013 — External Hosted ATS Connectors & Ingestion Scheduler (WP2.1-B)

**Status:** Accepted  
**Date:** 2026-10-07  
**Decision:** D-055 (`docs/DECISIONS.md`)  

## Context

WP2.1-A (ADR-0012, D-054) delivered the canonical Jobs domain (`Company`, `Job`, `JobOrigin`, `IdempotencyKey` models in migration `0005`), deterministic normalization, manual paste intake, and the `SafeHttpClient` SSRF security boundary for manual URL intake.

WP2.1-B completes the opportunity ingestion foundation by connecting TapJob to external hosted ATS job boards (Greenhouse, Lever, Ashby) as specified in `docs/IMPLEMENTATION_PLAN.md` and `docs/DELIVERY_ORDER.md`.

### Explicit Out-of-Scope Boundaries
- Opportunity Matching Engine (deferred to WP2.2);
- Public Jobs Feed (`GET /api/v1/jobs`) (deferred to WP2.3);
- Universal auto-apply or headless application submission (AGENTS.md §7: discovery only; official submission requires authorized employer API credentials);
- WhatsApp and Mobile Feed UI (deferred to respective channel checkpoints);
- Modifying production database or direct main commits.

## Architecture & Design Decisions

### 1. Adapter Implementation & Protocol Conformance
Three dedicated adapters implement the provider-neutral `tapjob_api.ports.JobSource` protocol:
1. `GreenhouseJobSource`: consumes Greenhouse public Job Board API (`boards-api.greenhouse.io/v1`). Filters out prospect posts where `internal_job_id` is None/null (not concrete vacancies). Requests `content="true"` in listing and caches listing details to eliminate N+1 detail fetches;
2. `LeverJobSource`: consumes Lever public Postings API (`api.lever.co/v0` and `api.eu.lever.co/v0`). Parses `categories.allLocations` (excluding primary), extracts `workplaceType`, and caches listing details (`description`, `descriptionPlain`, `lists`, `additional`) to eliminate N+1 detail fetches;
3. `AshbyJobSource`: consumes Ashby official public Job Board API (`api.ashbyhq.com/posting-api/job-board/{board_token}`). Adheres strictly to the documented public contract without relying on undocumented `id`:
   - Uses deterministic, bounded `external_id = f"ashby_{sha256(canonical_job_url)[:32]}"` derived from canonical `jobUrl`;
   - Filters out `isListed == False` postings (direct-link-only postings excluded from public discovery);
   - Parses `secondaryLocations` objects (and legacy strings) into `additional_locations_raw`;
   - Extracts explicit `workplace_type_hint` (`workplaceType`, `isRemote`);
   - Caches board listing details (`descriptionHtml`/`descriptionPlain`) to eliminate detail calls.

Each adapter satisfies `JobSource` statically checked by `mypy --strict`:
- `capabilities(ctx)`: reports `DISCOVERY` (SUPPORTED), `JOB_DETAILS` (SUPPORTED), and `INCREMENTAL_SYNC` (UNSUPPORTED);
- `discover(cursor, ctx)`: returns paginated `DiscoveryPage` with `DiscoveredJob` items, normalizing timestamps to UTC;
- `DiscoveredJob.location_raw`, `DiscoveredJob.additional_locations_raw`, and `DiscoveredJob.workplace_type_hint`: provisional extensions to the `JobSource` DTO (documented in ADR-0013 / D-055) carrying provider raw location strings and explicit workplace hints into the canonical ingestion pipeline;
- `fetch_details(ref, ctx)`: retrieves full job description HTML/text, utilizing discovery cache when available to eliminate redundant HTTP calls;
- `health(ctx)`: probes provider endpoint and returns `AdapterHealth` (`HEALTHY`, `DEGRADED` on 429/timeout, `UNAVAILABLE` on 5xx/404).

### 2. Transport Security & Bounded Resources
All ATS network requests are mediated by `tapjob_api.jobs.sources.base` with defense-in-depth SSRF protection and streaming body bounds:
- **Strict HTTPS Boundary:** requires `https://` schemes in runtime and rejects credentials/userinfo;
- **Official Host Whitelisting:** enforces provider-specific official hosts (`boards-api.greenhouse.io`, `api.lever.co`, `api.eu.lever.co`, `api.ashbyhq.com`);
- **DNS Pre-Resolution & IP Blacklisting:** resolves DNS (A and AAAA records) and validates every returned IP against loopback (`127.0.0.0/8`, `::1`), RFC 1918 private IPv4 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), RFC 4193 unique-local IPv6 (`fc00::/7`), CGNAT (`100.64.0.0/10`), link-local (`169.254.0.0/16`, `fe80::/10`), cloud instance metadata (`169.254.169.254`, `169.254.169.253`, `fd00:ec2::254`), IPv4-mapped IPv6, multicast, and broadcast;
- **Connection Pinning:** pins HTTP connections via `PinnedAsyncHTTPTransport` to the pre-validated IP address, defeating DNS rebinding (TOCTOU) attacks while preserving TLS SNI and certificate validation;
- **Revalidated Redirects & Global Deadline Budget:** manually handles redirects (max 3 hops), re-running strict scheme, host, DNS, and IP validations on each hop. Re-evaluates remaining `CallContext` deadline before each hop, immediately aborting with `TIMEOUT` if the budget has expired;
- **Streaming Response Bounds:** streams response bodies using `aiter_bytes()`. Fast-fails on `Content-Length > 5 MiB` and bounds accumulated uncompressed/decoded bytes to `MAX_ATS_RESPONSE_BYTES = 5 MiB` (5,242,880 bytes), aborting decompression bombs and chunked overflows in real time;
- **Proxy Isolation:** enforces `trust_env=False` to prevent proxy hijacking;
- **Error Mapping:** maps HTTP transport errors into normalized `PortError` taxonomy (`TIMEOUT`, `RATE_LIMITED` with `Retry-After` parsing, `AUTH_FAILED`, `INVALID_REQUEST`, `UNAVAILABLE`, `PROVIDER_ERROR`), never leaking raw secrets or provider stack traces.

### 3. Connector Capability Registry & Typed Configuration
`JobSourceRegistry` (`tapjob_api.jobs.sources.registry`) manages active connectors:
- Encapsulates `JobSourceConfig` bindings (`source_id`, `provider`, `source`, `company_name`, `corporate_domain`, `enabled`);
- **Typed Configuration:** `HostedAtsSourceEntry` validates JSON configurations from `HOSTED_ATS_SOURCES` (injected via `WorkerSettings.hosted_ats_sources`);
- **Fail-Closed Validation:** configures `ConfigDict(extra="forbid")` to reject unknown fields/typos (e.g. `enabledd`), rejects unknown providers, empty tokens, and missing company names;
- **Deterministic Identity:** generates deterministic `source_id = f"{provider}:{token}"` unless an explicit `source_id` is supplied;
- Supports atomic registration, lookup, and querying of active sources;
- Exposes `check_health` and `check_all_health` for operational monitoring.

### 4. Canonical Ingestion Pipeline
`ingest_job_source` (`tapjob_api.jobs.ingestion`) processes a configured job source:
- **Global Ingestion Identity:** newly discovered jobs are recorded with `Job.created_by_user_id = NULL` (ADR-0012 §2: global market discovery, distinguishing user-owned manual entries from platform discovery);
- **Corporate Domain Sanitization & Source-Scoped Company Reuse:**
  - Enforces `clean_corporate_domain` so ATS hostnames (`boards.greenhouse.io`, `jobs.lever.co`, `ashbyhq.com`) are stripped and never become `Company.domain`;
  - When `corporate_domain` is `None`, resolves Company already associated with the source board via `JobOrigin` provenance metadata (`provenance_metadata['board_id'] == config.source_id`). Fails closed (`ValueError`) if multiple distinct Companies are linked;
  - Company creation is deferred until a brand-new job actually needs to be persisted (never created on empty discovery or runs that only update existing origins);
  - Maintains strict separation between distinct source boards sharing the same display name without domain merging;
- **Mutable Posting Updates:** re-observed active postings update mutable ATS-provided fields (`title`, `title_normalized`, `location_raw`, `location_normalized`, `workplace_type`, `description_raw`, `description_cleaned`, `similarity_fingerprint`, `source_url`, `canonical_url`) while strictly preserving `job.id`, `company_id`, `created_by_user_id`, `origin.id`, and `first_observed_at`;
- **Deterministic Normalization & Multi-Location:** normalizes job titles, strips punctuation, cleans descriptions, computes SHA-256 similarity fingerprints, persists `Job.location_raw` and `Job.location_normalized`, and classifies workplace types prioritizing explicit provider hints before falling back to text heuristics;
- **Origin Tracking:** records `JobOrigin` with unique `(provider, external_id)` constraint, tracking `first_observed_at`, `last_observed_at`, and source board provenance metadata.

### 5. Liveness Consensus & Absence Reconciliation
To handle jobs removed from upstream job boards without destructive deletion:
- Each ingestion cycle tracks `seen_external_ids` for the source board;
- **Discovery Completeness Guard:** liveness reconciliation (`consecutive_not_found_count` increment and closure) runs **only** when `discovery_complete = True` (i.e. `next_cursor is None`, zero PortErrors during discovery, no pagination cutoff, and no cursor repeat cycles). Truncated or degraded runs skip absence reconciliation entirely to prevent false mass closures;
- **Idempotent Reconciliation Cycles & Concurrency Control:**
  - Ingestion runs require a deterministic `cycle_id`. To ensure idempotency under Celery redelivery (`acks_late=True`) and retries, `cycle_id` is derived from stable Celery task execution identity (`sweep:{task.request.id}` for periodic sweeps, `task:{task.request.id}` for standalone tasks, and explicit propagation from parent sweep to child ingestion tasks, with safe eager/test fallback);
  - `JobOrigin.provenance_metadata["last_reconciliation_cycle_id"]` records applied cycles. Under Celery redelivery or duplicate execution of the same cycle, missing count increments are idempotent no-ops;
  - **Dedicated Physical Connection Advisory Lock:** PostgreSQL advisory locks (`pg_try_advisory_lock` with deterministic 64-bit signed `bigint` key derived from SHA-256) prevent overlapping concurrent runs of the same source board. The lock is acquired and released strictly on a dedicated physical `Connection` checked out from the `Engine` and kept open for the entire duration of the ingestion pipeline, ensuring `session.commit()` on the ORM session does not release the connection back to the pool prematurely or cause `pg_advisory_unlock` to execute on a different connection. The return value of `pg_advisory_unlock` is verified to be `True`, and the dedicated connection is released in `finally`;
- **Transient Fail-Safe Retries:** discovery `PortError` with `transient=True` (TIMEOUT, RATE_LIMITED, UNAVAILABLE) re-raises to trigger Celery task retries. Transient detail fetch errors skip creation of new jobs in that run rather than permanently persisting empty placeholders;
- Existing active origins missing from complete discovery increment `consecutive_not_found_count` and update `last_observed_at`;
- When `consecutive_not_found_count >= CONSECUTIVE_NOT_FOUND_CLOSE_THRESHOLD` (3 consecutive missed runs):
  - `JobOrigin.observation_status` transitions from `ACTIVE` to `CLOSED`;
  - Parent `Job.status` is re-evaluated via `compute_job_effective_status`: when all authoritative origins are `CLOSED`, the job transitions to `CLOSED`;
- If a closed job reappears in future discovery, `consecutive_not_found_count` resets to 0 and statuses return to `ACTIVE`.

### 6. Asynchronous Scheduling & Beat Topology
Worker tasks (`tapjob_api.jobs.tasks`) and worker integration (`tapjob_worker.celery_app`):
- `ingest_job_source_task`: Celery task executing bounded ingestion for a registered source with transactional session management and `cycle_id` propagation;
- **Centralized Celery Beat Scheduler:** periodic execution uses a single Celery Beat scheduler instance configured with UTC schedule `jobs-scheduled-ingestion-sweep` running every 3600 seconds (`DEFAULT_INGESTION_SWEEP_INTERVAL_SECONDS`), generating unique cycle identifiers per sweep;
- Eliminates worker kickoff signals (`worker_ready`) and self-rescheduling chains, ensuring horizontal worker scaling does not duplicate periodic runs;
- Deployment topology (`infra/compose.yaml` and `infra/compose.deploy.yaml`) provisions a dedicated `beat` service running `celery -A tapjob_worker.main:app beat`.
