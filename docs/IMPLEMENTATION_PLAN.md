# Implementation Plan — TapJob

This file translates roadmap into executable work packages.

## Delivery sequence

Customer-surface priority is defined in `DELIVERY_ORDER.md`:

```text
Foundation -> Minimum Career Core -> WhatsApp -> Mobile App -> Browser Extension
```

Existing domain work packages remain authoritative for scope. After WP0.1/WP0.2, checkpoints must be sliced to reach the minimum WhatsApp vertical journey before building the full mobile customer experience.

## Rules

- Work packages are sequential unless explicitly marked parallel-safe or the delivery-order document defines an approved minimal vertical slice.
- Each WP must have its own branch/PR or a tightly scoped PR series.
- Do not start a downstream WP while dependencies are unverified.
- Update `PROJECT_STATE.md` after each accepted WP.
- Before reusing upstream artifacts, follow `SOURCE_REUSE_BLUEPRINT.md` and update `SOURCE_REUSE_LEDGER.md`.
- One primary donor/runtime implementation per capability; secondary donors provide tests, edge cases or reference behavior.

---

## WP0.1 — Engineering Foundation

### Objective
Create reproducible repository and CI baseline.

### Scope
- monorepo scaffold;
- backend service;
- mobile app;
- local Docker dependencies;
- PostgreSQL;
- Redis;
- Alembic;
- lint/typecheck/test;
- env templates;
- GitHub Actions;
- branch protection/ruleset configuration when supported by the active GitHub plan, otherwise documented manual enforcement of the same merge gates;
- secret scan;
- health checks;
- structured logging;
- `packages/design-tokens` scaffold;
- `packages/product-config` scaffold;
- typed backend/public configuration modules;
- initial i18n foundation (`pt-BR`);
- lint/check strategy against new raw visual magic values once tokens exist.

### Acceptance
- fresh clone -> documented local boot;
- backend health endpoint;
- mobile shell boots;
- DB migration applies;
- tests run in CI;
- latest-HEAD CI gate is demonstrably enforced either technically by GitHub protections or procedurally by review/merge evidence;
- no hardcoded secrets;
- demo mobile shell consumes semantic tokens/product config rather than hardcoded brand values;
- changing the configured product name or a primary semantic token does not require editing feature screens.

---

## WP0.2 — Technical Decisions & Contracts

### Objective
Resolve pending foundational ADRs.

### Already decided
- auth: Firebase Authentication;
- worker: Celery + Redis;
- object storage: Cloudflare R2;
- initial hosting: owner's Linux server + Docker Compose behind Cloudflare;
- product analytics: Firebase Analytics;
- technical observability: Sentry.

### Checkpoints

#### WP0.2-A — HTTP Contract, OpenAPI Client & Contract CI (VERIFIED)
Independently reviewed and merged in PR #9; CI green post-merge on `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`.
- OpenAPI/client generation: `@hey-api/openapi-ts` pinned exact, deterministic offline export, `createApiClient`/`isApiError` facade;
- `/api/v1` versioning convention (`/health`/`/ready` stay unversioned), explicit camelCase `operationId`;
- `ErrorResponse` error envelope and exception handlers;
- cursor pagination codec (`CursorPage`, not tamper-proof);
- `Idempotency-Key`/`X-Request-ID` conventions (idempotency: convention only, no middleware yet);
- contract CI gates (OpenAPI export diff, TS client generation diff, generator regression tests).

See `docs/adr/0003-api-contract-client-generation.md`, `docs/adr/0004-http-errors-pagination-correlation-idempotency.md` and `docs/PROJECT_STATE.md`.

#### WP0.2-B — Internal Ports & Capability Model (VERIFIED)

