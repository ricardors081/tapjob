# Foundation Decisions — TapJob

**Status:** Accepted foundation baseline  
**Decision date:** 2026-09-30  
**Market:** Brazil-first  
**Goal:** low initial cost, production-quality architecture, strong mobile UX, and a clear path to scale without rewriting the product.

This document resolves the eight foundation topics that must be clear before runtime implementation starts.

## Product positioning guardrail

TapJob is a candidate-side job-search execution platform, not a LinkedIn replacement.

Product-reference hierarchy:
1. AIApply — primary;
2. Huntr — complementary;
3. Simplify — complementary.

The product should maximize realistic capabilities around jobs, application quality, application execution/assistance, automation, tracking, notifications, interview preparation and career-agent workflows.

Do not add social-network mechanics unless a future product decision explicitly changes this boundary.

---

# 1. User Journey & Complete App Scope

## Decision

TapJob ships as a **complete Career Hub**, but implementation remains phased. The product architecture and navigation expose one coherent experience rather than a collection of disconnected tools.

The first-value journey must be fast:

```text
Install / Open
  -> Sign in
  -> Import resume OR create profile manually
  -> Parse resume
  -> User confirms Professional Truth Store facts
  -> Choose goals/preferences
  -> First relevant jobs
  -> Open job
  -> Understand match
  -> Generate tailored application kit
  -> Apply using available Apply Mode
  -> Track application
  -> Detect/record next step
  -> Prepare interview
  -> Outcome / offer / hired
```

## Authentication UX

Initial login methods:
- Google;
- Apple;
- e-mail.

Phone/SMS authentication is **not** required initially because SMS adds cost and operational complexity. Phone/WhatsApp number can still exist as a verified contact/linking attribute later.

## Onboarding

Goal: first meaningful value without forcing a long questionnaire.

### Step 1 — Account
Sign in.

### Step 2 — Resume
Options:
- import PDF/DOCX;
- skip and enter manually.

### Step 3 — Parse & Confirm
AI extracts facts, but the user explicitly reviews/accepts:
- contact;
- experience;
- education;
- skills;
- certifications;
- languages;
- projects.

Unconfirmed AI extraction does not enter the Professional Truth Store as fact.

### Step 4 — Career Goals
Minimum:
- target roles;
- location(s);
- remote/hybrid/on-site;
- employment type;
- salary preference optional;
- seniority;
- relocation/travel;
- work authorization when relevant.

### Step 5 — First Value
Show a personalized feed and clearly explain why jobs match.

Progressive enrichment continues later; onboarding must not demand every optional field.

## Canonical mobile navigation

Bottom tabs:

1. **Home**
2. **Vagas**
3. **Aplicações**
4. **Agente**
5. **Perfil**

Secondary screens:
- Currículos;
- Application Kits;
- Entrevistas;
- Documentos;
- Contatos;
- Analytics;
- Integrações;
- Configurações.

## Home

The Home screen is an action dashboard, not a metrics wall.

Priority cards:
- best new jobs;
- application requiring action;
- upcoming interview;
- follow-up due;
- recruiter/inbox event;
- agent recommendation;
- weekly progress.

## Jobs

Must support:
- personalized feed;
- search;
- filters;
- save/dismiss;
- company/title/location;
- source/freshness;
- match explanation;
- hard blockers;
- matched/missing requirements;
- application mode;
- original source link.

## Resume Studio

Must support:
- master resume;
- versions;
- ATS-safe templates;
- structured editing;
- preview;
- ATS analysis;
- tailoring per job;
- faithful diff;
- PDF;
- later DOCX.

## Applications

Must support:
- list and pipeline;
- timeline;
- notes;
- tasks;
- documents;
- next action;
- interview linkage;
- detected inbox events.

Canonical statuses remain defined in the domain model.

## Agent

The Career Agent is available inside the app and via WhatsApp over the same Career Core.

Example intents:
- find jobs;
- explain a match;
- tailor resume;
- prepare application;
- summarize pipeline;
- prepare interview;
- create follow-up;
- analyze results.

## Interviews

