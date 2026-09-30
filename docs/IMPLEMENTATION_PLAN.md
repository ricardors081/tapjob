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

### Decide
- OpenAPI/client generation;
- AI gateway abstraction;
- canonical interfaces required before upstream code enters runtime: `JobSource`, `MatchingEngine`, `ResumeParser`, `ResumeRenderer`, `AiGateway`, `MessagingChannel`, `ApplyConnector`, `InboxConnector`.

### Deliver
- ADRs;
- contract skeleton;
- error envelope;
- pagination conventions;
- observability conventions.

---

## WP1.1 — Identity & User Account

### Deliver
- sign-up/login;
- session lifecycle;
- account settings;
- locale/timezone;
- device registration;
- deletion skeleton;
- consent model.

### Security
- ownership enforcement;
- session revocation;
- rate limit;
- audit.

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

**WP0.1 only.**

Feature work should not begin before repository foundation and CI are independently verified.