# Decision Register — TapJob

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
| D-018 | TapJob remains the sovereign architecture; no upstream repository becomes the whole-product base | Accepted | 2026-09-30 |
| D-019 | Reuse is selective and classified as IMPORT / ADAPT / PORT / REFERENCE, with one primary runtime donor per capability | Accepted | 2026-09-30 |
| D-020 | Every code-bearing upstream reuse must pin repo + SHA + source path and be recorded in SOURCE_REUSE_LEDGER before merge | Accepted | 2026-09-30 |
| D-021 | Python donors (JobBot/AutoApply) are preferred for backend runtime; Expo donors (Basafy/Resumint/CareerTrack) are preferred for mobile runtime; Flutter donors are reference/port only | Accepted | 2026-09-30 |
| D-022 | `TapJob` is the working product/repository identity; runtime product identity must be centrally configured rather than hardcoded | Accepted | 2026-09-30 |
| D-023 | Visual decisions use centralized semantic design tokens with a DTCG-compatible canonical source where practical; feature screens do not own raw colors/typography/spacing | Accepted | 2026-09-30 |
| D-024 | Configuration is separated into product config, typed environment config, secrets and feature flags; no direct scattered environment/magic-value access | Accepted | 2026-09-30 |
| D-025 | Initial compute runs on the owner's Linux server with Docker Compose; Cloudflare Tunnel/edge protects public ingress; AWS is a scale/migration option, not an initial dependency | Accepted | 2026-09-30 |
| D-026 | Cloudflare R2 is initial private object storage for resumes/documents; PostgreSQL remains canonical business storage and Redis is non-durable infrastructure | Accepted | 2026-09-30 |
| D-027 | Firebase Authentication is initial identity provider with Google, Apple and e-mail; phone/SMS auth is deferred | Accepted | 2026-09-30 |
| D-028 | FCM is mobile push transport; Firebase Analytics measures product behavior; Sentry provides technical/AI observability | Accepted | 2026-09-30 |
| D-029 | Initial UI baseline uses Inter, semantic design tokens, blue primary/orange accent, green success and red error; actual screens never hardcode those values | Accepted | 2026-09-30 |
| D-030 | P0 job sources are manual URL/paste + Greenhouse + Lever + Ashby; Gupy is first Brazil strategic integration; LinkedIn/Indeed are initially assisted/external | Accepted | 2026-09-30 |
| D-031 | Controlled beta can expose implemented capabilities without payment; centralized entitlements/usage limits are designed before billing, while exact pricing remains data-driven | Accepted | 2026-09-30 |
| D-032 | Product analytics uses a canonical event taxonomy and server-confirmed domain events; analytics payloads never contain resume/e-mail bodies or sensitive answers | Accepted | 2026-09-30 |
| D-033 | After WP0.1 installs CI, PR + latest-HEAD green checks and resolved review threads are mandatory by governance. Enforce them with GitHub branch protection/rulesets when the repository plan supports it; until then, use the same gate procedurally. Squash merge is preferred | Accepted | 2026-09-30 |
| D-034 | Celery + Redis is the initial async worker/queue baseline unless an implementation spike proves a material reason to supersede it | Accepted | 2026-09-30 |
| D-035 | TapJob is a candidate-side job-search execution platform, not a LinkedIn-style social network; job feed means opportunity feed and networking means private contacts/outreach/follow-up | Accepted | 2026-09-30 |
| D-036 | AIApply is the primary product archetype; Huntr and Simplify are complementary product references; the 10 audited repositories are implementation accelerators/code donors rather than product-direction authorities | Accepted | 2026-09-30 |
| D-037 | Initial geographic/product focus is Brazil; architecture/i18n/integration boundaries must support later Latin America and broader international expansion without changing the core product thesis | Accepted | 2026-09-30 |
| D-038 | Customer-surface delivery order is Foundation -> Minimum Career Core -> WhatsApp -> Mobile App -> Browser Extension; this changes sequencing only, not approved product scope | Accepted | 2026-09-30 |
| D-039 | WhatsApp is an independent first-class channel over the same Career Core and does not depend on the mobile app; channel identities link to one internal User | Accepted | 2026-09-30 |
| D-040 | Existing Evolution API is the initial WhatsApp transport for development/controlled early validation behind the provider-neutral MessagingChannel adapter; official WhatsApp Cloud API remains the migration target for broad commercial scale | Accepted | 2026-09-30 |
| D-041 | n8n is allowed only as auxiliary bridge/orchestration/ops tooling; it cannot own canonical user state, domain rules, authorization, idempotency, agent policy or application state | Accepted | 2026-09-30 |
| D-042 | EasyPanel may be used as a deployment/operations UI on the existing Linux server but is not an architectural dependency or hidden source of runtime configuration | Accepted | 2026-09-30 |
| D-043 | WP0.1 foundation tooling: uv workspace (single lockfile) for Python, npm workspaces for JS, one backend image for api/worker/migrate, dependency-free DTCG token generator, WP0.1 split into checkpoints A/B — see `docs/adr/0001-monorepo-foundation-tooling.md` | Accepted | 2026-09-30 |

## Pending decisions

### P-001 Auth provider — RESOLVED 2026-09-30
Firebase Authentication by D-027.

### P-002 Worker — RESOLVED 2026-09-30
Celery + Redis baseline by D-034.

### P-003 Hosting — RESOLVED 2026-09-30
Initial staging/production compute uses the owner's Linux server + Docker Compose behind Cloudflare; migration to managed cloud remains an operational trigger.

### P-004 Object storage — RESOLVED 2026-09-30
Cloudflare R2 by D-026.

### P-005 Web timing — RESOLVED 2026-09-30
Mobile + WhatsApp are first-class V1 surfaces. End-user web is complementary and can follow mobile beta; browser extension enters assisted-apply phase.

### P-006 Gmail launch timing — RESOLVED 2026-09-30
Inbox Intelligence is V1.x/feature-gated and only launches publicly after OAuth verification/security obligations are satisfied.

### P-007 Brand/design tokens — RESOLVED 2026-09-30
Resolved by D-022/D-023. Product working name is `TapJob`; exact visual token values/logo are still free to evolve inside the centralized design-system contract.

### P-008 Monetization — PARTIALLY RESOLVED 2026-09-30
Entitlement architecture and Free/Pro/Automation direction are approved by D-031. Exact plan names, prices and quotas remain data-driven commercial decisions.

## Decision change process

Do not edit an Accepted decision to rewrite history.

Instead:
1. add a superseding decision;
2. mark previous as Superseded;
3. explain rationale in PR/ADR.