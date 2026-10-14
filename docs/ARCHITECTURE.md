# Architecture — TapJob

## 1. Architectural style

Baseline: **modular monolith + asynchronous workers**, designed for later extraction of services only when operationally justified.

Reasons:
- MVP speed;
- lower operational cost;
- single transactional domain;
- easier governance;
- avoids premature microservices;
- still allows connector/worker isolation.

## 2. Logical architecture

```text
Mobile (Expo/React Native) ─┐
WhatsApp Adapter ──────────┤
Web / Browser Extension ───┼──> API Gateway / FastAPI
                            │        │
                            │        ├── Identity/Profile
                            │        ├── Jobs
                            │        ├── Matching
                            │        ├── Resume
                            │        ├── Applications
                            │        ├── Interviews
                            │        ├── Contacts
                            │        ├── Notifications
                            │        ├── AI Orchestrator
                            │        └── Integrations
                            │
                            └────────> Async Queue / Workers
                                      ├── job ingestion
                                      ├── matching batches
                                      ├── document generation
                                      ├── inbox sync
                                      ├── notifications
                                      └── connector jobs

PostgreSQL = canonical transactional store
Redis = queue/cache/idempotency/rate limits
Object Storage = resumes, PDFs, documents
LLM Gateway = provider abstraction
Observability = logs + metrics + traces
```

## 3. Baseline stack

### Mobile
- React Native
- Expo
- TypeScript
- Expo Router
- TanStack Query or equivalent for server state
- Zustand only for local/UI state when needed
- SecureStore for device secrets/tokens

Rationale: strongest reuse opportunity from Basafy, Resumint and CareerTrack, fast Android/iOS delivery and push/deep linking support.

### Backend
- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL
- Redis
- task worker: Celery + Redis

Rationale: ecosystem of JobBot, AutoApply, JobSpy, Resume Matcher and AI tooling.

### Web
- Next.js/React when web surface becomes necessary.
- Web is complementary; V1 remains mobile-first.

### Browser extension
- TypeScript
- Manifest V3
- React only where UI complexa for necessária
- comunicação assinada com backend
- nenhuma chave sensível embutida

### Shared configuration packages
Target monorepo direction:
- `packages/design-tokens`: DTCG-compatible token source + generated native/web outputs;
- `packages/product-config`: product/brand identity and public stable defaults;
- `packages/contracts`: generated/shared API contracts when selected.

Feature code must not become the owner of cross-cutting configuration.

### Identity
Firebase Authentication is the initial identity provider for Google, Apple and e-mail login. TapJob keeps an internal User UUID and maps external Firebase UID separately.

### Push / analytics / observability
- Firebase Cloud Messaging: mobile push;
- Firebase Analytics: product/mobile analytics;
- Sentry: technical errors, tracing, performance and AI/agent observability.

### Storage
Cloudflare R2 is the initial S3-compatible private object storage with signed URLs.

## 4. Modules

### Identity
Users, sessions, consent, devices, linked accounts.

### Career Profile
Professional Truth Store, preferences, facts, source/evidence.

### Jobs
Normalized job, source, employer, location, salary, workplace, freshness.

### Connectors
Each connector implements:
- discover;
- fetch details;
- capabilities;
- health;
- rate limits;
- apply mode.

### Matching
Hard filters -> deterministic scoring -> semantic relevance -> optional LLM re-score.

### Resume
Master resume, versions, sections, rendered files, ATS analyses.

### Applications
Application state machine, timeline, kits, answers, documents, idempotency.

### Inbox
OAuth connection, sync cursor/history, events, classification, dedupe.

### Interview
Interview events, preparation, story bank, debrief, tasks.

### Agent
Tool registry, policy engine, memory, confirmation gates.

### Notifications
Channel-independent message composition + channel transports.

## 5. Connector contract

Each source must expose a capability manifest:

```json
{
  "source": "example",
  "discovery": true,
  "job_details": true,
  "official_apply": false,
  "assisted_apply": true,
  "application_status": false,
  "auth_type": "none|oauth|api_key|partner",
  "risk_level": "low|medium|high"
}
```

Application behavior derives from capabilities, not from hardcoded UI assumptions.

## 6. Canonical data principles

- External IDs never substitute internal UUIDs.
- A JobSource record preserves source-specific IDs and raw URL.
- Normalized Job is deduplicated across sources.
- Application is user-specific.
- ResumeVersion is immutable after submission; new changes create a new version.
- External events are appended to timeline before materialized status is updated.

## 7. Event model

Important domain events:
- JobDiscovered
- JobUpdated
- MatchCalculated
- ResumeTailored
- ApplicationKitReady
- ApplicationPrepared
- ApplicationSubmitted
- ApplicationStatusChanged
- InboxEventDetected
- InterviewScheduled
- FollowUpDue
- OfferDetected