##### Checkpoint A — Internal ports & capability model (VERIFIED)
Independently reviewed across three rounds (four blocking findings, then two micro-findings, then two documentation precision corrections — all resolved), merged via squash into `main` as PR #12 (squash commit `a8bfbe621e5a83ae5f205adb1919ce3250684efb`); CI green at latest HEAD before merge (run `37051633827`, 7/7) and CI green post-merge on `main` (run `37055251128`, 7/7).
- canonical interfaces required before upstream code enters runtime: `JobSource`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector` (as `Protocol`s with a capability model) under `tapjob_api.ports`;
- `MatchingEngine` remains internal/domain for now, not a provider-neutral port: interface extraction is deferred to WP2.2 if justified by a real substitution need, not assumed up front;
- shared `CallContext`/`Deadline`, normalized `PortError`/`PortErrorCode`, `AdapterHealth`, `CapabilityStatus`/`CapabilityReport`/`CapabilityManifest`;
- no adapter, no provider, no deploy.

See `docs/adr/0005-internal-ports-capability-model.md` and `docs/PROJECT_STATE.md`.

##### Checkpoint B — Worker correlation & deployment contract (VERIFIED)
Independently reviewed across three rounds (four blocking findings, then a `gh api` HTTP-method bug and a retry-metadata type hole), merged via squash into `main` as PR #15 (squash commit `3edaf1cee33b892a5d753b07b9b9946cbcd28887`); CI green at latest HEAD before merge (run `37230556089`, 8/8) and CI green post-merge on `main` (run `37231990603`, 8/8).
- worker-side TapJob correlation id propagated across Celery message boundaries via a custom message header, bound into structured worker logs (Celery task id/root id/parent id/task name/retries, never args/kwargs), with idempotent signal connection and context reset on success/failure/retry;
- deployment/CD contract within the approved topology (not a real deploy): build-once/promote-the-same-immutable-digest, one backend image preserved (D-043), `infra/compose.deploy.yaml`, `tools/deploy/validate_image_ref.mjs` digest-format gate, a `workflow_dispatch`-only GHCR publish workflow (zero runs to date), migration-before-rollout ordering, redigest-only rollback.

See `docs/adr/0006-worker-correlation-deployment-cd-contract.md`, `docs/DEPLOYMENT.md` and `docs/PROJECT_STATE.md`. No real deploy, no GHCR publish, no product feature, no migration occurred in reaching VERIFIED — the first real execution of `release-image.yml` and the first real environment promotion remain a separate, later, operational gate (ADR-0006).

### Deliver
- ADRs;
- contract skeleton;
- error envelope;
- pagination conventions;
- observability conventions.

---

## WP1.1 — Identity & User Account (VERIFIED)

Independently reviewed across three rounds (5 blocking findings, then 6 operational-hardening findings, then one concentrated session-retry-semantics blocker — all resolved), merged via squash into `main` as PR #17 (squash commit `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`); CI green at latest HEAD before merge (run `37254156902`, 8/8) and CI green post-merge on `main` (run `37312677497`, 8/8). See `docs/adr/0009-identity-auth-user-account.md` (D-051) and `docs/PROJECT_STATE.md` for the full checkpoint summary.

### Deliver
- sign-up/login — Firebase Authentication (Google/Apple/email), internal `User` UUID canonical, `ExternalIdentity` links a Firebase UID, never auto-linked by email, concurrency-safe bootstrap;
- session lifecycle — Firebase owns the client session; backend verifies ID tokens (`check_revoked=True`); global revoke-all (not per-device); 15-minute recent-authentication policy for security-sensitive actions;
- account settings — locale (product-config default, user-overridable), timezone (neutral `Etc/UTC` default, never inferred from server/request location);
- locale/timezone — validated (BCP-47-compatible pattern; real IANA `zoneinfo`);
- device registration — privacy-preserving random `installation_id`, idempotent re-register, ownership-scoped revoke;
- deletion skeleton — request/query/cancel only, 202 Accepted, never claims data was actually deleted;
- consent model — append-only `ConsentRecord`, no fixed purpose-key catalog enforced yet (none approved for the product).

### Security
- ownership enforcement — every `/me/...` route derives `user_id` from the verified token, never a client-supplied value; IDOR-tested (a resource owned by a different user resolves to its own not-found code, never a distinguishable forbidden response);
- session revocation — real Firebase `revoke_refresh_tokens`, audited with the actual provider outcome (never claims success before Firebase confirms it);
- rate limit — generic, reusable, Redis-backed fixed-window limiter, fail-closed on Redis outage, guarding bootstrap/revoke-all/devices/consents/deletion;
- audit — append-only `AccountAuditEvent`, allowlisted metadata only, never args/kwargs/tokens/passwords.

Mobile: React Native Firebase + an Expo Development Build (not the Firebase JS SDK, not Expo Go — `docs/adr/0009-...md` §H); only a minimal auth technical surface, no product screens. Zero real Firebase project, zero real deploy, zero real OAuth, zero production data in this checkpoint.

---

## WP1.2 — Career Profile & Truth Store (VERIFIED)

Implemented end-to-end — `CareerProfile`/`ProfileFact` typed truth store, optimistic concurrency,
truth snapshot, provenance, audit, migration `0003` — see `docs/adr/0010-career-profile-truth-store.md`
(D-052) and `docs/PROJECT_STATE.md` §1. Independently reviewed across three rounds, merged via
squash into `main` as PR #19 (squash commit `5c06c805ea2a68fcf100ec56cff5eaf715e962c2`). Real
post-merge CI (run `37364799439`, attempt 2) exposed a genuine SQLAlchemy identity-map-staleness
concurrency defect under `SELECT ... FOR UPDATE` (ADR-0010 "Addendum — round 4"); the structural fix
was independently reviewed and merged via squash as corrective PR #20 (squash commit
`f30590577cf25733de63025f987c251ebe0e89af`); post-merge CI on the resulting `main` (run
`37397784331`) is green 8/8, with `Backend integration` re-proving the previously-failing
concurrency test alongside a new deterministic regression test. **WP1.2 is VERIFIED.** WP1.3 —
Resume Import is **VERIFIED** (PR #22, squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`).
Phase 1 Career Core baseline is complete to the extent defined by those work packages. The next domain
work is Phase 2 — Jobs & Matching (WP2.1 under decision-gate preparation). Downstream work packages
remain NOT AUTHORIZED until their dependencies are verified.