Must support:
- interview detail;
- company/role brief;
- likely questions;
- STAR story bank;
- text practice;
- debrief;
- follow-up draft.

## UX quality rule

Every screen must implement:
- loading;
- empty;
- error;
- offline/degraded state when applicable;
- disabled;
- success;
- accessibility;
- analytics event;
- clear primary action.

A "complete app" does not mean implementing every future feature before beta. It means the product has a coherent complete journey and unfinished modules remain behind explicit feature flags rather than broken placeholders.

---

# 2. Authentication & Infrastructure

## Decision

Start with a low-cost hybrid architecture:

### Compute
**Existing Linux server + Docker Compose**

Initial containers/services:
- FastAPI API;
- async worker;
- PostgreSQL;
- Redis;
- Cloudflare Tunnel connector;
- migration/admin jobs as needed.

No Kubernetes.

### Public ingress/security
**Cloudflare**
- DNS;
- proxy/security edge;
- Cloudflare Tunnel;
- Free Managed WAF rules where applicable;
- Turnstile for abuse-sensitive public web flows;
- rate-limit strategy at edge + backend.

Cloudflare Tunnel is preferred so the origin server does not need public inbound API ports.

### Object storage
**Cloudflare R2**

Use for:
- resumes;
- generated PDFs;
- application documents;
- exports;
- private file artifacts.

Rules:
- private buckets;
- signed URLs;
- no public resume bucket;
- centralized retention;
- malware/file validation pipeline.

Current official R2 baseline (2026-09-30):
- 10 GB-month Standard storage free tier;
- free egress;
- S3-compatible API.

### Authentication
**Firebase Authentication**

Initial providers:
- Google;
- Apple;
- e-mail.

Reasons:
- mature mobile SDKs;
- standards-based auth;
- easy custom-backend verification;
- low initial cost;
- avoids building password/security flows ourselves.

Do not use phone authentication in the first release unless product evidence justifies SMS cost.

Backend stores Firebase UID as external identity, while TapJob owns its internal User UUID.

### Push notifications
**Firebase Cloud Messaging (FCM)**

FCM is the canonical push transport for mobile.

### Product analytics
**Google Analytics for Firebase**, with a strict analytics taxonomy and no resume/e-mail body data.

### Technical observability
**Sentry**
- mobile exceptions;
- backend exceptions;
- tracing;
- performance;
- agent/LLM tracing where useful.

Structured backend logs still remain canonical operational evidence.

### Database
**PostgreSQL 16+** on the Linux server initially.

Requirements:
- persistent volume;
- no public exposure;
- automated migrations;
- encrypted off-site backup;
- restore drills.

### Redis
Redis 7+:
- cache;
- rate limit helpers;
- queue/backend state as selected;
- idempotency helpers where appropriate.

Redis is not a source of durable business truth.

## Backup baseline

Initial:
- automated daily encrypted PostgreSQL backup;
- off-site copy to R2 or another independent object-storage target;
- retention policy defined before production;
- monthly restore test initially, increasing frequency as risk grows;
- configuration/export backup for critical infra.

Do not call a backup strategy complete until restore has been tested.

## Environments

- local;
- staging;
- production.

Production and staging never share:
- database;
- secrets;
- Firebase app config where isolation matters;
- object namespaces.

## AWS position

AWS is **not required initially**.

Migration triggers:
- current server becomes resource-constrained;
- uptime/redundancy requirement increases;
- managed DB reduces operational risk enough to justify cost;
- geographic/enterprise requirements demand it.

Possible next step:
- Lightsail/ECS for compute;
- RDS PostgreSQL for managed database;
- keep application interfaces unchanged.

TapJob must remain cloud-portable.

## Supabase position

Do not use Supabase as the main runtime platform initially because:
- we already own Linux compute;
- PostgreSQL remains canonical;
- Firebase covers auth/push;
- R2 covers object storage;
- another platform would add overlap.

Supabase may be reconsidered if managed PostgreSQL/auth operations become more valuable than the extra platform dependency.

## Firebase position

Use Firebase narrowly:
- Auth;
- FCM;
- Analytics;
- App Check where useful later.

Do **not** make Firestore the canonical TapJob business database.

---

