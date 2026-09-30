# Source Reuse Blueprint — TapJob

**Audit date:** 2026-09-30  
**Purpose:** define exactly what TapJob should reuse from the 10 authorized source repositories without turning the product into a multi-stack Frankenstein.

## 1. Reuse classifications

Every upstream artifact must be classified before adoption:

- **IMPORT** — same language/runtime and suitable boundaries; copy selected code with provenance and adapt naming/config.
- **ADAPT** — same ecosystem, but coupling/state/storage/API boundaries must be replaced.
- **PORT** — logic is valuable but source language/framework does not match TapJob; reimplement behavior behind canonical interfaces and port tests/fixtures.
- **REFERENCE** — product/UX/domain idea only; do not carry source implementation into runtime.

TapJob architecture remains authoritative:
- mobile: React Native + Expo + TypeScript;
- backend: Python + FastAPI + PostgreSQL;
- async workers;
- connector interfaces;
- Professional Truth Store;
- capability-based Apply Engine.

## 2. Pinned upstream audit points

These are the source commits reviewed for this blueprint. When implementation starts, pin the exact imported file to this SHA or re-audit a newer SHA.

| Repository | Audited SHA | License observed | Primary role |
|---|---|---|---|
| Gsync/jobsync | `ef3d7eec5c9aebaa1c6e03fc3ddd62d7dca8e37f` | MIT | SaaS domain, agent tools, ATS discovery, tests/evals |
| roysahar11/appliable | `2a900e892420039d228ceeb63f03b0480eb72fe4` | MIT | Claude workflows, tailoring discipline, WhatsApp UX |
| AbhishekMandapmalvi/AutoApply | `053071ba1bba5733b522d78c3d645002d817e55a` | MIT | Python resume/apply automation modules |
| career-ops-hq/career-ops | `d6a6c12db6d579ae0f3e6875d247409f6b8c9806` | MIT | Career intelligence, grounding, interview, evals |
| wardoep/jobbot | `5932b3782412aeda4ed492df7c788b1248121ecb` | no LICENSE file observed | Python ingestion/matching/alerts |
| codeharsh27/Career-OS | `28efc4d9d71f390d746241f9d6e85153b88078a3` | no LICENSE file observed | Mobile product flows / skill-gap UX |
| Talia04/Basafy | `e11c0e69a968d1325e0ab3662c0e6fce875c7ea9` | no LICENSE file observed | Gmail intelligence + Expo pipeline UX |
| prataps9/Resumint | `cfc581a7e429122e362ffdaf0162978b3ed1865c` | no LICENSE file observed | Expo Resume Studio |
| KaveenDK/CareerTrack-Mobile-App | `2ea6cdc28b8c2dc73febbf5588d70f396759bb4e` | no LICENSE file observed | Expo tracking, reminders, document capture |
| akashpd390/HireHub | `17d9d7283f406e6cfb3ca18a32649fb57dd2edc0` | MIT | Realtime/chat/bookmark/application UX |

Project owner states that authorization from all repository owners has been formalized and retained by e-mail. Do not commit private e-mail evidence or personal data to this repository. Record provenance in the reuse ledger when code is imported.

---

# 3. Canonical donor by capability

This table prevents duplicate competing implementations.

