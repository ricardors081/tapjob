# ADR-0015 — Job Feed, Alerts & Mobile Jobs Surface (WP2.3)

**Status:** Accepted  
**Date:** 2026-10-11  
**Decision:** D-057 (`docs/DECISIONS.md`)  

---

## 1. Context & Scope

WP2.1-A (ADR-0012, D-054) and WP2.1-B (ADR-0013, D-055) established the canonical Jobs domain (`Company`, `Job`, `JobOrigin`), manual intake endpoints, deterministic normalization, and external hosted ATS connectors (Greenhouse, Lever, Ashby) with ingestion scheduling.
WP2.2 (ADR-0014, D-056) established the deterministic Opportunity Matching Engine (`MatchingEngine`), scoring formulas, hard-filter knockout gates, grounded explainability, and canonical `MatchResult` persistence.
PR #42 delivered the Minimum WhatsApp Alpha Vertical Slice, proving end-to-end conversation, channel identity linkage, document intake, and outbox messaging.

WP2.3 is **Job Feed & Alerts** (`docs/IMPLEMENTATION_PLAN.md`, `docs/DELIVERY_ORDER.md`).
Per `docs/DELIVERY_ORDER.md`, following the verified closeout of the WhatsApp Alpha vertical slice, the product formally enters the **Mobile Product Experience** stage (`MOBILE_PRODUCT_STARTED = YES`), scoped strictly to the Jobs domain:

```text
WP2.3 Surface Boundaries:
- Backend: Authenticated Feed API, Relevance Ordering, Bounded Matching Layer, Cursor Pagination, Search/Filters, SavedJob/Dismiss, Company Watchlist, Alert Preferences, Alert Evaluation Worker.
- Mobile: Jobs surface under apps/mobile (Job Feed, Filters, Job Detail with WP2.2 explainability, Save/Dismiss, Company Watch, Alert Settings, Deep Linking).
```

### Explicit Boundaries & Non-Goals for WP2.3
- **Out of Scope (Deferred to WP3.x):**
  - Full Resume Studio (`WP3.1`), ATS Tailoring Studio (`WP3.2`), and full Application Kit Editor (`WP3.3`).
- **Out of Scope (Deferred to WP4.x):**
  - Full Application Kanban/Tracker (`WP4.1`), Full Career Agent Core (`WP4.2`).
- **Out of Scope (Deferred to WP5.x+):**
  - Browser extension, headless apply, automated form submission.
- **Strict Architecture Constraints:**
  - ZERO client-side matching or scoring logic.
  - ZERO duplication of WP2.2 matching heuristics or parallel scoring engines.
  - ZERO semantic embeddings, vector databases, or LLM-based re-ranking in the feed baseline (pure deterministic scoring).
  - ZERO live WhatsApp or Push network sends in test or dev (`WHATSAPP_REAL_SENDS = 0`).

---

## 2. Source Reuse Audit

Per `docs/SOURCE_REUSE_BLUEPRINT.md` and `docs/SOURCE_REUSE_LEDGER.md`:
- TapJob's existing canonical components (`MatchingEngine`, `TruthSnapshot`, `Job`, `CursorPage`, `OutboundMessageQueue`) already provide the full business foundation.
- No external upstream donor code is imported for feed ranking or alert evaluation.
- Mobile UI utilizes TapJob's established `@tapjob/design-tokens`, `@tapjob/contracts`, `@tapjob/product-config`, and `react-i18next`.

---

## 3. The 15 Architectural Decisions for WP2.3

### 1. Feed Candidate Visibility & Multi-Tenant Isolation
- Visibility Rule: A user in the feed may see:
  1. Global jobs discovered by ATS connectors or platform ingestion (`created_by_user_id IS NULL`);
  2. Private jobs created manually by that specific user (`created_by_user_id == current_user.id`).
- Security Invariant (BOLA/IDOR): Private jobs created by User A (`created_by_user_id == user_a.id`) must NEVER be visible, searchable, or accessible to User B.
- Dismissal Invariant: Jobs marked as `dismissed` by the current user are excluded from their default feed.
- Detail Endpoint Uniformity: `GET /api/v1/jobs/{job_id}` enforces this identical visibility predicate: returns 404 (`JOB_NOT_FOUND`) if the job belongs to another user.

### 2. Relevance Ordering
The feed uses a total, deterministic ranking function composed of canonical attributes:
1. **Eligibility Rank (Ascending):**
   - `ELIGIBLE` = 0
   - `REVIEW_REQUIRED` = 1
   - `INELIGIBLE` = 2
2. **Overall Score (Descending):** Pure integer `0..100` from WP2.2 `MatchResult`.
3. **Freshness (Descending):** Job `created_at` timestamp.
4. **Deterministic Tie-Breaker (Ascending):** `job.id` UUID.

No non-deterministic sorting or random factors are permitted. Given the same profile version, policy version, and jobs, the order is completely reproducible.

### 3. Job Liveness & Freshness
- Only offerable opportunities are returned:
  - Status must be `ACTIVE` or `UNKNOWN` (unless explicitly filtered).
  - Status `CLOSED` and `EXPIRED` are excluded from the candidate feed pool.
  - If `expires_at` is set, `expires_at > now()` is required.
