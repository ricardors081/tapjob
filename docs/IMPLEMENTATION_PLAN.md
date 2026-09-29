# Implementation Plan — Tap Vaga

This file translates roadmap into executable work packages.

## Rules

- Work packages are sequential unless explicitly marked parallel-safe.
- Each WP must have its own branch/PR or a tightly scoped PR series.
- Do not start a downstream WP while dependencies are unverified.
- Update `PROJECT_STATE.md` after each accepted WP.

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
- secret scan;
- health checks;
- structured logging.

### Acceptance
- fresh clone -> documented local boot;
- backend health endpoint;
- mobile shell boots;
- DB migration applies;
- tests run in CI;
- no hardcoded secrets.

---

## WP0.2 — Technical Decisions & Contracts

### Objective
Resolve pending foundational ADRs.

### Decide
- auth;
- worker;
- object storage;
- hosting;
- OpenAPI/client generation;
- AI gateway abstraction.

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

### Mobile
- onboarding;
- profile review/edit.

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

### Acceptance
Deterministic tests + AI evals.

---

## WP2.3 — Job Feed & Alerts

### Mobile
- feed;
- detail;
- filters;
- save/dismiss;
- watchlists.

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

### Mobile
Use Resumint-style flows as a reference, rebuilt behind backend contracts.

---

## WP3.2 — ATS & Tailoring

### Deliver
- ATS checks;
- keyword match;
- tailored version;
- fact grounding;
- diff;
- per-job resume.

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

### Eval
- tool selection;
- grounding;
- unsafe external action prevention.

---

## WP4.3 — WhatsApp Adapter

### Deliver
- inbound webhook;
- identity linking;
- natural commands;
- job alerts;
- application kit handoff;
- interactive confirmations;
- deep links.

### Constraint
Production adapter must align with official WhatsApp Business Platform requirements.

---

## WP5.1 — Apply Engine

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

### Reuse opportunity
Basafy is primary logic reference.

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
