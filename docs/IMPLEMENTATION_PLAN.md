# Implementation Plan — TapJob

This file translates roadmap into executable work packages.

## Delivery sequence

Customer-surface priority is defined in `DELIVERY_ORDER.md`:

```text
Foundation -> Minimum Career Core -> WhatsApp-first product -> Mobile App -> Browser Extension
```

### Canonical Principle of Channels
TapJob is ONE product with ONE Career Core. WhatsApp, Mobile App, and Browser Extension are surfaces/channels over the same canonical state and business rules:

```text
WhatsApp ──┐
Mobile ────┼──> TapJob Core
Extension ─┘
```

Nenhum canal pode possuir:
- profile truth próprio;
- matching próprio;
- application state próprio;
- resume truth próprio;
- Career Agent policy própria;
- business rule própria.

O backend/Core permanece channel-neutral. Compartilham a mesma User identity, Truth Store, Jobs, Matching, Resume, Application Kit, Applications/Tracker, Career Agent e política de notificações. Canais variam APRESENTAÇÃO e CAPACIDADES DE INTERFACE, nunca a verdade canônica.

### WhatsApp-First Priority & Mobile Readiness
- **WhatsApp-first product:** A prioridade absoluta após a fundação do Career Core é colocar o WhatsApp Alpha funcionando de ponta a ponta antes da construção pesada da experiência visual mobile. O WhatsApp Alpha consome o mesmo backend/Core channel-neutral, não sendo um chatbot isolado.
- **Mobile Readiness é parallel-safe:** Atividades de infraestrutura e prontidão de build mobile (Expo Development Build, Android/iOS native readiness, EAS config, env config, Firebase auth wiring, generated OpenAPI client, session/secure-storage, navigation shell, tokens, network/error foundations, deep-links, push plumbing, Sentry, build CI) podem avançar em paralelo quando não bloquearem nem atrasarem o WhatsApp Alpha. Isso NÃO significa que a experiência de produto mobile foi iniciada.
- **Mobile Product Experience continua depois do WhatsApp Alpha:** Construção visual completa de Job Feed, Job Detail, Match visual, Resume Studio, Kanban/Tracker, analytics, document manager, large Application Kit review e telas de produto pertence ao estágio posterior ao WhatsApp Alpha funcional.

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
- **WP2.1-A — Jobs Domain, Manual Intake & Normalization Foundation (VERIFIED):**
  - Canonical `Company`, `Job`, `JobOrigin`, `IdempotencyKey` entities (migration `0005`);
  - Strong identity via `(provider, external_id)` with nullable `external_id` for manual intakes;
  - Corporate domain identity protection against multi-tenant ATS hostnames;
  - Deterministic text normalization, workplace type classification, and non-unique similarity fingerprint;
  - `SafeHttpClient` SSRF security boundary (DNS pre-resolution, IP allowlist, connection pinning, SNI/TLS preservation, 2 MiB streaming decoded limit, manual redirects);
  - Authenticated manual paste intake (`POST /api/v1/jobs/intake/paste`, 30 req/h rate limit);
  - Authenticated safe URL intake (`POST /api/v1/jobs/intake/url`, 15 req/h rate limit);
  - Authenticated job detail retrieval (`GET /api/v1/jobs/{jobId}`).
  - See `docs/adr/0012-jobs-domain-manual-intake.md` (D-054). Independently reviewed, merged via squash into `main` (PR #25, squash commit `38da12050d9613f502bce956edc7167c8427f3df`), post-merge CI run `37716255293` 8/8 green. **WP2.1-A is VERIFIED.**
- **WP2.1-B — External Hosted ATS Connectors & Ingestion Scheduler (VERIFIED):**
  - Greenhouse, Lever, Ashby adapter implementations conforming strictly to `JobSource` port protocol;
  - Base ATS HTTP client with deadline budgeting, 5s connect / 10s read bounded timeouts, 5 MiB payload limits, `trust_env=False`, and normalized `PortError` mapping;
  - Connector capability registry (`JobSourceRegistry`) and source health tracking;
  - Ingestion pipeline (`ingest_job_source`) persisting global discovery into `Company`, `Job` (`created_by_user_id=None`), and `JobOrigin` (`(provider, external_id)`) with corporate domain sanitization;
  - Liveness consensus: absence reconciliation tracking missing jobs (`consecutive_not_found_count`) and transitioning to `CLOSED` after 3-strike threshold;
  - Celery worker tasks `jobs.ingest_source` and periodic `jobs.sweep_scheduled_ingestion` in `tapjob_worker` with centralized Celery Beat scheduler, stable task-ID-derived `cycle_id`, and dedicated-connection PostgreSQL advisory locks.
  - See `docs/adr/0013-external-ats-connectors-ingestion.md` (D-055). Independently reviewed, merged via squash into `main` (PR #36, squash commit `fccc14bcdf581245bdefd7b67395ca96eafcf2f7`, approved PR HEAD `8586c759f8ee28cfcead9310f4740f479cf4dfde`). CI green pre-merge (run `37800774060`, 8/8) and post-merge (run `37822195467`, 8/8). Tree equivalence confirmed (`e86f20fb132438725128a582638dfc8cf338e8d6`). **WP2.1-B is VERIFIED.**

### Reuse plan
- ADAPT JobBot's normalization utilities, text cleanup, and workplace keywords (`docs/SOURCE_REUSE_LEDGER.md`).
- PORT JobSync candidate dedupe heuristics and test cases.
- PORT Appliable dedupe cases as supplemental fixtures.

---

## WP2.2 — Matching Engine (VERIFIED)

Decision gate defined and accepted in ADR-0014 (`docs/adr/0014-matching-engine.md`, D-056). Architecture, contracts, scoring models, explainability, cache, concurrency, evals, and implementation plan are implemented on branch `feat/wp2-2-matching-engine`. Independently reviewed across multiple rounds and approved with zero blocking findings (CRITICAL=0, HIGH=0, MEDIUM_BLOCKING=0). Merged via squash into `main` as PR #40 (squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, approved implementation HEAD `55744b6872b49854cad45c2ef69cd3c132f3c073`). Post-merge CI run `37997452936` on `main` is green 8/8. **WP2.2 is VERIFIED.** Downstream work packages (WP2.3+ Feed and beyond) remain unstarted.

### Deliver
- 5 canonical hard-filter knock-out gates (`WORK_AUTHORIZATION`, `WORKPLACE_TYPE`, `LOCATION`, `SALARY_MINIMUM`, `ROLE_ALIGNMENT`) governed by a formal Capability Matrix;
- Strict classification: `HARD_BLOCKER`, `SOFT_MISMATCH`, and `UNKNOWN` (undisclosed job attributes never block; cross-currency or cross-period salary evaluates to `UNKNOWN`; lack of declared relocation is `SOFT_MISMATCH`; free-form work authorization evaluates to `UNKNOWN`);
- `ROLE_ALIGNMENT_HARD_BLOCK_ENABLED = False` in V1 (gate outcomes strictly limited to `PASSED`, `SOFT_MISMATCH`, `UNKNOWN`; occupation taxonomy hard blocker deferred);
- Internal pure domain contract `DerivedJobMatchSignals` (`JobSalarySignal`, `JobWorkAuthSignal`, `JobSeniorityLevel`, `DerivedJobMatchSignals`) in `tapjob_api.matching.signals` distinguishing `required_skills`, `preferred_skills`, and `unclassified_skills` (heading-less descriptions never invent required skills), with fail-closed bounded regexes, zero LLM, and zero network calls;
- Internal pure domain contract `CandidateMatchSignals` (`derive_candidate_match_signals`) reducing canonical `TruthSnapshot` boundary from `get_truth_snapshot` deterministically with fact-order independence, taxonomy/alias convergence for skills, deterministic experience ordering with ongoing duration frozen by `evaluation_as_of_date`, calendar month union duration for overlapping roles (counting each month at most once; conservative fail-closed 0 months for `YearMonth(month=None)`), `CandidateDomainTokenSignal(canonical_token, fact_ids)` preserving evidence grounding from `ExperienceFactValue` and `ProjectFactValue` (`CareerProfile.summary` excluded from V1 score), canonical salary collapse/ambiguity resolution, Acceptable Set Semantics combining `WorkplaceTypePreferenceValue` and `LocationPreferenceValue.workplace_types`, disjunctive location alternatives with total sort key `(country or "", region or "", city or "")` (preventing `None < str` TypeError), deterministic fail-closed delimiter-based token location comparison, and question-key-aware work authorization grouping with conflict resolution;
- Question-key-aware candidate interpretation via `WORK_AUTHORIZATION_ANSWER_MAP`, canonical jurisdiction comparison, and fail-closed sponsorship availability extraction (unstated availability leaves `sponsorship_offered = None`);
- `SalaryPeriod` enum alignment (`JobSalarySignal.period: SalaryPeriod | None`, `JOB_SALARY_PERIOD_MAP`, hourly policy) with `SALARY_NUMERIC_SCORE_EFFECT = NONE` (salary deficit generates `SOFT_MISMATCH` with informational gap in missing requirements, zero overall score penalty; isolated `$` fails closed to `UNKNOWN`);
- Explicit typed total mapping `WORKPLACE_TYPE_MAPPING` (`WorkplaceType.ONSITE` -> `JobWorkplaceType.ON_SITE`);
- Formal `CriticalUnknown` definition (`WORK_AUTHORIZATION_AMBIGUOUS`, `SPARSE_PROFILE`), where `SPARSE_PROFILE` is emitted strictly for candidates with 0 confirmed skills AND 0 confirmed experiences (jobs with 0 extracted skills evaluate neutrally without emitting `SPARSE_PROFILE`);
- Deterministic 5-component lexical scoring (integer 0..100) using pure integer arithmetic from start to finish (ZERO `float` types, ZERO uses of Python builtin `round()`, canonical `round_half_up_ratio` helper, basis points fixed-point arithmetic 0..10,000, and exact round-half-up to integer 0..100):
  - Title Alignment (25%): Dice coefficient on core tokens with 90% weighting for past titles via `round_half_up_ratio`;
  - Skill Overlap (35%): 6-case decision tree with 75/25 required/preferred ratio, unclassified skills at 0% weight when required exists, and missing requirements strictly from $R \setminus S_C$;
  - Experience & Domain (20%): domain keyword overlap with pure integer duration adequacy percent and zero-experience precedence;
  - Seniority Alignment (10%): candidate seniority derivation from calendar month union and 7x7 score matrix;
  - Workplace & Location (10%): 50/50 pure integer split via `round_half_up_ratio(wp + loc, 2)`;
- Neutral imputation for omitted non-mandatory job fields;
- `MINIMUM_ELIGIBLE_SCORE = 50` and formal eligibility decision table (`ELIGIBLE`, `INELIGIBLE`, `REVIEW_REQUIRED`);
- Dedicated `job_match_fingerprint` incorporating `job_id`, normalized title, normalized location, `workplace_type`, and cleaned description hash;
- Bilingual Portuguese/English normalization and technology alias map;
- Semantic layer (embeddings) and LLM re-score: DEFERRED (evaluated in WP2.3 if bulk search justifies it);
- Formal separation of `DeterministicMatchCore` vs presentation-layer LLM synthesis;
- Grounded deterministic template explainability linking strengths directly to `ProfileFact.id` values and normalized job fields; no LLM dependency in the WP2.2 runtime baseline (optional LLM explanation synthesis via `AiGateway` `AiPurpose.JOB_MATCH_EXPLANATION` remains an architectural capability for future work packages);
- Reproducibility invariant bound to `policy_version` (`2026.1`), `profile_version`, `job_match_fingerprint`, and `evaluation_as_of_date` (freezing ongoing employment calculation);
- Canonical payload serialization (`DeterministicMatchCore.to_canonical_payload()`) covering all 14 core fields for `deterministic_result_digest`;
- PostgreSQL canonical `match_results` persistence with unique constraint `uq_match_results_user_job_versions`;
- Concurrency convergence verification via `INSERT ... ON CONFLICT DO NOTHING` asserting `deterministic_result_digest` equality;
- Volatile Redis cache (24h TTL) with pre-cache liveness/visibility revalidation (evicting closed/expired jobs);
- Synchronous single-match execution (< 50ms p95) via authenticated `POST /api/v1/jobs/{jobId}/match`;
- Untrusted job descriptions treated as inert text (prompt-injection impervious);
- 15 golden deterministic eval scenarios in pytest and 6 architectural invariant test suites.

### Reuse plan
- ADAPT JobBot's hard-filter gate and matching pipeline (`app/matching/gate.py`, `scorer.py`, `engine.py`).
- ADAPT AutoApply's lexical scoring and skill weighting concepts (`core/resume_scorer.py`) into bounded pure-Python integer arithmetic.
- PORT career-ops's fact-grounding discipline and untrusted job text separation (`keyword-match.mjs`).
- PORT JobSync's job-match eval scenarios (`evals/job-match/*`) into a deterministic pytest test suite (`tests/test_matching_evals.py`).

### Acceptance
- Single-match latency < 50ms (p95);
- Unconfirmed facts structurally excluded from matching;
- Hard blockers reliably force `overall_score = 0` and `eligibility = INELIGIBLE`;
- Undisclosed job attributes never trigger hard blockers;
- Concurrent evaluations for same version idempotently resolve with verified result digest equality;
- Pre-cache guard prevents serving stale cache for closed or unauthorized jobs;
- 15 golden eval scenarios and all architectural invariant test suites pass with 100% determinism;
- CI 8/8 green.


---

## Next Product Goal — Minimum WhatsApp Alpha Vertical Slice

O próximo grande marco de entrega de produto é um **Minimum WhatsApp Alpha Vertical Slice**, viabilizando o primeiro produto utilizável de ponta a ponta sem exigir a conclusão prévia e exaustiva de todos os WPs avançados.

### Target User Journey
```text
User says "oi"
  -> channel identity
  -> TapJob User linkage
  -> resume/basic profile
  -> PDF/DOCX intake
  -> fact extraction
  -> confirmation
  -> target role/preferences
  -> job import/recommendation
  -> Matching Engine
  -> match explanation
  -> minimal grounded resume tailoring
  -> basic Application Kit
  -> document/link handoff
  -> basic application tracking
  -> reminders/follow-up
  -> Career Agent tools
  -> WhatsApp interaction surface
```

### Natureza do Checkpoint: Cross-WP Minimum Slice
- Este checkpoint antecipa estritamente as capacidades mínimas necessárias de WP2.3, WP3.x e WP4.x para viabilizar a jornada conversacional completa no WhatsApp sobre o Core canônico.
- **IMPORTANTE:** Não remove nem reduz os escopos completos existentes de:
  - WP2.3 (Job Feed & Alerts completo);
  - WP3.1 (Resume Studio completo);
  - WP3.2 (ATS & Tailoring completo);
  - WP3.3 (Application Kit completo);
  - WP4.1 (Application Tracker completo);
  - WP4.2 (Career Agent Core completo);
  - WP4.3 (WhatsApp-first Product Surface completo).
- As versões completas desses work packages continuam existindo e serão executadas conforme o roadmap.

### Macro-Delivery Sequencing Direction
Após a aprovação deste alinhamento, a execução deve ser estruturada em macro-entregas coerentes:
- **CHECKPOINT A — WhatsApp Alpha Core:**
  Capacidades mínimas compartilhadas no backend/Core:
  - channel identity model e linkage com TapJob User canônico;
  - backend mínimo de feed/alert necessário ao vertical slice;
  - grounded resume tailoring mínimo;
  - basic Application Kit;
  - minimal Application Tracker;
  - Career Agent Core e tool definitions necessários ao alpha.
- **CHECKPOINT B — WhatsApp Alpha Surface + Mobile Readiness:**
  - `MessagingChannel` adapter implementation;
  - Evolution API initial adapter isolado atrás de `MessagingChannel`;
  - contratos normalizados inbound/outbound;
  - webhook de entrada e autenticação de eventos;
  - conversational orchestration;
  - confirmações interativas;
  - document handoff (envio de kit/currículo);
  - reminders e follow-up no WhatsApp;
  - deep links;
  - Mobile Readiness infrastructure (paralela e segura).

### Regras de Neutralidade de Provedores e Ferramentas Auxiliares
- **Evolution API Neutrality:** A Evolution API é o transporte inicial de desenvolvimento/alpha. Deve residir estritamente atrás do port `MessagingChannel`. Nenhuma regra de negócio ou Career Core pode depender de Baileys, quirks de QR/sessão, formatos de webhook da Evolution, IDs de provedor ou semântica de retentativa do provedor. O caminho arquitetural para migração para a WhatsApp Cloud API oficial é preservado.
- **Papel do n8n:** O n8n é uma ferramenta auxiliar de integração e automação operacional. O n8n **NÃO** é dono canônico de: estado de usuário, matching, Professional Truth Store, estado de candidaturas, política do Career Agent ou regras críticas de negócio. Toda lógica essencial reside no Core versionado e testado do TapJob.

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
- **Mobile Readiness (parallel-safe with WhatsApp):**
  - Expo Development Build;
  - Android native/build readiness;
  - iOS native/build readiness;
  - EAS configuration/profiles quando aplicável;
  - environment configuration;
  - Firebase mobile/auth wiring;
  - generated OpenAPI API client integration foundation;
  - session/secure-storage foundation;
  - navigation shell;
  - design tokens/design-system primitives;
  - error/loading/network foundations;
  - deep-link infrastructure;
  - push-notification plumbing quando houver backend contract;
  - Sentry/observability;
  - build/compile CI;
  - package/bundle identifiers/configuration quando necessário;
  - icon/splash/native config quando necessário.
  *(Essas atividades de prontidão técnica NÃO significam que a experiência de produto mobile foi iniciada).*
- design system;
- security threat modeling;
- AI eval datasets;
- source/licensing registry;
- infrastructure experiments;
- partnership outreach.

## First authorized next step

**Next Product Goal — Minimum WhatsApp Alpha Vertical Slice.**

WP0.1, WP0.2 (WP0.2-A e WP0.2-B), WP1.1, WP1.2, WP1.3, WP2.1 (WP2.1-A e WP2.1-B) e WP2.2 estão todos independentemente verificados, mergeados e validados com CI verde na `main`:
- WP1.1 — Identity & User Account is **VERIFIED** (`docs/adr/0009-identity-auth-user-account.md`, D-051; PR #17, squash commit `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`; CI green pre-merge run `37254156902` 8/8 e post-merge run `37312677497` 8/8).
- WP1.2 — Career Profile & Truth Store (`docs/adr/0010-career-profile-truth-store.md`, D-052) is **VERIFIED** (PR #19, squash commit `5c06c805ea2a68fcf100ec56cff5eaf715e962c2`, corrective PR #20 squash commit `f30590577cf25733de63025f987c251ebe0e89af`, post-merge CI run `37397784331` 8/8 green).
- WP1.3 — Resume Import (`docs/adr/0011-resume-import.md`, D-053) is **VERIFIED** (PR #22, squash commit `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, approved PR HEAD `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`; pre-merge CI run `37538366658` 8/8, post-merge CI run `37551974662` 8/8).
- WP2.1-A — Jobs Domain, Manual Intake & Normalization Foundation (`docs/adr/0012-jobs-domain-manual-intake.md`, D-054) is **VERIFIED** (PR #25, squash commit `38da12050d9613f502bce956edc7167c8427f3df`; post-merge CI run `37716255293` 8/8 green).
- WP2.1-B — External Hosted ATS Connectors & Ingestion Scheduler (`docs/adr/0013-external-ats-connectors-ingestion.md`, D-055) is **VERIFIED** (PR #36, squash commit `fccc14bcdf581245bdefd7b67395ca96eafcf2f7`, approved PR HEAD `8586c759f8ee28cfcead9310f4740f479cf4dfde`; pre-merge CI run `37800774060` 8/8, post-merge CI run `37822195467` 8/8).
- WP2.2 — Matching Engine (`docs/adr/0014-matching-engine.md`, D-056) is **VERIFIED** (PR #40, squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, approved PR HEAD `55744b6872b49854cad45c2ef69cd3c132f3c073`; post-merge CI run `37997452936` on `main` is green 8/8; independent review APPROVED with 0 critical, 0 high, 0 medium-blocking findings).

A fundação do Career Core e o Matching Engine determinístico estão completos.
- **Próximo objetivo autorizado:** **Minimum WhatsApp Alpha Vertical Slice** (cross-WP minimum slice).
- **Parallel-safe:** Mobile Readiness apenas.
- **Mobile Product Experience:** Permanece estritamente após o WhatsApp Alpha vertical slice estar funcional (Job Feed visual, Job Detail completo, Match visual, Resume Studio, Kanban/Tracker, analytics, document manager, large Application Kit review, settings/integrations UI e telas de produto).
- **Browser Extension:** Permanece após as superfícies Mobile/Core conforme já definido.
- WPs downstream em sua totalidade (WP2.3+ visual feeds, WP3.x visual studios, WP4.x full mobile trackers) permanecem **NÃO AUTORIZADOS** antes do WhatsApp Alpha slice.