| Capability | Primary donor | Secondary donor | TapJob action |
|---|---|---|---|
| Job source contract + ingestion | JobBot | JobSync | IMPORT JobBot contract/adapters; PORT JobSync edge cases/tests |
| Greenhouse / Lever / Ashby discovery | JobBot | JobSync | IMPORT Python adapters; cross-check against JobSync behavior |
| Job dedupe / normalization | JobBot | Appliable + JobSync | IMPORT/ADAPT JobBot; add test cases from others |
| Hard filters | JobBot | career-ops | IMPORT JobBot gate, then extend with TapJob rules |
| Resume/job lexical scoring | AutoApply | JobBot | IMPORT scorer; recalibrate thresholds |
| LLM job reasoning | career-ops | JobSync | PORT prompts/contracts/evals to AI Gateway |
| Professional Truth Store | career-ops | Appliable | PORT source-backed profile model and truth discipline |
| Resume import/extraction | JobSync | AutoApply | PORT prompts/evals + IMPORT Python parser helpers |
| Resume editor mobile | Resumint | CareerTrack | IMPORT/ADAPT Expo components |
| Resume tailoring | Appliable | career-ops + AutoApply | PORT prompt/workflow; IMPORT Python assembly/scoring helpers |
| Resume rendering | AutoApply | Resumint + JobSync | IMPORT/ADAPT backend renderer; reuse template concepts |
| Application tracker domain | TapJob canonical model | JobSync + Basafy | PORT behavior/UI, do not import upstream state model wholesale |
| Application tracker mobile | Basafy | CareerTrack | IMPORT/ADAPT screens/hooks |
| Career Agent tools | JobSync | career-ops | PORT tool schemas/policies to Python agent gateway |
| Career reasoning | career-ops | Appliable | IMPORT prompt assets / PORT scripts |
| WhatsApp experience | Appliable | JobBot | PORT interaction patterns; build own official adapter |
| Assisted apply field mapping | AutoApply | career-ops | IMPORT/ADAPT selector/field logic; remove silent submit |
| Apply policy / knock-out pre-scan | career-ops | AutoApply | PORT policy into TapJob Apply Engine |
| Gmail lifecycle intelligence | Basafy | JobBot | PORT Basafy pipeline/tests to Python; JobBot only as fallback ideas |
| Push/local reminders | Basafy | CareerTrack | IMPORT Expo logic |
| Interview prep/practice/debrief | career-ops | JobSync | IMPORT prompt assets + PORT data flow |
| Follow-up/outcome patterns | career-ops | JobBot | PORT scripts/rules |
| Analytics | Basafy | JobSync + JobBot | PORT metrics/RPC semantics into canonical SQL |
| Recruiter/contact model | JobSync | HireHub | PORT domain behavior |
| Realtime recruiter chat | HireHub | — | REFERENCE/Future, not V1 |
| Skill-gap UX | CareerOS | career-ops | REFERENCE/PORT concepts |
| Offline-first mobile | CareerOS | Basafy | REFERENCE + use Expo query persistence where useful |

---

# 4. Repository-by-repository adoption plan

## 4.1 Gsync/jobsync

### Classification
**PORT + selected IMPORT of framework-neutral tests/fixtures/prompts.**

### Take
- `src/lib/scraper/greenhouse/*`
- `src/lib/scraper/lever/*`
- `src/lib/scraper/ashby/*`
- `src/lib/scraper/ats/*`
- `src/lib/jobs/jobDedupe.ts`
- `src/lib/agent/tools/*`
- `src/lib/agent/turnMetrics.ts`
- resume-import prompts under `src/lib/ai/prompts/resume-import/*`
- automation-match prompts/evals
- `evals/resume-import/*`
- `evals/job-match/*`
- `evals/agent-chat/*`
- application/interview/contact/task domain behavior and tests
- PDF/export UX concepts for future web.

### Why
JobSync has strong coverage, failure handling and eval infrastructure. Its ATS discovery implementations include concurrency, pagination, timeout and rate-limit behavior worth preserving.

### Do not take wholesale
- Next.js application shell;
- Prisma/SQLite data model;
- NextAuth;
- its User/Profile schema as our canonical DB;
- cron scheduling implementation;
- React web UI for V1 mobile.

### TapJob destination
- Python connector implementations under `services/api/app/integrations/jobs/`;
- eval fixtures under `services/api/evals/`;
- agent tool contract inspiration under `services/api/app/agent/tools/`.

---

## 4.2 roysahar11/appliable

### Classification
**IMPORT for development-time Claude assets; PORT for runtime workflows.**

### Take
- `.claude/agents/scan-jobs/agent.md`
- `.claude/agents/job-description-fetcher/agent.md`
- selected parts of `.claude/agents/job-fetcher/agent.md`
- `.claude/skills/customize-resume/SKILL.md`
- `.claude/skills/quick-apply/SKILL.md`
- daily job orchestration ideas;
- `scripts/scrapers/lib/dedup.js` test scenarios;
- daily/WhatsApp report templates;
- Source of Truth Discipline;
- autonomous-mode guardrails.

### Adapt
Create TapJob-specific skills instead of copying names/paths blindly:
- `.claude/skills/tap-work-package/`
- `.claude/skills/tap-resume-tailor/`
- `.claude/skills/tap-source-reuse/`