- If `published_at` is missing in provider metadata, the feed relies on `created_at` as the freshness metric without fabricating past timestamps.

### 4. Stable Cursor Pagination
- Implemented using the existing `tapjob_api.http.pagination` codec (`CursorPayload`, `encode_cursor`, `decode_cursor`).
- The cursor binds:
  - `sort`: `"relevance"`
  - `filters_fingerprint`: 16-character SHA-256 digest of request filters + `profile_version` + `matching_policy_version`.
  - `last_key`: `{"eligibility_rank": int, "score": int, "created_at": str, "job_id": str}`.
- Fail-Closed Stability: If the user modifies filters, or their `profile_version` bumps mid-pagination, or the system `policy_version` changes, `decode_cursor` rejects the cursor with `400 INVALID_CURSOR`. Tampered cursors or cursors exceeding `MAX_CURSOR_LENGTH (2048)` are rejected immediately.

### 5. Filter Contract
Endpoints accept typed, validated, and bounded query parameters:
- `q`: Optional search text (validated, bounded to max 100 chars, matched case-insensitively against title and company).
- `company`: Optional company name filter.
- `location`: Optional location filter.
- `workplace_type`: Optional enum filter (`JobWorkplaceType`).
- `eligibility`: Optional list of eligibility values (`eligible`, `review_required`, `ineligible`).
- `min_score`: Optional integer threshold (0..100).
- `saved_only`: Optional boolean flag to restrict to user's saved jobs.
- `limit`: Optional page size (default 20, max 100).
- `cursor`: Optional opaque pagination token.
No raw user input is passed directly to SQL. All queries use parameterized SQLAlchemy constructs.

### 6. SavedJob Semantics
- Model: `SavedJob` with columns:
  - `id`: UUID primary key
  - `user_id`: UUID FK -> `users.id` (ON DELETE CASCADE)
  - `job_id`: UUID FK -> `jobs.id` (ON DELETE CASCADE)
  - `state`: String enum (`saved`, `dismissed`)
  - `created_at`, `updated_at`: `timestamptz`
- Table constraint: `UNIQUE(user_id, job_id)`.
- Operations are idempotent:
  - `POST /api/v1/jobs/{job_id}/save`: Sets state to `saved`.
  - `DELETE /api/v1/jobs/{job_id}/save`: Removes the saved relation.
  - `POST /api/v1/jobs/{job_id}/dismiss`: Sets state to `dismissed`.
  - `DELETE /api/v1/jobs/{job_id}/dismiss`: Removes the dismissed relation.
  - `GET /api/v1/jobs/saved`: Returns cursor-paginated list of saved jobs.

### 7. Company Watchlist Semantics
- Model: `CompanyWatch` with columns:
  - `id`: UUID primary key
  - `user_id`: UUID FK -> `users.id` (ON DELETE CASCADE)
  - `company_id`: UUID FK -> `companies.id` (ON DELETE CASCADE)
  - `created_at`: `timestamptz`
- Table constraint: `UNIQUE(user_id, company_id)`.
- Operations are idempotent:
  - `POST /api/v1/companies/{company_id}/watch`: Adds watch relation if company exists.
  - `DELETE /api/v1/companies/{company_id}/watch`: Removes watch relation.
  - `GET /api/v1/companies/watched`: Returns list of watched companies for current user.

### 8. Alert Eligibility
- Owned entirely by the Core domain, not by messaging transports or client devices.
- An ingested job is alert-eligible for a user if:
  1. The user has an active `JobAlertPreference(enabled=True)`;
  2. The job is offerable and visible (`created_by_user_id IS NULL` or equals user);
  3. The match score meets or exceeds `JobAlertPreference.min_score`;
  4. The eligibility matches `JobAlertPreference.min_eligibility` (e.g. `eligible`);
  5. The job has not already been notified to this user.

### 9. Alert Deduplication
- Durable Dedupe Key: `alert:{user_id}:{job_id}:{policy_version}`.
- Model: `JobAlertNotification` with columns:
  - `id`: UUID primary key
  - `user_id`: UUID FK -> `users.id`
  - `job_id`: UUID FK -> `jobs.id`
  - `dedupe_key`: String (UNIQUE constraint)
  - `channel`: String (`whatsapp`)
  - `match_score`: SmallInteger
  - `status`: String enum (`pending`, `queued`, `delivered`, `failed`, `skipped`):
    - `PENDING`: eligible intent waiting for usable channel / deferred reconciliation.
    - `QUEUED`: durable outbox message exists (`outbound_message_id` FK set), not yet dispatched.
    - `DELIVERED`: outbox message reached terminal `SENT` status (`delivered_at` populated).
    - `FAILED`: terminal outbox delivery failure (`DEAD` / `FAILED`).
    - `SKIPPED`: no longer authorized or eligible before delivery (e.g. user disabled alerts / opted out, thresholds tightened, job closed/expired, or outbox cancelled).
  - `outbound_message_id`: BigInteger FK nullable -> `outbound_message_queue.id` (ON DELETE SET NULL).
  - `created_at`: `timestamptz`
  - `delivered_at`: `timestamptz` nullable