### Deliver
- CareerProfile;
- ProfileFact;
- preferences;
- skills;
- experiences;
- education;
- certifications;
- links;
- work authorization answers;
- salary/location preferences.

### Channel-first
- API/use cases must support WhatsApp onboarding first;
- full mobile onboarding/profile editing is implemented in the later Mobile App surface stage.

### Reuse plan
- PORT source-backed profile/evidence concepts from career-ops.
- ADAPT Appliable's source-of-truth discipline and resume preferences workflow.
- TapJob PostgreSQL model remains canonical.

### Acceptance
All facts used by AI have provenance/confirmation metadata.

---

## WP1.3 — Resume Import (VERIFIED)

Implemented end-to-end and independently reviewed across two rounds (10 findings in Round 1, 7 findings + 2 hardening items in Round 2), rebased cleanly over Expo maintenance PR #23, merged via squash into `main` as PR #22 (squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, approved PR HEAD `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`). CI green pre-merge (run `37538366658`, 8/8) and post-merge (run `37551974662`, 8/8). Tree equivalence confirmed (`af7d26f4544f573f4d93b212b4c11b070f3f2052`). **WP1.3 is VERIFIED.** See `docs/adr/0011-resume-import.md` (D-053) and `docs/PROJECT_STATE.md` §1. Phase 1 Career Core baseline is complete. Next domain work is Phase 2 — Jobs & Matching (WP2.1 under decision-gate preparation). WP2.2+ remain NOT AUTHORIZED.

### Deliver
- PDF/DOCX upload — bounded, signature-validated, never trusting `Content-Type`/extension alone;
- text extraction — bounded, non-OCR `pypdf`/`python-docx`, scanned/image-only documents detected
  and reported, never silently presented as successful;
- structured parsing — a deterministic, non-AI heuristic parser (no `AiGateway` provider adapter
  implemented in this checkpoint, stated honestly); every candidate fail-closed validated against
  WP1.2's canonical `FactValue` union before persistence;
- review before save — candidates are UNCONFIRMED `ResumeImportCandidate` rows, never a
  `ProfileFact`, until explicit user review/finalization; no acceptance by omission;