### Do not use as production dependency
- WAHA/WhatsApp implementation;
- LinkedIn Chrome automation as core job source;
- filesystem application folders as production database.

### Key idea to preserve
A tailored resume can only use approved profile facts/finalized content. This aligns directly with Professional Truth Store.

---

## 4.3 AbhishekMandapmalvi/AutoApply

### Classification
**Highest-value direct Python donor: IMPORT + ADAPT.**

### Take
Backend candidates:
- `core/resume_parser.py`
- `core/resume_scorer.py`
- `core/resume_assembler.py`
- `core/resume_renderer.py`
- JD analysis helpers used by scoring;
- relevant tests under `tests/test_resume_*.py`;
- `bot/apply/base.py`
- ATS-specific field/selectors from:
  - `bot/apply/greenhouse.py`
  - `bot/apply/lever.py`
  - `bot/apply/ashby.py`
  - `bot/apply/workday.py`
- rate-limit, browser and application error-taxonomy ideas.

### Mandatory modifications
- replace Flask/PyWebView boundaries with FastAPI/service interfaces;
- final submit is disabled in initial assisted mode;
- CAPTCHA means human handoff, never bypass;
- sensitive/EEO/self-ID questions always route through TapJob confirmation policy;
- no storing portal passwords in normal profile fields;
- use TapJob idempotency/audit;
- PostgreSQL models remain TapJob's, not AutoApply's local model.

### Do not take
- Flask route layer;
- desktop shell;
- PyInstaller/pywebview packaging;
- its application DB schema as canonical;
- LinkedIn/Indeed automatic submission as product promise.

---

## 4.4 career-ops-hq/career-ops

### Classification
**Core intelligence donor: IMPORT prompt assets + PORT algorithms.**

### Take
- Master Career Profile / source evidence concepts from `career-profile.mjs` and `DATA_CONTRACT.md`;
- CV fact allowlist / retracted-claim discipline;
- `keyword-match.mjs` heuristics;
- `application-answers.mjs`;
- `application-artifacts.mjs`;
- `prepare-application.mjs`;
- follow-up/outcome/pattern/offer logic;
- provider contracts where useful;
- interview modes:
  - plan;
  - practice;
  - debrief;
- story bank / question bank structure;
- prompt injection treatment of job descriptions/transcripts;
- apply autofill rules from `docs/APPLY_AUTOFILL.md`;
- golden eval fixtures.

### Why
This repository is the strongest source for the reasoning layer and disciplined separation between user facts and system logic.

### Adapt
- store durable data in PostgreSQL, not Markdown/TSV filesystem files;
- translate Node scripts to Python services;
- prompt assets become versioned TapJob prompt resources;
- external side effects go through Career Agent policy tools.

### Do not take
- filesystem as production source of truth;
- product-specific update/fork machinery;
- application probability estimates as a TapJob product feature unless later explicitly approved.

---

## 4.5 wardoep/jobbot

### Classification
**Primary Python donor for jobs pipeline: IMPORT + ADAPT.**

### Take
- `app/sources/base.py` source contract;
- `app/sources/greenhouse.py`;
- `app/sources/lever.py`;
- `app/sources/ashby.py`;
- allowed/public API source adapters after individual policy review;
- normalization/dedupe helpers;
- `app/matching/gate.py`;
- `app/matching/scorer.py`;
- `app/matching/engine.py`;
- `app/matching/llm_rescore.py` concepts;
- source budget / failure isolation;
- alert transport patterns;
- resume parsing utilities if they outperform AutoApply for a specific format;
- migration/test scenarios.

### Secondary only
`app/inbox.py` uses IMAP. TapJob should prefer Gmail OAuth/API from Basafy architecture, so use JobBot inbox only for event taxonomy and fallback ideas.

### Do not take wholesale
- User/Preference/Job ORM models;
- password auth implementation;
- IMAP password storage flow;
- Jinja web UI;
- local plan/billing concepts.

---

## 4.6 codeharsh27/Career-OS

### Classification
**REFERENCE / PORT only.**

### Take conceptually
- information architecture;
- Discover -> Match -> Track -> Prepare product framing;
- cold-start matching UX;
- skill-gap flow;
- offline-first expectations;
- resume wizard organization;
- job detail/viewer interaction patterns.

### Do not import runtime code
The app is Flutter/Dart while TapJob is Expo/React Native. Direct reuse would introduce a second mobile stack.

