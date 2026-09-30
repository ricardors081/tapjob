# Project State — TapJob

**As of:** 2026-09-30  
**Repository:** `ricardoo081/tapjob`  
**Phase:** 0 — Foundation  
**Code status:** NOT STARTED

This file must reflect reality only.

## 1. Completed

### Product discovery
- Product thesis defined.
- Brazil-first strategy defined.
- Mobile + WhatsApp + future web/extension direction accepted.
- AIApply, Huntr and Simplify adopted as product references.
- Ten public repositories analyzed as technical references.
- Second-pass source audit completed against pinned upstream HEAD SHAs.
- Selective code-reuse blueprint completed: primary donor chosen per capability; IMPORT / ADAPT / PORT / REFERENCE rules established.
- Source reuse ledger and Claude Code workflow established.
- Repository-owner reuse authorizations reported as formalized by e-mail and retained privately by the project owner.
- Initial external-platform feasibility research completed for:
  - Gupy;
  - LinkedIn;
  - Indeed;
  - Greenhouse;
  - Lever;
  - Ashby;
  - Gmail.

### Canonical documentation
The canonical foundation includes:
- Project Charter;
- PRD;
- Feature Scope;
- Architecture;
- Data Model;
- Integrations;
- Apply Engine;
- Career Agent;
- Product Surfaces;
- Security/Privacy;
- UX direction;
- Roadmap;
- Foundation Decisions;
- Implementation Plan;
- Governance;
- Decisions;
- References;
- Open Questions;
- Agent operating contract;
- Source Reuse Blueprint;
- Source Reuse Ledger;
- Claude Code Workflow;
- Claude Code root entrypoint (`CLAUDE.md`);
- Design System & Tokens standard;
- Engineering Standards for typed configuration, i18n, domain constants, APIs, errors, observability and no-magic-value rules;
- `TapJob` adopted as the working product identity and the GitHub repository has been renamed to `ricardoo081/tapjob`;
- eight foundation topics resolved in `FOUNDATION_DECISIONS.md`: user journey, infrastructure/auth, design/performance, privacy/security, job-source ordering, monetization/entitlements, analytics/operations and engineering governance;
- product positioning locked: candidate-side job-search execution, not a LinkedIn/social-network clone; AIApply is primary reference, Huntr/Simplify are complementary references, and the 10 audited repos remain implementation accelerators.

## 2. Not implemented

No production functionality exists yet.

Specifically NOT implemented:
- backend;
- database;
- mobile app;
- web app;
- browser extension;
- WhatsApp integration;
- auth;
- profile;
- resume parser;
- job ingestion;
- matching;
- AI gateway;
- application tracking;
- apply engine;
- Gmail integration;
- notifications;
- CI/CD;
- deployment;
- monitoring.

## 3. Current risks

### R1 — External platform access
Official submission APIs often require employer/partner credentials.

Mitigation:
capability-based Apply Engine and graceful fallback.

### R2 — Gmail restricted scope
Inbox Intelligence may require Google verification/security assessment.

Mitigation:
feature flag and compliance work before public release.

### R3 — Code reuse provenance
Some reference repositories have MIT licenses; others do not expose a root LICENSE file. The project owner reports that authorization from all 10 owners is formalized by e-mail.

Mitigation:
keep private authorization evidence outside the repository; every code-bearing reuse PR must pin upstream repository/SHA/path and record its permission basis, adaptation and security review in `SOURCE_REUSE_LEDGER.md`.

### R4 — Scope size
Vision is broad.

Mitigation:
phase gates and MVP exit criteria.

### R5 — AI hallucination
Career documents can be harmful if facts are fabricated.

Mitigation:
Professional Truth Store + grounded generation + evals.

### R6 — GitHub enforcement capability
Current repository state reports `main` as unprotected, and GitHub ruleset/branch-protection APIs indicate that the current private-repository plan does not expose the intended protection capability.

Mitigation:
WP0.1 still installs CI and the team follows the documented PR/latest-HEAD review process. If the GitHub plan permits branch protection later, enforce required PR/check/conversation rules in GitHub itself. Until then, the governance requirement remains procedural and auditable rather than falsely documented as technically enforced.

## 4. Immediate next work package

### WP0.1 — Repository & Engineering Foundation

Deliverables:
- project monorepo/scaffold decision;
- backend skeleton;
- mobile skeleton;
- shared contracts strategy;
- Docker local stack;
- PostgreSQL;
- Redis;
- environment templates;
- secret handling;
- lint/typecheck/tests;
- GitHub Actions;
- branch/PR conventions;
- CODEOWNERS if team exists;
- dependency/security scanning;
- basic observability;
- contribution docs.

Acceptance:
- clean clone can run local skeleton;
- CI green;
- health endpoints;
- no secrets;
- database migration framework working;
- mobile shell boots;
- docs align with repository reality.

### WP0.2 — Technical Contracts & Remaining Architecture Decisions

Already resolved:
- auth: Firebase Authentication;
- worker: Celery + Redis;
- object storage: Cloudflare R2;
- initial hosting: Linux + Docker Compose behind Cloudflare.

Remaining:
- OpenAPI/client generation strategy;
- AI Gateway abstraction;
- canonical connector/interfaces;
- error/pagination/observability contracts;
- exact deployment automation within the approved topology.

Output:
ADRs/contracts, not production feature claims.

## 5. Phase gates

### Gate 0
Foundation and CI ready.

### Gate 1
Career Core accepted.

### Gate 2
Jobs & Matching accepted.

### Gate 3
Resume & Application Kit accepted.

### Gate 4
Tracking + Agent + WhatsApp accepted.

### Gate 5
Assisted Apply accepted.

### Gate 6
Inbox Intelligence compliant and accepted.

## 6. Documentation maintenance rule

On every significant PR:
- update this file when phase/status changes;
- update DECISIONS when a decision changes;
- update INTEGRATIONS when platform capability changes;
- update ROADMAP only when scope ordering changes.

## 7. Current authoritative conclusion

TapJob has a defined product and architecture baseline but **no application code yet**.

The correct next action is engineering foundation, not feature implementation.