- Generic Outbox Cancellation: `OutboundMessageStatus` includes `CANCELLED` (`status = 'cancelled'`). When a user disables alerts while a message is in `PENDING` or `RETRYABLE` state, the outbox record transitions to `CANCELLED` (excluded from worker claims) and the notification transitions to `SKIPPED`.
- Database invariant: `UNIQUE(dedupe_key)` prevents double notification even under concurrent evaluation runs or worker retries.

### 10. Notification & Channel Neutrality
- Preferences and intent are channel-neutral. `JobAlertPreference` models user interest without coupling to a specific transport SDK:
  - `enabled`: boolean (default `False` — strict opt-in required).
  - `min_score`: integer threshold (default 50).
  - `min_eligibility`: string (default `eligible`).
  - `delivery_channel`: enum (`JobAlertDeliveryChannel.WHATSAPP` only in WP2.3).
- In WP2.3, WhatsApp is the sole supported delivery channel (`SUPPORTED_JOB_ALERT_CHANNELS = ("whatsapp",)`). Any unsupported channel string is rejected with 422 Unprocessable Entity.
- Advanced filter dicts (`filters`) are excluded in WP2.3 to maintain a clean, factual contract.

### 11. Mobile / Backend Boundary
- The mobile application (`apps/mobile`) is strictly a presentation and interaction surface.
- ZERO score calculation, eligibility logic, hard blocker evaluation, or skill parsing runs in TypeScript.
- Match score, eligibility badges, strengths, missing requirements, and explanations are received precomputed from the backend via the generated `@tapjob/contracts` client.
- The UI renders badges, cards, and bottom sheets using `@tapjob/design-tokens` semantic colors and typography.
- Operation ID uniformity: `getJobById` retrieves single job detail.

### 12. Performance & Bounded-Work Policy
- Candidate Pool Bound: `FEED_MAX_CANDIDATES = 100`. The DB retrieves at most 100 recent candidate opportunities matching coarse filters.
- Snapshot Candidate Cutoff: The initial candidate pool timestamp is captured as `feed_as_of` (`candidate_cutoff`) and carried in the pagination cursor, preventing candidate pool drift and omitted jobs when new jobs are ingested between page fetches.
- Bounded Query Count: A feed request executes a bounded set of DB operations (candidate jobs selection, saved/dismissed lookup, and canonical batch match lookup/persistence).
- Performance Stance: Response latency is bounded by the candidate pool ceiling and query constraints; formal p95 SLAs are to be measured under staging load rather than claimed a priori.

### 13. Bulk Matching Strategy
- Bulk matching is orchestrated canonically by `tapjob_api.matching.service.get_or_compute_matches_batch`.
- Evaluates the user's single `TruthSnapshot` and candidate signals once per batch.
- Batch-loads existing `MatchResult` records, validates fingerprints, computes missing matches, and persists them using concurrency-safe `INSERT ... ON CONFLICT DO NOTHING`.
- Verifies `deterministic_result_digest` convergence and publishes to Redis cache only post-commit.
- Both the feed API and alert evaluation worker consume this canonical batch service, ensuring zero parallel matching engines exist.

### 14. Privacy & Security
- Strict ownership checks on every endpoint. User ID is extracted from verified auth session (`identity_deps.get_current_user`).
- No IDOR/BOLA leaks: cross-user private jobs, saved jobs, company watches, and alert preferences return 404.
- Job descriptions remain untrusted external data. No raw HTML is executed in mobile; text is rendered inertly.
- Zero PII in logs: search queries, resumes, and alert phone numbers are omitted from log payloads.
- Deep linking is validated against `productConfig.deepLinkScheme`, and external job URLs are restricted to `https:` and `http:` schemes (rejecting `javascript:`, `intent:`, `file:`, etc.).

### 15. Rollback & Migration Stance
- Migration `20261011_0013_job_feed_alerts_saved_watchlist.py` creates:
  - `saved_jobs` (with unique constraint `uq_saved_jobs_user_job`)
  - `company_watches` (with unique constraint `uq_company_watches_user_company`)
  - `job_alert_preferences` (with unique constraint `uq_job_alert_preferences_user`, `delivery_channel = 'whatsapp'`)
  - `job_alert_notifications` (with unique constraint `uq_job_alert_notifications_dedupe_key`, `outbound_message_id` FK nullable)
- Full rollback support: `downgrade()` cleanly drops all created tables, constraints, and indexes.
- Verified through migration upgrade -> downgrade -> re-upgrade tests.

---

## 4. Consequences & Guarantees

- **No Duplication:** WP2.2 remains the single source of truth for matching intelligence via `get_or_compute_matches_batch`.
- **Bounded Feed:** Bounded candidate pool (max 100) and query count with candidate pool snapshot freezing across cursor pagination pages.
- **Full Isolation:** Multi-user isolation is guaranteed at the database query level.
- **Mobile Velocity:** Clean `@tapjob/contracts` integration enables high-quality, reactive mobile UI without business logic sprawl.