### Security exclusion
Hardcoded job API credentials were observed in `lib/core/secrets/job_secrets.dart`. Never import those keys or that secret-management pattern.

### Backend
CareerOS FastAPI backend is a small scaffold; TapJob's backend should not be based on it.

---

## 4.7 Talia04/Basafy

### Classification
**Highest-value mobile/inbox donor: IMPORT mobile + PORT backend.**

### Direct mobile candidates
- Gmail connection/session helpers;
- Gmail sync progress/state UX;
- application list/detail UX;
- import review flow;
- notification settings;
- Expo push registration/handling;
- relevant onboarding screens.

### Backend logic to port to Python
From `basafy-backend/supabase/functions/gmail-sync-user/`:
- `query-builder.ts`;
- `parsers.ts`;
- `entity-normalization.ts`;
- `match-resolver.ts`;
- `match-adjudicator.ts`;
- `deep-prefilter.ts`;
- `stage-fetch.ts`;
- `stage-parse.ts`;
- `stage-write.ts`;
- `observability.ts`;
- `rate-limit.ts`;
- `validation.ts`;
- timeline/wrapped-report models;
- their associated tests.

### SQL/data ideas to port
- Gmail sync state;
- job-email events;
- application merge/dedupe;
- notifications/devices;
- insights calculations.

### Mandatory modifications
- Supabase Edge Function coupling becomes FastAPI/worker services;
- Supabase schema becomes Alembic/PostgreSQL canonical schema;
- OAuth refresh tokens use TapJob encryption/storage;
- full e-mail retention minimized per privacy policy.

---

## 4.8 prataps9/Resumint

### Classification
**Primary Resume Studio UI donor: IMPORT + ADAPT.**

### Take
- resume data types as UI draft shapes;
- resume list/editor flows;
- `ResumeScore` and AI suggestion presentation;
- job-tailor screen structure;
- section editors;
- preview/template UX;
- duplicate/version UX;
- LaTeX templates as visual/rendering input;
- Zustand patterns for temporary local editing state.

### Mandatory modifications
- TapJob API is canonical persistence;
- Zustand is local draft/UI state, not durable business source of truth;
- AI calls leave the mobile app and go through backend AI Gateway;
- remove use of private provider keys in `EXPO_PUBLIC_*`;
- remove demo/mock fallbacks that could masquerade as real analysis;
- no AdMob/monetization code unless separately approved.

### Rendering
Final PDF generation should happen in a backend worker. Resumint templates can be ported/adapted there; mobile may retain preview-only rendering.

---

## 4.9 KaveenDK/CareerTrack-Mobile-App

### Classification
**IMPORT selected Expo features; ADAPT data layer.**

### Take
- Expo Router patterns;
- application/job list and detail components;
- search/filter chip UX;
- camera/document capture;
- Expo notification scheduling;
- interview reminder logic;
- follow-up composer ideas;
- dashboard cards/charts where useful.

### Mandatory modifications
- remove Firebase coupling;
- replace jobService/storageService with TapJob API;
- use canonical application states;
- use design tokens instead of hardcoded status/brand colors.

### Do not use as source of truth
Its job state model is much smaller than TapJob's canonical application timeline.

---

## 4.10 akashpd390/HireHub

### Classification
**REFERENCE / PORT, mainly future.**

### Take conceptually
- realtime chat room/message flow;
- recruiter connection concept;
- bookmarks;
- job details/apply bottom-sheet patterns;
- resume selection per application.

### Why not direct import
Flutter/Dart is incompatible with TapJob's Expo mobile baseline.

### Phase
Recruiter realtime chat is not V1. Keep this donor for future networking/recruiter features.

---

# 5. Actual composition of TapJob

## Backend

### Jobs
Start with JobBot Python contracts/adapters.
Add JobSync edge cases, tests and eval ideas.
Do not maintain duplicate Greenhouse/Lever/Ashby implementations.

### Matching
Pipeline:

```text
Hard Filters (JobBot)
  -> deterministic lexical/requirements score (AutoApply + JobBot)
  -> semantic layer when justified
  -> Career reasoning (career-ops)
  -> structured explanation/evidence
  -> JobSync-derived eval gate
```

### Career profile
TapJob database model remains canonical.
Port career-ops source-backed Career Profile concepts and Appliable truth discipline.

