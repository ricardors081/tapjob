# Open Questions & Research Backlog — Tap Vaga

Open does not mean blocked. Questions are resolved by ADR/research PR.

## Product

### OQ-001 Brand
Is Tap Vaga final public brand or working name?

### OQ-002 Pricing
Free/Pro/Automation structure?

### OQ-003 Auto-apply policy
What minimum safeguards are required before any OFFICIAL_API auto-submit without per-application review?

### OQ-004 Web
Ship web with beta or after mobile validation?

## Technical

### OQ-010 Auth
Select auth strategy.

### OQ-011 Worker
Select async queue/worker.

### OQ-012 Hosting
Provider and region.

### OQ-013 Object storage
Provider.

### OQ-014 Search
When/if pgvector is necessary.

### OQ-015 Shared contracts
OpenAPI-generated clients vs shared schemas/packages.

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

### OQ-050 Design system
Finalize brand tokens, typography and iconography.

### OQ-051 Onboarding
How much information is mandatory before first value?

Hypothesis:
import resume -> confirm basics -> immediately show matches -> enrich later.

### OQ-052 Notification strategy
Default cadence without causing alert fatigue.

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