# 3. Design System & Performance

## Decision

TapJob should look custom/premium rather than like a generic component-library demo.

### Visual direction
- light-first;
- minimal;
- spacious;
- precise;
- high-contrast typography;
- restrained motion;
- professional rather than corporate-heavy;
- no generic "AI gradients everywhere".

### Initial brand token baseline

These values are starting tokens, not literals to scatter in screens.

#### Brand
- Primary: `#2563EB`
- Primary strong: `#1D4ED8`
- Accent orange: `#F97316`

#### Semantic
- Success/verified: `#16A34A`
- Warning: `#D97706`
- Error/rejected: `#DC2626`
- Info: `#0284C7`

#### Light surfaces
- Canvas: `#F8FAFC`
- Surface: `#FFFFFF`
- Text primary: `#0F172A`
- Text secondary: `#475569`
- Border: `#E2E8F0`

The actual application consumes semantic tokens, never these hex values directly.

### Typography
**Inter** is the initial product typeface.

Reasons:
- excellent legibility;
- broad language support;
- strong mobile/web availability;
- neutral enough for a career product;
- multiple weights;
- works well with dense job/resume information.

Use a defined typography scale only.

### Spacing
4-point foundation.

Approved semantic scale maps to a finite set such as:
- 4;
- 8;
- 12;
- 16;
- 20;
- 24;
- 32;
- 40;
- 48;
- 64.

### Radius
Initial direction:
- sm: 8;
- md: 12;
- lg: 16;
- xl: 24;
- full: pill/circle.

### Motion
Use motion to show state, not decorate.

Initial duration direction:
- fast: ~160 ms;
- standard: ~220 ms;
- emphasis: ~320 ms.

Respect reduced-motion preferences.

### Icons
Use **one primary icon family**. Initial recommendation: Lucide.

Do not mix multiple libraries screen by screen.

### Mobile implementation
- React Native + Expo;
- latest stable Expo compatible with the chosen dependency set;
- New Architecture;
- Expo Router;
- TanStack Query for server state;
- Zustand only for local/editor UI state;
- React Native Reanimated for justified motion;
- FlashList for long/high-frequency lists when measured benefit exists;
- optimized image/file rendering;
- lazy/deferred work where appropriate.

### Performance quality bar

Targets are verified in release/profile builds, not dev mode.

Rules:
- no heavy computation on render;
- no uncontrolled rerender cascades;
- paginate large feeds;
- use list virtualization;
- cache server state appropriately;
- prefetch high-value next screens;
- skeletons rather than blocking spinners where appropriate;
- background expensive AI/document work;
- measure startup/list/render/network performance;
- test lower/mid Android devices, not only flagship/emulator.

Smoothness target:
- aim for platform-native-feeling 60 FPS interactions where applicable;
- performance regressions are bugs.

---

# 4. Privacy & Security Baseline

## Decision

Security/privacy are foundation acceptance criteria.

## Data minimization

Store only what is required for:
- career profile;
- job matching;
- applications;
- documents;
- user-authorized integrations.

Do not collect sensitive profile attributes "in case they are useful later".

## Professional data

Canonical career facts:
- stored in PostgreSQL;
- explicit provenance;
- user confirmation when derived by AI.

Resume/document files:
- private R2;
- signed URLs;
- no public object listing.

## Gmail

Inbox Intelligence is:
- opt-in;
- disconnected by default;
- readonly minimum scope;
- feature-gated until Google verification/security obligations are satisfied.

Default storage:
- normalized hiring event;
- minimal evidence/excerpt where required;
- avoid persisting full raw e-mail bodies unless a specific feature requires it.

## OAuth tokens
- encrypted server-side;
- never logged;
- revoked/deleted on disconnect/account deletion when possible.

## Mobile
- auth/session secrets in SecureStore/keychain-backed storage;
- no private provider key in app bundle;
- all `EXPO_PUBLIC_*` values considered public.

## Analytics
Never send:
- resume body;
- e-mail body;
- OAuth token;
- legal/sensitive answers;
- raw uploaded document content;
- unnecessary PII.

Use pseudonymous internal IDs where identification is needed.