### Resume
- parser/scoring/assembly: AutoApply;
- extraction prompts/evals: JobSync;
- tailoring policy: Appliable + career-ops;
- rendering: backend-adapted AutoApply/Resumint templates.

### Agent
- tool architecture: JobSync;
- reasoning/playbooks: career-ops;
- conversational workflow: Appliable;
- TapJob policy/confirmation remains authoritative.

### Apply
- field/selector knowledge: AutoApply;
- knock-out and human-control policy: career-ops;
- TapJob Apply Engine decides mode;
- no upstream code may bypass `OFFICIAL_API / ASSISTED_EXTENSION / DEEP_LINK / MANUAL / DISABLED`.

### Inbox
Basafy pipeline is primary.
JobBot IMAP logic is not the primary architecture.

## Mobile

Foundation remains Expo/React Native.

Composition:
- navigation/shell patterns: CareerTrack + Resumint;
- Resume Studio: Resumint;
- applications/inbox sync: Basafy;
- tracking components/reminders/camera: CareerTrack;
- CareerOS/HireHub: visual/flow references only.

Do not introduce Flutter.

---

# 6. Import order by work package

## WP0.1
Do **not** import feature code.
Only establish repo, CI, tooling and provenance mechanism.

## WP0.2
Create interfaces that upstream code must fit:
- JobSource;
- MatchingEngine;
- ResumeParser;
- ResumeRenderer;
- AiGateway;
- MessagingChannel;
- ApplyConnector;
- InboxConnector.

## WP1.2 / WP1.3
Reuse:
- career-ops Career Profile concepts;
- JobSync resume import prompt/evals;
- AutoApply parser helpers;
- Resumint profile/resume UI patterns.

## WP2.1
Import/adapt JobBot job source framework + ATS adapters.
Port JobSync tests/edge cases.

## WP2.2
Import/adapt JobBot hard filters + AutoApply scorer.
Port career-ops reasoning and JobSync evals.

## WP3.x
Import/adapt Resumint mobile editor.
Import/adapt AutoApply resume backend.
Port Appliable + career-ops tailoring rules.

## WP4.x
Port JobSync agent tool architecture.
Import career-ops prompt assets.
Adapt Appliable conversational patterns.
Import/adapt Basafy/CareerTrack mobile tracker pieces.

## WP5.x
Import field mapping/selectors from AutoApply.
Port career-ops autofill policy.
Build TapJob browser extension around canonical Apply Engine.

## WP6.x
Port Basafy Gmail pipeline + tests.
Import/adapt Basafy mobile Gmail UX/push.

## WP7.x
Import/port career-ops interview plan/practice/debrief.
Adapt JobSync interview/contact/task ideas.

---

# 7. Provenance protocol

Every code-bearing reuse PR must add a row to `docs/SOURCE_REUSE_LEDGER.md`.

Required:
- upstream repo;
- upstream SHA;
- upstream path;
- destination;
- mode: IMPORT / ADAPT / PORT;
- permission/license basis;
- changes made;
- upstream tests ported;
- security review result.

A copied file without ledger entry is a merge blocker.

## 8. Security rules for source adoption

Before importing:
1. secret scan upstream file;
2. inspect auth/storage assumptions;
3. inspect outbound network calls;
4. inspect telemetry;
5. inspect file paths and shell execution;
6. inspect LLM secret placement;
7. inspect user-data retention;
8. run dependency review for new packages.

Specific known hazards:
- CareerOS hardcoded API credentials;
- Resumint client-side AI provider keys/calls;
- AutoApply silent/final browser submission behavior;
- JobBot IMAP credential model;
- Appliable browser automation/WhatsApp self-hosted assumptions.

## 9. Anti-Frankenstein rules

- one canonical data model: TapJob;
- one application state machine: TapJob;
- one connector contract per external capability;
- one AI Gateway;
- one notification abstraction;
- one mobile stack;
- one auth decision;
- no upstream UI state becomes server truth;
- no duplicate implementation survives because "both repos had it".

## 10. Definition of successful reuse

Reuse is successful only when it:
- reduces implementation time;
- fits canonical interfaces;
- has tests;
- has provenance;
- does not import stale architecture;
- does not weaken security/privacy;
- can be maintained without knowing the original repository.