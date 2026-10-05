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

## WP1.1 — Identity & User Account (IMPLEMENTED, pending independent review)

Independently reviewed: not yet. See `docs/adr/0009-identity-auth-user-account.md` (D-051) and `docs/PROJECT_STATE.md` for the full checkpoint summary.

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

## WP1.2 — Career Profile & Truth Store

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

## WP1.3 — Resume Import

### Deliver
- PDF/DOCX upload;
- text extraction;
- structured parsing;
- review before save;
- file storage;
- master resume creation.

### Reuse plan
- PORT JobSync resume-import prompts/evals.
- IMPORT/ADAPT AutoApply parser helpers where format coverage is useful.
- Use Resumint only for mobile resume/profile editing UX, not backend parsing.

### AI eval
Golden dataset for extraction correctness.

---

## WP2.1 — Jobs Domain & Connector Framework

### Deliver
- Company;
- Job;
- JobSource;
- connector capability registry;
- scheduler;
- ingestion;
- dedupe;
- source health.

### Reuse plan
- IMPORT/ADAPT JobBot's Python `JobSource` contract, normalization utilities and Greenhouse/Lever/Ashby adapters as primary implementation.
- PORT JobSync timeout/pagination/rate-limit edge cases and connector tests.
- Use Appliable dedupe cases as supplemental fixtures, not a second ingestion stack.

### First adapters
- Greenhouse;
- Lever;
- Ashby;
- manual URL/paste import.

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

**WP1.1 — Identity & User Account (IMPLEMENTED, pending independent review).** WP0.1 and WP0.2 (both WP0.2-A and all of WP0.2-B) are independently verified, merged, and post-merge CI verified — Phase 0 / Foundation is complete. WP1.1 is the only work package authorized so far, and is now implemented (`docs/adr/0009-identity-auth-user-account.md`, D-051) pending independent review — it is not VERIFIED by this report alone. WP1.2 and every downstream work package remain **not authorized** until their own dependencies are independently verified — implementing WP1.1 does not authorize the rest of WP1.x.

WP0.1 is independently verified and merged. WP0.2-A is independently verified and merged (PR #9, `main` commit `706cc33d659c59db76cf4cd687a938f7558ba37b`). WP0.2-B Checkpoint A (internal ports/capability model) is independently verified and merged via squash (PR #12, `main` commit `a8bfbe621e5a83ae5f205adb1919ce3250684efb`; post-merge CI green, run `37055251128`, 7/7). WP0.2-B Checkpoint B (worker-side TapJob correlation propagation; ADR-0006 deployment/CD contract, not a real deploy) is independently verified and merged via squash (PR #15, `main` commit `3edaf1cee33b892a5d753b07b9b9946cbcd28887`; CI green pre-merge, run `37230556089`, 8/8, and post-merge, run `37231990603`, 8/8). WP0.2-B overall — and WP0.2 as a whole — is **VERIFIED**. WP1.1 (internal `User`/Firebase `ExternalIdentity`/bootstrap/session lifecycle/devices/consent/deletion skeleton/rate limiting/minimal mobile auth surface) is implemented pending independent review, CI green at latest HEAD, merge, and post-merge CI green — the same pattern every prior checkpoint followed. Product feature work beyond WP1.1 (i.e. WP1.2+) remains not authorized.