## Account deletion
Product must support:
- export;
- correction;
- integration disconnect;
- account deletion;
- file deletion;
- token revocation.

Operational retention/deletion schedule is documented before production and validated against LGPD/legal review.

## Security testing
Before production:
- threat model;
- authz/IDOR tests;
- secret scan;
- dependency scan;
- upload tests;
- webhook signature/auth tests;
- rate-limit/abuse tests;
- prompt-injection tests;
- restore test;
- account-deletion test.

---

# 5. Job Source Priority

## Decision

Do not start with dozens of sources.

## P0 — MVP sources

1. **Manual URL / paste job description**
2. **Greenhouse**
3. **Lever**
4. **Ashby**

Reasons:
- strong value quickly;
- clean connector boundaries;
- reusable public/hosted job data;
- already-audited donor code exists.

## P1 — Brazil

5. **Gupy**
   - assisted/deep-link initially unless official authorization permits submission;
   - official partnership/API path investigated in parallel.

Then research/connect:
- Sólides;
- Vagas.com.br;
- InfoJobs;
- Catho;
- relevant company career pages.

Do not build a connector until its dossier documents legal/API/technical behavior.

## P2 — Closed/high-friction platforms

- LinkedIn;
- Indeed;
- Workday;
- SmartRecruiters.

Initial behavior:
- external job link/import;
- analysis;
- application kit;
- assisted browser flow when allowed.

Do not make headless automation a product dependency.

## Source UX

The user should not care which connector provided the job.

Normalize into one Job model while preserving:
- source;
- original URL;
- source IDs;
- freshness;
- apply capability.

---

# 6. Monetization & Entitlements

## Decision

Do not force monetization before proving value, but architect entitlements from the beginning.

## Beta

Initial controlled beta:
**all implemented beta capabilities can be enabled without payment**, subject to reasonable anti-abuse/AI usage caps.

Do not build Stripe/payment checkout in WP0.1.

## Canonical entitlement model

Do not scatter:

```text
if plan == "pro"
```

through feature code.

Use a central entitlement service/policy.

Possible entitlements:
- job alert frequency;
- tailored resumes per period;
- AI career actions;
- application kits;
- inbox intelligence;
- advanced analytics;
- extension-assisted apply;
- interview simulations;
- premium templates.

## Future plan direction

### Free
Core acquisition/organization:
- profile;
- job feed/search;
- saved jobs;
- application tracker;
- master resume;
- basic matching;
- limited AI/tailoring.

### Pro
Career intelligence:
- larger tailoring allowance;
- ATS analysis;
- application kits;
- advanced alerts;
- interview prep;
- Gmail intelligence;
- advanced analytics;
- richer agent usage.

### Automation / Power
Higher-cost automation capabilities:
- extension/assisted workflows;
- higher limits;
- workflow automation;
- priority/advanced agent capabilities.

Exact names, prices and quotas remain commercial decisions after product usage data.

## Cost protection

Even during free beta:
- user-level AI quotas;
- per-provider budget limits;
- caching;
- smaller model for low-value steps;
- expensive model only where needed;
- abuse/rate limits.

---

# 7. Analytics & Operations

## Decision

Use two different kinds of measurement:

### Product analytics
**Firebase Analytics**

Track user/product behavior.

Canonical event taxonomy starts before feature coding.

Minimum event set:

### Acquisition/onboarding
- `sign_up_completed`
- `resume_import_started`
- `resume_import_completed`
- `profile_confirmation_completed`
- `career_preferences_completed`
- `onboarding_completed`

### Jobs
- `job_feed_viewed`
- `job_viewed`
- `job_saved`
- `job_dismissed`
- `match_explanation_viewed`

### Resume
- `resume_created`
- `ats_analysis_completed`
- `resume_tailor_started`
- `resume_tailor_completed`

### Applications
- `application_kit_created`
- `application_started`
- `application_submitted`
- `application_status_changed`
- `application_followup_created`

### Interviews/outcomes
- `interview_detected`
- `interview_prep_started`
- `interview_completed`
- `offer_recorded`
- `hired_recorded`

