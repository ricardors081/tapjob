# Roadmap — TapJob

Roadmap is capability-based, not calendar promise.

## Delivery priority overlay

The canonical customer-surface order is defined in `DELIVERY_ORDER.md`:

```text
Foundation
  -> Minimum Career Core
  -> WhatsApp
  -> Mobile App
  -> Browser Extension
```

The capability phases below remain valid. Their implementation checkpoints must be sliced so the WhatsApp vertical journey is reached before the full mobile surface, without weakening domain/security/test requirements.

## Phase 0 — Foundation

Goal: make repository safe for multi-agent execution.

Deliverables:
- canonical docs;
- governance;
- architecture baseline;
- ADRs;
- project skeleton;
- CI;
- environments;
- secret policy;
- quality gates.

Exit:
- repo scaffolded;
- CI green;
- local dev reproducible;
- no production features claimed.

## Phase 1 — Career Core

Deliver:
- auth;
- CareerProfile;
- ProfileFacts / Truth Store;
- resume import;
- preferences;
- canonical database;
- channel-ready contracts and identity model;
- only the minimal mobile workspace/shell required by repository foundation; full customer mobile experience comes after WhatsApp.

Exit journey:
user creates/imports profile and sees confirmed structured facts.

## Phase 2 — Jobs & Matching

Deliver:
- connector framework;
- Greenhouse;
- Lever;
- Ashby;
- manual URL import;
- normalization/dedupe;
- feed/search;
- matching engine;
- explainability;
- alerts.

Exit:
relevant jobs appear with explainable scores.

## Phase 3 — Resume & Application Kit

Deliver:
- Resume Studio;
- ATS analysis;
- tailored resume;
- PDF;
- cover letter;
- application answers;
- company brief;
- kit snapshot.

Exit:
user opens a job and gets a faithful application kit.

## Phase 4 — Agent, Tracking & WhatsApp-first Surface

Deliver:
- applications pipeline;
- timeline/tasks;
- core Career Agent tools;
- confirmation policies;
- minimal application tracking/timeline;
- WhatsApp adapter and channel identity;
- WhatsApp onboarding/commands/documents/alerts;
- deep links ready for later mobile handoff.

Exit:
a user can obtain meaningful TapJob value end-to-end through WhatsApp without requiring the mobile app.

## Phase 5 — Mobile App

Deliver:
- polished Home;
- job feed/search/filter;
- Resume Studio;
- applications/Kanban/timeline;
- agent chat;
- profile/settings;
- interview/document/analytics surfaces as approved;
- deep-link handoff from WhatsApp.

Exit:
the same Career Core becomes a premium visual control plane on mobile.

## Phase 6 — Assisted Apply

Deliver:
- Apply Engine capability registry;
- deep-link flows;
- browser extension;
- safe autofill;
- resume attachment;
- manual submission evidence;
- sync back to tracker.

Exit:
user can complete unsupported portal applications with substantially less repetitive work.

## Phase 7 — Inbox Intelligence

Deliver:
- Gmail OAuth;
- restricted-scope compliance work;
- initial/backfill sync;
- event classifier;
- dedupe;
- application auto-link;
- push;
- follow-up tasks.

Exit:
common hiring e-mails update pipeline accurately with review path for ambiguity.

## Phase 8 — Interviews & Analytics

Deliver:
- interview scheduler/internal calendar;
- prep;
- STAR bank;
- simulator text;
- debrief;
- funnel analytics;
- source/resume performance.

Exit:
user can understand and improve the whole search funnel.

## Phase 9 — Brazil official integrations

Priority:
- Gupy partnership/integration exploration;
- official apply where authorized;
- additional Brazil job sources;
- employer/education/outplacement partnerships.

Exit:
at least one meaningful official application integration beyond hosted ATS discovery.

## Phase 10 — Expansion

- Outlook/Calendar;
- voice interview practice;
- salary negotiation;
- advanced experiments;
- international sources;
- Spanish/English localization;
- partner ecosystem.

## Cross-phase workstreams

Always active:
- security/privacy;
- AI evals;
- connector monitoring;
- design system;
- observability;
- cost control;
- documentation;
- licensing/provenance.

## MVP definition

The MVP is not "all roadmap phases".

Commercial beta target is WhatsApp-first after the minimum Career Core, followed by the mobile app; selected Assisted Apply capabilities can enter once the profile/resume/application contracts are stable.

Inbox Intelligence can enter beta behind feature flag depending on OAuth/security readiness.

## Anti-scope-creep rule

A feature can enter current phase only if:
- required for exit journey;
- dependency for approved feature;
- security/compliance necessity.

Everything else moves to backlog.