# Roadmap — TapJob

Roadmap is capability-based, not calendar promise.

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
- basic mobile shell.

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

## Phase 4 — Tracking & Agent

Deliver:
- applications pipeline;
- timeline/tasks;
- Career Agent mobile;
- core tools;
- confirmation policies;
- WhatsApp adapter;
- deep links.

Exit:
same job/application can be managed in mobile and WhatsApp.

## Phase 5 — Assisted Apply

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

## Phase 6 — Inbox Intelligence

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

## Phase 7 — Interviews & Analytics

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

## Phase 8 — Brazil official integrations

Priority:
- Gupy partnership/integration exploration;
- official apply where authorized;
- additional Brazil job sources;
- employer/education/outplacement partnerships.

Exit:
at least one meaningful official application integration beyond hosted ATS discovery.

## Phase 9 — Expansion

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

Commercial beta target comprises Phases 0–4 with selected Phase 5 capabilities.

Inbox Intelligence can enter beta behind feature flag depending on OAuth/security readiness.

## Anti-scope-creep rule

A feature can enter current phase only if:
- required for exit journey;
- dependency for approved feature;
- security/compliance necessity.

Everything else moves to backlog.