Events support observability and future event-driven extraction without forcing event sourcing in V1.

## 8. Async jobs

Must be idempotent and retry-safe.

Examples:
- sync_source(source, cursor)
- match_job(user, job)
- render_resume(version)
- sync_gmail(user)
- classify_inbox_event(message)
- deliver_notification(notification)
- refresh_watch(user)
- generate_application_kit(application)

## 9. AI Gateway

Never call LLM providers directly from mobile or extension.

Gateway responsibilities:
- provider routing;
- structured output;
- timeout/retry;
- cost metadata;
- prompt/version tracking;
- redaction;
- policy;
- evaluation;
- fallback.

Initial providers may include OpenAI, Gemini, Anthropic/OpenRouter and local models where viable, but product logic must not depend on one vendor.

## 10. Search & ranking

V1 can use PostgreSQL:
- full-text;
- trigram;
- structured filters.

Semantic matching:
- embeddings stored with pgvector or external vector service only if justified.

Do not add a separate vector database without measured need.

## 11. Initial deployment topology

Initial production/staging compute runs on the owner's Linux server with Docker Compose and EasyPanel.

For the **WhatsApp Live Gate staging environment** (ADR-0016, D-058):
- **Runtime control plane:** EasyPanel (v2.36.0+) managing services within project `julia`;
- **Target workloads:**
  - `tapjob-api` (FastAPI backend, Traefik HTTPS domain `https://tapjob-api-julia.gdxvyl.easypanel.host`, `/ready` HTTP 200);
  - `tapjob-worker` (Celery background worker, concurrency=2, Redis connected);
  - `tapjob-beat` (Celery beat periodic scheduler, persistent volume mounted at `/data/celerybeat`, `--schedule=/data/celerybeat/celerybeat-schedule`);
  - `tapjob-postgres` (dedicated PostgreSQL 16, persistent volume, daily local backup `0 0 * * *`, non-public);
  - `tapjob-redis` (dedicated Redis 7, persistent volume, password protected, non-public);
  - `tapjob-migrate` (one-shot Alembic migration task, completed up to revision 0013, stopped with zero ongoing footprint).
- **Database driver contract:** synchronous SQLAlchemy backend requiring `postgresql+psycopg://` scheme (psycopg 3).
- **Object storage:** Cloudflare R2 bucket `tapjob-staging-resume-imports` (private, Standard storage, bucket-scoped Object Read & Write credential).
- **Public ingress:** EasyPanel Traefik HTTPS ingress (automated Let's Encrypt TLS) routing to `tapjob-api`. Cloudflare Tunnel is no longer mandatory for this environment, while remaining supported for custom DNS and production environments.
- **Messaging Integration:** Evolution API runs in project `koglim` (`https://evo.koglim.com.br`) with networks unbridged. Outbound calls use HTTPS; inbound webhooks route via Traefik HTTPS to TapJob API (Evolution webhook unconfigured/disabled, fail-closed HTTP 404 in TapJob).
- **n8n:** strictly outside the synchronous messaging path.

No Kubernetes in the initial architecture. AWS remains a migration/scale option rather than an initial dependency.

## 12. Configuration & environments

Configuration follows `docs/ENGINEERING_STANDARDS.md`:
- product config is versioned;
- environment values are typed/validated at startup;
- secrets are server-side secret configuration;
- feature flags handle staged/operational switching;
- mobile does not read secrets, and `EXPO_PUBLIC_*` is treated as public.

Environments:
- local
- development
- staging
- production

No production secret in repository.

## 13. Testing pyramid

### Unit
Scoring, normalization, status transitions, parsing, policies.

### Contract
Connectors and provider adapters.

### Integration
Postgres/Redis/object storage.

### E2E
Critical journeys:
- onboarding;
- discover -> tailor -> apply;
- inbox -> status;
- interview prep;
- WhatsApp action confirmation.

### AI evals
Golden datasets for:
- resume extraction;
- matching;
- job classification;
- email classification;
- application answer grounding.

## 14. Deployment baseline

Containerized backend/worker on the existing Linux server initially, PostgreSQL/Redis isolated from public ingress, Cloudflare at the edge, R2 for object storage, CI/CD through GitHub Actions, migrations before rollout with rollback plan. Database migration to a managed PostgreSQL service is triggered by reliability/scale evidence rather than assumed on day one.

## 15. Architecture constraints

- no hardcoded credentials;
- no long-running work in request thread;
- no LLM response directly mutating critical state without schema/policy validation;
- no connector bypassing audit/idempotency;
- no application submission without mode/capability check;
- no scraping logic embedded in mobile app.