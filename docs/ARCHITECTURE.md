# Architecture — Tap Vaga

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
- task worker: Celery/RQ/Arq to be selected by ADR before implementation

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

### Storage
S3-compatible object storage with signed URLs.

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

## 11. Environments

- local
- development
- staging
- production

No production secret in repository.

## 12. Testing pyramid

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

## 13. Deployment baseline

Containerized backend/worker, managed Postgres preferred initially, CI/CD through GitHub Actions, migrations before rollout with rollback plan.

## 14. Architecture constraints

- no hardcoded credentials;
- no long-running work in request thread;
- no LLM response directly mutating critical state without schema/policy validation;
- no connector bypassing audit/idempotency;
- no application submission without mode/capability check;
- no scraping logic embedded in mobile app.