- file storage — private Cloudflare R2 (staging/production) behind a local `ObjectStoragePort`,
  random server-generated storage keys; local/development uses a shared `LocalFileObjectStorage`
  directory (a Docker named volume both `api`/`worker` mount) when R2 is unset, never an in-memory
  fake at runtime — `create_storage_adapter` fails fast if neither is configured. Tests inject
  `InMemoryObjectStorage` explicitly instead (zero real R2 mutation; round-2 independent review
  "Finding 5");
- master resume creation — one atomic `profile_version` transition per finalization, at most one
  master `Resume`/immutable `ResumeVersion` per user (a real DB invariant), grounded via `fact_ids`
  back to confirmed `ProfileFact`s, idempotent under retry and genuine concurrent races.

### Reuse plan
- PORT JobSync resume-import prompts/evals — DONE: discipline/rules/eval-assertion shape ported
  into the deterministic parser and its golden-dataset tests; schema/code explicitly rejected (does
  not map onto TapJob's canonical `FactValue` union) — see `docs/SOURCE_REUSE_LEDGER.md`.
- IMPORT/ADAPT AutoApply parser helpers where format coverage is useful — DONE as ADAPT only: the
  heading-synonym-map CONCEPT was adapted; AutoApply's own Markdown/regex machinery does not apply
  to plain text extracted from a real PDF/DOCX and was not imported.
- Use Resumint only for mobile resume/profile editing UX, not backend parsing — still applies; no
  mobile UI was built in this checkpoint (backend/Career-Core capability only).

### AI eval
Golden dataset for extraction correctness — implemented as a deterministic pytest suite
(`test_resume_import_parsing.py`): multi-role/ongoing-role experience, education, skills, languages,
certifications, projects, links, summary/headline, missing sections, unrecognized section heading,
partial dates, pt-BR/Unicode, prompt-injection-like text treated as inert data, and confirmation
that `target_roles`/salary/location/work-authorization are never inferred. No network AI provider
call required or used.

### Known limitations and pre-production gates (documented, not silently worked around)
No malware scanner integrated (`docs/SECURITY_PRIVACY.md` §12). No `AiGateway`-backed parser (fully
deterministic in this checkpoint). No PDF/resume rendering (`ResumeArtifact` remains unimplemented,
`docs/DATA_MODEL.md`). No signed-URL/download endpoint (not required by this checkpoint's accepted
API surface).
**Pre-production gate:** raw uploaded source blob retention/deletion lifecycle is not yet implemented end-to-end. Stored blobs persist indefinitely in storage; account deletion (WP1.1 skeleton) does not yet reach stored blobs. Before production launch or handling real personal data, an approved retention policy, orphan reconciliation, and end-to-end blob deletion must be implemented (Gate 1).

---

## WP2.1 — Jobs Domain & Connector Framework

Divided into two sequential checkpoints:
- **WP2.1-A — Jobs Domain, Manual Intake & Normalization Foundation (IMPLEMENTED):**
  - Canonical `Company`, `Job`, `JobOrigin`, `IdempotencyKey` entities (migration `0005`);
  - Strong identity via `(provider, external_id)` with nullable `external_id` for manual intakes;
  - Corporate domain identity protection against multi-tenant ATS hostnames;
  - Deterministic text normalization, workplace type classification, and non-unique similarity fingerprint;
  - `SafeHttpClient` SSRF security boundary (DNS pre-resolution, IP allowlist, connection pinning, SNI/TLS preservation, 2 MiB streaming decoded limit, manual redirects);
  - Authenticated manual paste intake (`POST /api/v1/jobs/intake/paste`, 30 req/h rate limit);
  - Authenticated safe URL intake (`POST /api/v1/jobs/intake/url`, 15 req/h rate limit);
  - Authenticated job detail retrieval (`GET /api/v1/jobs/{jobId}`).
  - See `docs/adr/0012-jobs-domain-manual-intake.md` (D-054).
- **WP2.1-B — External Hosted ATS Connectors & Ingestion Scheduler (PENDING AUTHORIZATION):**
  - Greenhouse, Lever, Ashby adapter implementations conforming to `JobSource` port;
  - Ingestion scheduler and periodic Beat tasks;
  - Connector capability registry and source health tracking.

### Reuse plan
- ADAPT JobBot's normalization utilities, text cleanup, and workplace keywords (`docs/SOURCE_REUSE_LEDGER.md`).
- PORT JobSync candidate dedupe heuristics and test cases.
- PORT Appliable dedupe cases as supplemental fixtures.

---

## WP2.2 — Matching Engine

### Deliver
- hard filters;
- lexical score;
- semantic layer if justified;
- optional LLM re-score;
- explanation;
- blockers;
- missing requirements;
- caching/versioning.

### Reuse plan
- IMPORT/ADAPT JobBot hard-filter gate and matching pipeline.
- IMPORT/ADAPT AutoApply lexical/TF-IDF resume/job scoring helpers.
- PORT career-ops job reasoning and evidence discipline.
- PORT JobSync job-match evals as a regression gate.

### Acceptance
Deterministic tests + AI evals.

---

## WP2.3 — Job Feed & Alerts

### Surface sequencing
The underlying feed/search/filter APIs can be built before the app. The full visual mobile feed/detail/filter experience is delivered after the WhatsApp-first vertical slice.

### Backend
- relevance ordering;
- freshness;
- notification eligibility.

---

## WP3.1 — Resume Studio

### Deliver
- structured editor;
- sections;
- templates;
- versioning;
- PDF render;
- preview;
- resume library.

### Reuse plan
- IMPORT/ADAPT Resumint Expo resume list/editor/preview components and local draft-state patterns.
- IMPORT/ADAPT AutoApply backend resume assembly/rendering helpers.
- Resumint AI provider calls must be replaced by TapJob backend AI Gateway.

### Mobile
Resumint is the primary code donor for Resume Studio UI, rebuilt behind TapJob API contracts.

---

## WP3.2 — ATS & Tailoring

### Deliver
- ATS checks;
- keyword match;
- tailored version;
- fact grounding;
- diff;
- per-job resume.

### Reuse plan
- PORT Appliable `customize-resume` truth/approval workflow.
- PORT career-ops CV fact allowlist, retracted-claim and writing guardrails.
- Reuse AutoApply scoring/assembly where compatible.
- Reuse JobSync evaluation fixtures.

### Blocking rule
No generated fact without Professional Truth Store evidence.

---

## WP3.3 — Application Kit

### Deliver
- cover letter;
- answer suggestions;
- company brief;
- recruiter message draft;
- checklist;
- immutable kit snapshot.

---

## WP4.1 — Application Tracker

### Deliver
- state machine;
- timeline;
- notes;
- tasks;
- status transitions;
- application documents;
- analytics counters.

### Reuse plan
- IMPORT/ADAPT Basafy application list/detail, timeline and update UX as primary mobile donor.
- IMPORT selected CareerTrack search/filter/reminder/document-capture components.
- PORT JobSync application/interview/contact/task behavior where it improves the canonical model.

### Mobile
- Kanban/list;
- detail/timeline.

---

## WP4.2 — Career Agent Core

### Deliver
- agent session;
- tool registry;
- read tools;
- low-risk write tools;
- confirmation system;
- prompt injection defense;
- AI gateway;
- cost/latency telemetry.

### Reuse plan
- PORT JobSync tool registry/schema, approval cards and turn-metrics concepts.
- IMPORT/PORT career-ops prompt/playbook assets and golden evals.
- ADAPT Appliable conversational orchestration patterns.
- TapJob confirmation/policy layer remains authoritative.

### Eval
- tool selection;
- grounding;
- unsafe external action prevention.

---

## WP4.3 — WhatsApp-first Product Surface

### Initial transport
- Evolution API behind `MessagingChannel`;
- normalize provider events immediately;
- no domain code depends on Evolution/Baileys webhook shapes;
- preserve migration path to official WhatsApp Cloud API.

### Deliver
- inbound webhook;
- identity linking;
- natural commands;
- job alerts;
- application kit handoff;
- interactive confirmations;
- deep links.

### Reuse plan
- PORT Appliable's WhatsApp interaction/reporting UX.
- Use JobBot multi-channel alert separation as a transport-design reference.
- Do not import WAHA or browser-session assumptions into the production adapter.

### Constraint
Evolution is an initial transport for development/controlled early validation. Broad commercial scale must re-evaluate/migrate to the official WhatsApp Cloud API as documented in `DELIVERY_ORDER.md`.

---

## WP5.1 — Apply Engine

### Reuse plan
- IMPORT/ADAPT AutoApply `BaseApplier` concepts, form-field mapping, ATS selectors, upload handling and failure detection.
- PORT career-ops knock-out pre-scan and human-submit policy.
- Do not carry upstream silent final-submit behavior into the initial TapJob modes.

### Deliver
- capability resolver;
- official/assist/deep-link/manual modes;
- policy settings;
- idempotency;
- audit;
- failure taxonomy;
- downgrade logic.

---

## WP5.2 — Browser Extension

### Deliver
- supported-site detection;
- field extraction;
- backend mapping;
- user preview;
- safe autofill;
- resume upload;
- unresolved-question flow;
- tracker sync.

### Constraint
No silent final submission in initial release.

---

## WP6.1 — Gmail Compliance & OAuth

### Before code completion
- Google Cloud setup;
- OAuth consent configuration;
- privacy docs;
- verification path;
- restricted-scope assessment requirements.

### Deliver
- connect/disconnect;
- encrypted token storage;
- minimal readonly scope;
- watch renewal.

---

## WP6.2 — Inbox Intelligence

### Reuse plan
Basafy is the primary implementation donor. PORT its query buckets, staged fetch, parsers, entity normalization, match resolver/adjudicator, dedupe, observability, rate limits, timeline models and tests from Supabase/Deno into Python/PostgreSQL workers. IMPORT/ADAPT compatible Expo Gmail sync/onboarding/push UX. JobBot IMAP logic is secondary reference only.

### Deliver
- query buckets;
- metadata/snippet prefilter;
- selective body fetch;
- heuristic parser;
- LLM adjudication;
- application linkage;
- normalized events;
- dedupe;
- confidence review;
- push.

---

## WP7.1 — Interview Hub

### Reuse plan
- IMPORT/PORT career-ops interview plan/practice/debrief prompt assets, story-bank and question-bank patterns.
- PORT JobSync interview/contact/task domain behavior only where it fits canonical entities.

### Deliver
- interview entity;
- reminders;
- company/role prep;
- likely questions;
- STAR story bank;
- text simulator;
- debrief;
- follow-up.

---

## WP7.2 — Analytics

### Reuse plan
- PORT Basafy funnel/insights semantics as primary analytics reference.
- Cross-check JobSync/JobBot metrics and tests for additional edge cases.

### Deliver
- funnel;
- source performance;
- resume performance;
- weekly trends;
- time per stage;
- response/interview/offer conversion.

Guard against claiming causation from observational metrics.

---

## WP8.1 — Gupy Partnership Connector

### Prerequisite
Official access/partnership model resolved.

### Deliver if authorized
- job discovery;
- question forms;
- Quick Apply;
- resume upload;
- application IDs;
- error handling;
- audit.

---

## WP8.2 — Brazil Source Expansion

Research and add only with connector dossiers:
- Sólides;
- Vagas.com.br;
- Catho;
- InfoJobs;
- others validated.

---

## Parallel-safe workstreams

Can progress alongside WPs when they do not alter contracts:
- design system;
- security threat modeling;
- AI eval datasets;
- source/licensing registry;
- infrastructure experiments;
- partnership outreach.

## First authorized next step

**WP1.3 — Resume Import (IMPLEMENTED, pending independent review — NOT VERIFIED).**
WP0.1, WP0.2 (both WP0.2-A and all of WP0.2-B), WP1.1 and WP1.2 are all independently verified,
merged, and post-merge CI verified. WP1.1 — Identity & User Account is **VERIFIED**
(`docs/adr/0009-identity-auth-user-account.md`, D-051; PR #17, squash commit
`bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`; CI green pre-merge run `37254156902` 8/8 and post-merge
run `37312677497` 8/8), which authorized WP1.2 to begin. WP1.2 — Career Profile & Truth Store
(`docs/adr/0010-career-profile-truth-store.md`, D-052) is **VERIFIED**: implemented end-to-end,
independently reviewed across three rounds, merged via squash as PR #19 (squash commit
`5c06c805ea2a68fcf100ec56cff5eaf715e962c2`); real post-merge CI (run `37364799439`, attempt 2)
exposed a genuine SQLAlchemy identity-map-staleness concurrency defect under
`SELECT ... FOR UPDATE` (ADR-0010 "Addendum — round 4"), fixed structurally and independently
reviewed as corrective PR #20, merged via squash (squash commit
`f30590577cf25733de63025f987c251ebe0e89af`); post-merge CI on the resulting `main`
(run `37397784331`) is green 8/8, with `Backend integration` re-proving the previously-failing
concurrency test. WP1.2 being verified authorized WP1.3 to begin. WP1.3 — Resume Import (`docs/adr/0011-resume-import.md`, D-053) is **VERIFIED**: implemented end-to-end, independently reviewed across two rounds (10 findings in Round 1, 7 findings + 2 hardening items in Round 2), rebased cleanly over Expo maintenance PR #23, merged via squash into `main` as PR #22 (squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, approved PR HEAD `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`). CI green pre-merge (run `37538366658`, 8/8) and post-merge (run `37551974662`, 8/8). Tree equivalence confirmed (`af7d26f4544f573f4d93b212b4c11b070f3f2052`). **WP1.3 is VERIFIED.** Raw source blob retention/deletion lifecycle remains an open pre-production gate. Phase 1 Career Core baseline (WP1.1/WP1.2/WP1.3) is complete. The next domain work is Phase 2 — Jobs & Matching (WP2.1 under decision gate). WP2.2+ and further downstream work packages remain **NOT AUTHORIZED**.