### Agent
- `agent_session_started`
- `agent_tool_requested`
- `agent_action_confirmed`
- `agent_action_rejected`

Do not send sensitive document/email content as event properties.

## Server-confirmed product events

Critical business facts such as `application_submitted` or `offer_recorded` must also exist in canonical backend domain data/events.

Client analytics is not the source of truth for business state.

## Technical observability

Use:
- Sentry error/performance/tracing;
- structured backend JSON logs;
- request/correlation IDs;
- connector health metrics;
- queue/job metrics;
- AI model latency/token/cost metadata.

## Core product metrics

North Star remains Qualified Progress Rate.

Dashboard metrics:
- relevant jobs/user/week;
- job view -> save;
- save -> application;
- kit -> application;
- application -> response;
- application -> interview;
- interview -> offer;
- offer -> hired;
- time to first relevant job;
- time to application;
- response time;
- performance by job source;
- performance by resume version;
- agent usage and action completion.

Do not infer causality solely from correlation.

## Operational alerts

Alert on:
- API error spike;
- background queue backlog;
- connector failure;
- DB/storage space risk;
- repeated application submission errors;
- Gmail watch/sync failure;
- notification delivery degradation;
- AI cost anomaly;
- auth failure spike.

---

# 8. Engineering Governance

## Decision

When WP0.1 establishes CI, repository governance becomes strict.

## main
- protected;
- PR required;
- no direct feature push;
- required latest-HEAD checks;
- unresolved review conversations block merge;
- prefer squash merge;
- branch must be current with base where required.

## Branch pattern
- `feat/`
- `fix/`
- `docs/`
- `security/`
- `refactor/`
- `spike/`

## CI required checks

### Backend
- Ruff/lint/format check;
- type check;
- pytest unit;
- PostgreSQL/Redis integration tests where applicable;
- Alembic migration validation.

### Mobile
- lint;
- TypeScript typecheck;
- unit/component tests;
- Expo Doctor/dependency compatibility.

### Repository/security
- secret scan;
- dependency vulnerability scan;
- source-reuse provenance check where applicable;
- docs/link checks;
- Docker build;
- later CodeQL/security static analysis where useful.

## Dependency automation
Use Dependabot or equivalent for controlled dependency PRs.

Do not auto-merge major updates.

## Releases
- semantic versions where useful;
- tagged release;
- release notes;
- migrations recorded;
- rollback plan;
- staging verification before production.

## Environments
Promotion direction:

```text
branch/PR
  -> CI
  -> staging
  -> verification
  -> production
```

No local unreviewed deploy to production.

## Review roles

Implementation and final review should be separate roles/agents when practical.

Recommended Claude workflow:
- grill-me for unresolved design;
- feature-dev for implementation;
- code-review before final independent review;
- security skill for sensitive/upstream imports;
- GitHub real diff/CI remains authoritative.

## Definition of Done

No checkpoint is complete only because an agent says it is.

Required:
- real files;
- tests;
- lint/typecheck;
- docs;
- source reuse ledger where applicable;
- security checks;
- CI green;
- real PR review;
- project state update.

---

# 9. Initial Cost Strategy

## Goal
Keep fixed infrastructure cost close to the cost of the Linux server the owner already has.

### Expected initial external services
- Cloudflare Free where sufficient;
- Cloudflare R2 free tier while usage fits;
- Firebase no-cost products/quotas for Auth/FCM/Analytics where applicable;
- Sentry Developer/free tier initially;
- Expo Free plan initially, local builds available if quota is reached;
- LLM usage is the main variable cost and must be metered.

## Cost escalation should be evidence-driven

Pay when one of these becomes true:
- user load exceeds free quota;
- reliability needs managed infrastructure;
- paid feature materially saves engineering/operations time;
- revenue justifies it;
- compliance requires it.

Do not pay for enterprise architecture before product usage requires it.

---

# 10. Decisions Resolved by This Document

This document resolves the previously open foundation questions around:
- initial user journey;
- authentication;
- hosting;
- object storage;
- initial design direction;
- job-source ordering;
- early monetization architecture;
- analytics/observability;
- repository governance.

The next implementation step remains **WP0.1 — Engineering Foundation**.