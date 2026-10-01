# Open Questions & Research Backlog — TapJob

Open does not mean blocked. Questions are resolved by ADR/research PR.

## Product

### OQ-001 Brand
Is TapJob final public brand or working name?

### OQ-002 Pricing — PARTIALLY RESOLVED
Free/Pro/Automation entitlement direction is approved; exact prices/quotas wait for beta usage data.

### OQ-003 Auto-apply policy
What minimum safeguards are required before any OFFICIAL_API auto-submit without per-application review?

### OQ-004 Web
Ship web with beta or after mobile validation?

## Technical

### OQ-010 Auth — RESOLVED
Firebase Authentication: Google, Apple and e-mail initially.

### OQ-011 Worker — RESOLVED
Celery + Redis.

### OQ-012 Hosting — RESOLVED FOR INITIAL DEPLOYMENT
Existing Linux server + Docker Compose behind Cloudflare. Cloud migration only on evidence.

### OQ-013 Object storage — RESOLVED
Cloudflare R2.

### OQ-014 Search
When/if pgvector is necessary.

### OQ-015 Shared contracts — RESOLVED 2026-10-01
`@hey-api/openapi-ts` (pinned exact) generates a TypeScript client from the backend's deterministically-exported OpenAPI schema; `packages/contracts` publishes only a `createApiClient`/`isApiError` facade, never the generated internals. See `docs/adr/0003-api-contract-client-generation.md` (D-045).

## Integrations

### OQ-020 Gupy
Contact partnership/integration team and determine:
- candidate-side partnership model;
- access to public jobs;
- Quick Apply eligibility;
- commercial terms;
- test/sandbox.

### OQ-021 LinkedIn
Determine acceptable candidate-assist UX for browser extension under current LinkedIn terms.

### OQ-022 Indeed
Partner eligibility for candidate-side product.

### OQ-023 Brazil sources
Research:
- Sólides;
- Vagas.com.br;
- Catho;
- InfoJobs;
- Trabalha Brasil;
- Empregos.com.br;
- Google Jobs sourcing constraints.

### OQ-024 Workday
Assess variability and acceptable assisted-apply approach.

### OQ-025 SmartRecruiters
Official discovery/apply capabilities.

## Privacy

### OQ-030 Gmail
Determine exact Google verification/security assessment path and cost before public launch.

### OQ-031 Data retention
Define retention schedule by data class.

### OQ-032 DPO/legal
Determine when formal DPO/legal review is required before beta.

## AI

### OQ-040 Providers
Select first production provider(s) and fallback.

### OQ-041 Embeddings
Need and provider.

### OQ-042 Eval thresholds
Define minimum pass rates before prompt/model changes.

### OQ-043 Interview voice
Privacy and recording model.

## Mobile/UX

### OQ-050 Design system — RESOLVED FOR INITIAL BASELINE
Centralized semantic tokens; blue primary/orange accent; green success/red error; Inter; Lucide. Values remain rebrandable through tokens.

### OQ-051 Onboarding — RESOLVED
Sign in -> import/manual profile -> parse/confirm truth -> minimal career preferences -> first matches -> progressive enrichment.

### OQ-052 Notification strategy
Defaults/cadence still need UX testing, but delivery architecture is FCM + channel preferences + throttling.

## Commercial/Operations

### OQ-060 Source costs
Budget connectors/LLM/notifications per active user.

### OQ-061 Partnerships
Universities, bootcamps, outplacement, career coaches?

### OQ-062 Support
Operational model for broken connectors and failed applications.

## Research rule

Every resolved question should link to:
- evidence;
- decision;
- implementation impact.