WP0.1 is independently verified and merged. WP0.2-A is independently verified and merged (PR #9, `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`). WP0.2-B Checkpoint A (internal ports/capability model) is independently verified and merged via squash (PR #12, `main` commit `a8bfbe621e5a83ae5f205adb1919ce3250684efb`; post-merge CI green, run `37055251128`, 7/7). WP0.2-B Checkpoint B (worker-side TapJob correlation propagation; ADR-0006 deployment/CD contract, not a real deploy) is independently verified and merged via squash (PR #15, `main` commit `3edaf1cee33b892a5d753b07b9b9946cbcd28887`; CI green pre-merge, run `37230556089`, 8/8, and post-merge, run `37231990603`, 8/8). WP0.2-B overall — and WP0.2 as a whole — is **VERIFIED**. WP1.1 (internal `User`/Firebase `ExternalIdentity`/bootstrap/session lifecycle/devices/consent/deletion skeleton/rate limiting/minimal mobile auth surface) is independently reviewed across three rounds, merged via squash (PR #17, `main` commit `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`), CI green at latest HEAD before merge and post-merge CI green. **WP1.1 is VERIFIED.** WP1.2 (`CareerProfile`/`ProfileFact` typed truth store, optimistic concurrency, truth snapshot, provenance, audit) is independently reviewed across three rounds, merged via squash (PR #19, `main` commit `5c06c805ea2a68fcf100ec56cff5eaf715e962c2`); a genuine post-merge concurrency defect was then found, structurally fixed, independently reviewed and merged via squash as corrective PR #20 (`main` commit `f30590577cf25733de63025f987c251ebe0e89af`), with post-merge CI green 8/8 (run `37397784331`) re-proving the fix. **WP1.2 is VERIFIED.** WP1.3 (PDF/DOCX resume import, file security, object storage, bounded extraction, deterministic candidate parsing, fail-closed validation, review/finalization, provenance, master resume creation) is independently reviewed across two rounds, merged via squash as PR #22 (squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`), and post-merge CI green 8/8 (run `37551974662`). **WP1.3 is VERIFIED.** The next domain work is Phase 2 — Jobs & Matching (WP2.1 under decision-gate preparation). Product feature work beyond WP2.1 — i.e. WP2.2+ — remains NOT AUTHORIZED.