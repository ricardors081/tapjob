# Decision Register — Tap Vaga

This is the current high-level decision register. Structural decisions may later be expanded into dedicated ADRs.

| ID | Decision | Status | Date |
|---|---|---|---|
| D-001 | Build one Career Core exposed by mobile, WhatsApp and later web/extension | Accepted | 2026-09-29 |
| D-002 | Brazil is the initial market | Accepted | 2026-09-29 |
| D-003 | AIApply + Huntr + Simplify are product references, not dependencies | Accepted | 2026-09-29 |
| D-004 | Use public reference repos as accelerators with provenance | Accepted | 2026-09-29 |
| D-005 | React Native + Expo is mobile baseline | Accepted | 2026-09-29 |
| D-006 | Python/FastAPI + PostgreSQL is backend baseline | Accepted | 2026-09-29 |
| D-007 | Start as modular monolith + async workers | Accepted | 2026-09-29 |
| D-008 | Professional Truth Store is mandatory for generated career artifacts | Accepted | 2026-09-29 |
| D-009 | No universal auto-apply promise | Accepted | 2026-09-29 |
| D-010 | Apply Engine supports OFFICIAL_API / ASSISTED_EXTENSION / DEEP_LINK / MANUAL / DISABLED | Accepted | 2026-09-29 |
| D-011 | User retains control over sensitive/legal application answers | Accepted | 2026-09-29 |
| D-012 | LLM provider calls happen through backend AI Gateway | Accepted | 2026-09-29 |
| D-013 | Gmail Inbox Intelligence is optional/feature-gated until compliance ready | Accepted | 2026-09-29 |
| D-014 | Gupy official integration/partnership is a Brazil priority | Accepted | 2026-09-29 |
| D-015 | LinkedIn/Indeed headless auto-apply is not production foundation | Accepted | 2026-09-29 |
| D-016 | GitHub PR review is mandatory; latest-HEAD green CI becomes mandatory once WP0.1 installs CI, with only the documented pre-CI bootstrap exception before that | Accepted | 2026-09-29 |
| D-017 | GitHub documentation is canonical handoff for future agents | Accepted | 2026-09-29 |

## Pending decisions

### P-001 Auth provider
Options:
- own auth;
- Supabase Auth;
- managed provider.

Criteria:
cost, mobile flows, security, portability, social login, account deletion.

### P-002 Worker
Candidates:
- Celery;
- RQ;
- Arq;
- other.

### P-003 Hosting
Need staging/production target.

### P-004 Object storage
Managed S3-compatible provider.

### P-005 Web timing
Whether web ships with V1 or after mobile beta.

### P-006 Gmail launch timing
Depends on OAuth verification/security requirements.

### P-007 Brand/design tokens
Product name "Tap Vaga" is working/canonical repository name; visual brand not frozen.

### P-008 Monetization
Pricing/tiering not approved.

## Decision change process

Do not edit an Accepted decision to rewrite history.

Instead:
1. add a superseding decision;
2. mark previous as Superseded;
3. explain rationale in PR/ADR.
