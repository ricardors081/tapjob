# ADR-0016 — EasyPanel Runtime Control Plane & Traefik Ingress for WhatsApp Live Gate Staging

**Status:** Accepted  
**Date:** 2026-10-13  
**Decision:** D-058 (`docs/DECISIONS.md`)  

---

## 1. Context & Operational Background

Following the verification and merge of PR #42 (Minimum WhatsApp Alpha Vertical Slice) and PR #43 (WP2.3 Job Feed, Alerts & Mobile Jobs Surface), TapJob has entered the **WhatsApp Live Gate** execution stage (`CURRENT_PRODUCT_PRIORITY = WHATSAPP_LIVE_AND_COMMERCIAL_READINESS`).

Previous foundational decisions (`docs/adr/0006-worker-correlation-deployment-cd-contract.md`, D-025, D-042, D-050) established:
1. Compute hosted on the owner's Linux server using Docker Compose;
2. Cloudflare Tunnel as the public ingress;
3. EasyPanel strictly as an operations/visualization UI;
4. Build-once, promote-the-same-immutable-digest (`registry/repo@sha256:...`) deployment discipline.

To achieve live staging validation for the WhatsApp Live Gate without operational impedance or unnecessary networking complexity, this ADR formalizes the operational decision to authorize **EasyPanel** as the runtime/deployment control plane and **EasyPanel Traefik** as the HTTPS public ingress for TapJob staging on the owner's Linux server.

---

## 2. Architectural & Operational Decisions

### Decision A: Runtime Control Plane
- **EasyPanel (v2.36.0+)** is formally authorized as the runtime/deployment control plane on the owner's Linux server.
- **GitHub remains the canonical source of truth**: all service configurations, environment variable definitions, image references, and documentation are version-controlled in the repository.
- EasyPanel is **NOT** the source of application code or ad-hoc runtime mutations. EasyPanel manages container lifecycle, Traefik ingress routing, TLS certificates, and isolated service networks.
- All TapJob staging services are isolated within EasyPanel project:
  ```text
  PROJECT = julia
  ```
- No existing services inside project `julia` belong to TapJob. Existing services (e.g. `julia-postgres`, `julia-redis`, `wp6stg-evolution-db`, `wp6stg-evolution-redis`, `teste-ad`) must never be touched, modified, or reused.

### Decision B: Immutable Artifact & GHCR Pipeline
- Application runtime services run **ONLY** pre-built, immutable image digests from GitHub Container Registry:
  ```text
  ghcr.io/ricardos081/tapjob-backend@sha256:<digest>
  ```
- **Strict Anti-Patterns Prohibited:**
  - ZERO mutable `:latest` or branch tags in runtime deployments.
  - ZERO source-code compilation or `docker build` on the server host.
  - ZERO falling back to building on server if registry pull fails.
- Artifacts are built exclusively via the existing `.github/workflows/release-image.yml` GitHub Actions workflow triggered from canonical `main`.
- All backend workloads (`tapjob-api`, `tapjob-worker`, `tapjob-beat`, `tapjob-migrate`) use the **EXACT SAME** immutable backend image digest.

### Decision C: Public Ingress via Traefik
- **EasyPanel Traefik HTTPS ingress** is authorized as the public ingress path for this server.
- Cloudflare Tunnel is no longer mandatory for this staging environment (it remains a viable future option or for custom production routing).
- Initial ingress uses an EasyPanel generated HTTPS domain (e.g., Let's Encrypt managed by Traefik) routing to `tapjob-api` on internal container port `8000`.
- No raw host TCP ports are published for application containers.
- Custom DNS may be configured in a subsequent commercial milestone.

### Decision D: Dedicated PostgreSQL Service
- TapJob staging operates on a dedicated PostgreSQL service:
  ```text
  SERVICE = tapjob-postgres
  PROJECT = julia
  ```
- Compatibility: PostgreSQL 16 (matching repository specification).
- Storage: Dedicated persistent Docker volume.
- Network Isolation: Accessible only within the `julia` project network; **NO** public host port exposure (`POSTGRES_PUBLICLY_EXPOSED = NO`).
- Never reuse or share databases with any other project or service on the host.

### Decision E: Dedicated Redis Service
- TapJob staging operates on a dedicated Redis service:
  ```text
  SERVICE = tapjob-redis
  PROJECT = julia
  ```
- Compatibility: Redis 7 (matching repository specification).
- Storage: Persistent Docker volume.
- Authentication: Dedicated password configured.
- Network Isolation: Internal project network only; **NO** public host port exposure (`REDIS_PUBLICLY_EXPOSED = NO`).
- Never reuse or share Redis instances with any other application.

### Decision F: Evolution API Ingress & Outbound Integration Boundary
- Evolution API remains in its dedicated project:
  ```text
  PROJECT = koglim
  SERVICE = evolution-api
  VERSION = 2.3.7
  DOMAIN  = https://evo.koglim.com.br
  INSTANCE = TapJob
  ```
- **Network Boundary:** Docker networks of project `julia` and project `koglim` are **NOT** joined together.
- **Outbound Path:** TapJob worker/api dispatches requests to Evolution via the public HTTPS domain `https://evo.koglim.com.br`.
- **Inbound Path (Future):** Evolution dispatches webhooks to TapJob via the public HTTPS Traefik domain (`https://<tapjob-staging-domain>/webhooks/whatsapp`).
- **Webhook State:** For this deployment gate, Evolution webhook remains **STRICTLY DISABLED** (`EVOLUTION_WEBHOOK_ENABLED = NO`). The TapJob receiver endpoint exists with fail-closed security (`WHATSAPP_WEBHOOK_ENABLED = false`), and no live messages will be sent or received (`WHATSAPP_REAL_SENDS = 0`).
- **Authentication Scope:** The instance-scoped credential is configured for the Evolution integration and has been accepted by the read-only instance endpoints used in this checkpoint (`/instance/connectionState/TapJob`, `/webhook/find/TapJob`). Evolution documentation/API contract indicates the same instance credential model for instance-scoped message operations; actual outbound sendText (`/message/sendText/TapJob`) validation remains explicitly deferred to the WhatsApp Live Activation Gate (`EVOLUTION_LIVE_VALIDATION = NO`, `WHATSAPP_REAL_SENDS = 0`). Preferred over global API keys.

### Decision G: n8n Demarcation
- `n8n` is explicitly outside the synchronous messaging path.
- n8n is NOT connected between Evolution and TapJob.
- Critical path:
  ```text
  WhatsApp ──> Evolution (koglim) ──> TapJob API (julia, Traefik HTTPS)
           ──> PostgreSQL + Redis ──> Celery Worker / Beat
           ──> Evolution (koglim) ──> WhatsApp
  ```

### Decision H: Environments
- Runtime environment is:
  ```text
  APP_ENV = staging
  ```
- This environment serves as the WhatsApp Live Gate staging environment.
- It is NOT production. Production requires separate promotion, approval, and commercial setup.

### Decision I: Services Topology & Migration
Initial EasyPanel services inside project `julia`:
1. `tapjob-postgres` (PostgreSQL 16, private network only, persistent volume, daily local backup)
2. `tapjob-redis` (Redis 7, private network only, password protected)
3. `tapjob-api` (FastAPI app on port 8000, Traefik HTTPS ingress)
4. `tapjob-worker` (Celery worker, concurrency=2)
5. `tapjob-beat` (Celery beat scheduler, persistent volume mounted at `/data/celerybeat`, schedule `--schedule=/data/celerybeat/celerybeat-schedule`)
6. `tapjob-migrate` (One-shot migration runner, executing `alembic upgrade head` to revision 0013, stopped after completion with zero ongoing resource footprint)

### Decision J: Database Driver Contract
- Synchronous SQLAlchemy backend runtime requires the `postgresql+psycopg://` scheme (psycopg 3).
- `postgresql+asyncpg://` must never be used in database connection strings.
- All deployed backend services touching the database (`tapjob-api`, `tapjob-worker`, `tapjob-migrate`) strictly use `postgresql+psycopg://`.

### Decision K: Capacity & Resource Model
Initial staging resource reservations and limits:
- `tapjob-api`: CPU <= 1 core, RAM = 512 MB
- `tapjob-worker`: CPU <= 1 core, RAM = 768 MB, Celery concurrency = 2
- `tapjob-beat`: CPU <= 0.25 core, RAM = 256 MB
- `tapjob-postgres`: RAM target = 1 GB
- `tapjob-redis`: RAM target = 256 MB

---

## 3. Operational Risk: Upstream Evolution API Webhook Persistence Across Restarts
Upstream Evolution API has a reported webhook-configuration persistence issue across container restarts in a newer 2.4.0-rc2 environment. TapJob currently runs 2.3.7 and has not reproduced that defect locally. As a precautionary operational invariant, after any Evolution restart/redeploy the operator must re-read `/webhook/find/TapJob` and verify the expected configuration before declaring inbound messaging healthy.
- **Runbook Requirement:** Whenever Evolution API is restarted or redeployed, the operator must verify webhook configuration (`GET /webhook/find/{instance}`) before declaring the channel healthy.
- For this staging checkpoint, Evolution is **NOT** restarted.

---

## 4. Absolute Stop Conditions & Prerequisites
1. **GHCR Pull Gate:** An authorized credential with package-read scope must exist in EasyPanel to pull private images from `ghcr.io/ricardos081/tapjob-backend`. Visibility of GHCR packages must NEVER be made public as a shortcut.
2. **R2 Storage Gate:** Private Cloudflare R2 object storage credentials (`R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`) must be configured for TapJob staging. If unavailable, deployment must stop at PostgreSQL/Redis/definitions creation without starting application containers.
3. **Migration Gate:** Database migrations must complete with exit code 0 before starting API or worker workloads.
4. **Live Traffic Freeze:** Webhook is NOT activated, and zero real WhatsApp messages are dispatched (`WHATSAPP_REAL_SENDS = 0`).

---

## 5. Factual Staging Topology & Execution Evidence

As verified in this checkpoint:
- **EasyPanel Project:** `julia`
- **Isolated Dedicated Services Provisioned & Running:**
  - `tapjob-postgres`: PostgreSQL 16, persistent volume, non-public (`POSTGRES_PUBLICLY_EXPOSED = NO`), internal host `julia_tapjob-postgres:5432`, database `tapjob`, daily local backup scheduled at `0 0 * * *` on Local Disk destination (`cmplbmjgm000001rw79m98ejz`, retention 7).
  - `tapjob-redis`: Redis 7, persistent volume, non-public (`REDIS_PUBLICLY_EXPOSED = NO`), internal host `julia_tapjob-redis:6379`, password protected.
  - `tapjob-api`: Running on immutable image digest, resources 1 CPU / 512 MB, Traefik HTTPS domain `https://tapjob-api-julia.gdxvyl.easypanel.host`, internal `/ready` probe HTTP 200, public `/ready` probe HTTP 200 (`status=ok`, `postgresql=ok`, `redis=ok`), `WHATSAPP_WEBHOOK_ENABLED = false`.
  - `tapjob-worker`: Running on immutable image digest, resources 1 CPU / 768 MB, Celery worker concurrency = 2 (`--concurrency=2`), connected to Redis broker, all background tasks registered, zero restart loops.
  - `tapjob-beat`: Running on immutable image digest, resources 0.25 CPU / 256 MB, command `celery -A tapjob_worker.beat:app beat --loglevel=INFO --schedule=/data/celerybeat/celerybeat-schedule`, persistent volume `tapjob-beat-schedule` mounted at `/data/celerybeat`, scheduler `celery.beat.PersistentScheduler`, zero permission errors, adhering to strict least-privilege runtime configuration (broker and runtime settings only; no database, R2, or messaging secrets injected).
  - `tapjob-migrate`: One-shot migration completed successfully with exit code 0 up to Alembic head revision `0013` (`20261011_0013_job_feed_alerts_saved_watchlist.py`). Workload stopped (`enabled: false`, zero ongoing footprint).
- **Immutable Release Image:**
  - Source commit SHA: canonical `main` commit `812b6200e125b14acabbd992b5e759f1ff0d879a`.
  - Release workflow run ID: `38439535306`.
  - Immutable image digest: `sha256:73bca62692826567514f7fe689804534e4ef4eff0790a45f1b87c49fd3887eb2`.
  - Full promotion ref: `ghcr.io/ricardo081683/tapjob-backend@sha256:73bca62692826567514f7fe689804534e4ef4eff0790a45f1b87c49fd3887eb2`.
  - Digest validated via `tools/deploy/validate_image_ref.mjs` (PASS).
- **Prerequisite Gates Verified:**
  - `GHCR_PULL_AUTH_AVAILABLE = YES`: credential `ricardoo081` pulls private image from GHCR without public visibility exposure.
  - `R2_CONFIG_AVAILABLE = YES`: Cloudflare R2 bucket `tapjob-staging-resume-imports` configured with bucket-scoped Object Read & Write credential, private, Standard storage, zero public r2.dev access. Smoke test verified: `R2_PUT=PASS`, `R2_GET=PASS`, `R2_CONTENT_MATCH=PASS`, `R2_DELETE=PASS`, `R2_SMOKE_OBJECTS_REMAINING=0`.
  - `DATABASE_URL_DRIVER = postgresql+psycopg`: synchronous psycopg 3 driver in use across API, worker, and migrate.
  - `DATABASE_BACKUP_OFFSITE = NOT_CONFIGURED`: daily local backups scheduled on local disk destination (`cmplbmjgm000001rw79m98ejz`, retention 7); offsite backup replication is not configured for this staging environment. This is not a blocker for the staging live canary, but is an explicit commercial-production readiness requirement before handling paid/customer production traffic.
  - `MIGRATION_CURRENT_REVISION = 0013`, `MIGRATION_AT_HEAD = YES`.
- **Fail-Closed Webhook Verification:**
  - Public endpoint: `POST https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp`.
  - Result: HTTP 404 `RESOURCE_NOT_FOUND` from Uvicorn, fail-closed while `WHATSAPP_WEBHOOK_ENABLED=false`, zero inbound envelope processing.
- **Evolution API Verification (Read-Only):**
  - Host: `https://evo.koglim.com.br` (project `koglim`).
  - Instance: `TapJob` connected (`state: open`).
  - Webhook: unconfigured / disabled (`null`).
  - Outbound sends: zero live messages sent (`WHATSAPP_REAL_SENDS = 0`).
- **Resource Metrics Observed:**
  - `API_CPU` ~0.34%, `API_RAM` ~136.3 MB (26.6% of 512 MB limit)
  - `WORKER_CPU` ~4.23%, `WORKER_RAM` ~292.8 MB (38.1% of 768 MB limit)
  - `BEAT_CPU` ~0.0%, `BEAT_RAM` ~109.0 MB (42.6% of 256 MB limit)
  - `POSTGRES_RAM` ~41.5 MB
  - `REDIS_RAM` ~4.3 MB
  - Host: RAM free ~6,970 MB (58.3% of 11.96 GB), CPU usage ~12.7%, Disk free ~164.5 GB (85.4% of 192.7 GB).
- **Operational Boundaries Preserved:**
  - `EVOLUTION_LIVE_VALIDATION = NO`
  - `WHATSAPP_REAL_SENDS = 0`
  - `PRODUCTION_MUTATED = NO`
  - `OTHER_EASYPANEL_SERVICES_MUTATED = NO`
  - `N8N_MUTATED = NO`
  - `CUSTOM_DNS_MUTATED = NO`

---

## 6. Phase A: Live Webhook Activation Gate Evidence

On branch `ops/whatsapp-live-activation-gate`, Phase A of the WhatsApp Live Activation Gate was executed in staging:

### 1. Ingress Receiver Enablement
- **Configuration Change:** `WHATSAPP_WEBHOOK_ENABLED = true` on `tapjob-api` service in project `julia`. All other environment variables (including existing cryptographic `WHATSAPP_WEBHOOK_SECRET`) preserved untouched.
- **Redeployment:** Only `tapjob-api` redeployed. `tapjob-postgres`, `tapjob-redis`, `tapjob-worker`, `tapjob-beat` were not restarted.
- **Health Verification:** Public probe `GET https://tapjob-api-julia.gdxvyl.easypanel.host/ready` returned HTTP 200 (`status=ok`, `postgresql=ok`, `redis=ok`). Zero restart loops or configuration failures.

### 2. Fail-Closed Authentication Negative Tests
- **Test A (No Secret Header):**
  - Request: `POST https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp` with `Content-Type: application/json`, payload `{}`, no secret header.
  - Result: HTTP 401 `AUTHENTICATION_REQUIRED`.
- **Test B (Synthetic Wrong Secret Header):**
  - Request: `POST https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp` with `Content-Type: application/json`, payload `{}`, header `X-Webhook-Secret: wrong-value`.
  - Result: HTTP 401 `AUTHENTICATION_REQUIRED`.
- **Side Effect Audit:**
  - `UNAUTHENTICATED_ENVELOPE_CREATED = NO`
  - Zero `ProviderEventEnvelope`, `ChannelIdentity`, `User`, or `OutboundMessageQueue` records created.

### 3. Valid-Secret Harmless Synthetic Payload Test
- **Test C (Configured Secret with Empty Body):**
  - Request: `POST https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp` with `Content-Type: application/json`, payload `{}`, valid header `X-Webhook-Secret: <configured secret>`.
  - Result: HTTP 202 `{"status":"accepted"}`.
  - Parsing Behavior: Because the payload contains no message envelopes or recognized event types, `EvolutionAdapter.parse_inbound` produced an empty message list, resulting in 0 database writes and 0 Celery task dispatches.
  - `SYNTHETIC_VALID_AUTH_ENVELOPE_CREATED = NO`
  - `WHATSAPP_REAL_SENDS = 0`

### 4. Evolution API v2.3.7 Instance Webhook Configuration
- **Contract Verification:** Verified against Evolution API v2.3.7 schema (`webhook.enabled`, `webhook.url`, `webhook.headers`, `webhook.byEvents`, `webhook.base64`, `webhook.events`).
- **Endpoint:** `POST https://evo.koglim.com.br/webhook/set/TapJob` with instance-scoped API token. Global API key was neither used nor exposed.
- **Configuration Applied:**
  - `url`: `https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp`
  - `enabled`: `true`
  - `byEvents`: `false`
  - `base64`: `false`
  - `events`: `["MESSAGES_UPSERT"]` (minimal event subscription, zero broad/unnecessary events)
  - `headers`: `{"X-Webhook-Secret": "<secret>"}` (header value redacted)
- **Response:** HTTP 201 Created.

### 5. Evolution Webhook Persistence Verification
- **Verification Call:** `GET https://evo.koglim.com.br/webhook/find/TapJob` with instance-scoped API token.
- **Factual Persisted Configuration:**
  - `enabled`: `true`
  - `url`: `https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp`
  - `webhookByEvents`: `false`
  - `webhookBase64`: `false`
  - `events`: `["MESSAGES_UPSERT"]`
  - `WEBHOOK_HEADER_PRESENT = YES` (secret value redacted)
- **Precaution Maintained:** Evolution was NOT restarted or redeployed during this gate (`EVOLUTION_RESTARTED = NO`). Upstream persistence revalidation precaution remains required after any future Evolution restarts.

### 6. Background Worker & Beat Health Verification
- **Worker (`tapjob-worker`):** Running healthy (Up 19 hours), Celery concurrency = 2, connected to Redis broker, zero restart loops.
- **Beat (`tapjob-beat`):** Running healthy (Up 2 hours), persistent scheduler `/data/celerybeat/celerybeat-schedule`, least-privilege configuration verified.

### 7. Phase A Boundaries Preserved
- `WHATSAPP_WEBHOOK_ENABLED = true`
- `EVOLUTION_WEBHOOK_ENABLED = YES`
- `EVOLUTION_WEBHOOK_EVENTS = MESSAGES_UPSERT`
- `WEBHOOK_AUTH_HEADER = X-Webhook-Secret`
- `WEBHOOK_HEADER_VALUE_REDACTED = YES`
- `EVOLUTION_LIVE_INBOUND_VALIDATION = NO`
- `EVOLUTION_SENDTEXT_RUNTIME_VERIFIED = NO`
- `WHATSAPP_REAL_SENDS = 0`
- `PRODUCTION_MUTATED = NO`
- `N8N_MUTATED = NO`
- `OTHER_EASYPANEL_SERVICES_MUTATED = NO`

---

## 7. Phase B: First Real End-to-End Canary Evidence

On branch `ops/whatsapp-live-canary-phase-b`, Phase B of the WhatsApp Live Activation Gate was executed in staging between dedicated test identities.

### 1. Test Isolation & Strict Allowlist
- **Instance Pair:**
  - Evolution instance: `TapJob` (`ownerJid: ********8445@s.whatsapp.net`, state `open`)
  - Test client instance: `TapJob-Canary-Client` (`ownerJid: ********8733@s.whatsapp.net`, state `open`)
- **Execution-Time Allowlist:** Strictly locked to the pair above (`LIVE_ALLOWLIST_PAIR_VERIFIED = YES`). Zero customer or third-party phone numbers were involved.
- **Baseline Chat Check:** Exactly 0 messages in chat between Canary and TapJob prior to test start (`BASELINE_CANARY_CHAT_MESSAGES = 0`, `BASELINE_TAPJOB_CHAT_MESSAGES = 0`).

### 2. First Real Send — Canary Client to TapJob
- **Action:** Canary client dispatched real WhatsApp message:
  - Endpoint: `POST https://evo.koglim.com.br/message/sendText/TapJob-Canary-Client`
  - Body: `{"number": "********8445", "text": "oi"}`
  - Timestamp (T0): 1792004467.4 s
- **Response:** HTTP 201 Created, provider message ID: `3EB0F08E1C1E850D2D8D21`.
- `CANARY_CLIENT_SENDTEXT_RUNTIME_VERIFIED = YES`
- `CANARY_CLIENT_TO_TAPJOB_SENDS = 1`
- `CANARY_PROVIDER_MESSAGE_ID_PRESENT = YES`

### 3. Real Webhook Ingress & Verification
- **Delivery:** Evolution API delivered real `messages.upsert` event to TapJob public Traefik endpoint:
  - Endpoint: `POST https://tapjob-api-julia.gdxvyl.easypanel.host/webhooks/whatsapp`
  - Authentication: Header `X-Webhook-Secret` validated successfully.
- **Result:** HTTP 202 `{"status":"accepted"}`.
- `REAL_WEBHOOK_RECEIVED = YES`
- `REAL_WEBHOOK_HTTP_STATUS = 202`
- `REAL_WEBHOOK_AUTHENTICATED = YES`

### 4. Inbound Envelope & Celery Worker Processing
- **Persistence:** Exactly one `ProviderEventEnvelope` created for the Canary message:
  - `provider = "evolution"`
  - `provider_message_id = "3EB0F08E1C1E850D2D8D21"`
  - `status = "PROCESSED"`
  - `processing_attempts = 1`
  - `duplicate_envelopes = 0`
- `REAL_INBOUND_ENVELOPE_CREATED = YES`
- `REAL_INBOUND_ENVELOPE_STATUS = PROCESSED`
- `REAL_INBOUND_PROCESSING_ATTEMPTS = 1`
- `REAL_INBOUND_DUPLICATE_ENVELOPES = 0`

### 5. Identity & User Resolution
- **First Contact Resolution:**
  - Resolved phone number to single new `User`, `ChannelIdentity`, and `ChannelIdentityLink`.
  - Zero duplicate users created.
  - Zero cross-user linking.
- `CANARY_IDENTITY_CREATED_OR_REUSED = CREATED`
- `CANARY_USER_CREATED_OR_REUSED = CREATED`
- `CANARY_DUPLICATE_USERS = 0`
- `CANARY_DUPLICATE_IDENTITIES = 0`

### 6. First-Contact Orchestration & Outbox Intent
- **Orchestration Execution:** For inbound text `"oi"`, TapJob first-contact career assistant greeting was selected by orchestration.
- **Outbox Intent:** Exactly one logical outbound message created in `OutboundMessageQueue`:
  - Recipient: Canary test number (`********8733`)
  - Intent count: 1
- `GREETING_ORCHESTRATION_EXECUTED = YES`
- `GREETING_OUTBOX_INTENTS_CREATED = 1`

### 7. Real TapJob Outbound Dispatch
- **Dispatcher:** Celery Beat / Worker background task dispatched outbound queue entry via Evolution adapter:
  - Endpoint: `POST https://evo.koglim.com.br/message/sendText/TapJob`
  - Body: Greeting text (`"Olá! Bem-vindo ao TapJob. Sou seu assistente de carreira pessoal..."`)
- **Outbox Final Status:** `SENT`, provider message ID: `3EB0CAC4CDF3F4B420A546`.
- `TAPJOB_OUTBOX_CREATED = YES`
- `TAPJOB_OUTBOX_FINAL_STATUS = SENT`
- `TAPJOB_PROVIDER_MESSAGE_ID_PRESENT = YES`
- `EVOLUTION_SENDTEXT_RUNTIME_VERIFIED = YES`
- `TAPJOB_TO_CANARY_SENDS = 1`

### 8. Canary Reply Reception Verification
- **Query:** `POST https://evo.koglim.com.br/chat/findMessages/TapJob-Canary-Client`
- **Result:** Reply found with:
  - `fromMe = false` from Canary perspective
  - Message ID: `3EB0CAC4CDF3F4B420A546`
  - Content non-empty (160 characters): `"Olá! Bem-vindo ao TapJob. Sou seu assistente de carreira pessoal.\nPara começar, envie seu currículo em PDF ou compartilhe suas principais experiências e competências."`
  - Timestamp (T5): 1792004477.5 s
- `CANARY_REPLY_FOUND = YES`
- `CANARY_REPLY_NONEMPTY = YES`
- `CANARY_REPLY_FROM_TAPJOB = YES`
- `CANARY_REPLY_PROVIDER_MESSAGE_ID = 3EB0CAC4CDF3F4B420A546`

### 9. Outbound fromMe Echo Safety
- **Behavior:** Evolution instance `TapJob` generated an echo `messages.upsert` event for its own outbound message with `key.fromMe = true`.
- **Filtering:** TapJob provider boundary filter identified `fromMe = true` and rejected the event from creating an inbound user message.
- **Consequences:**
  - Zero recursive user or envelope creation.
  - Zero recursive greeting orchestration.
  - Zero recursive outbox entries.
  - Message loop prevented completely.
- `OUTBOUND_FROMME_EVENT_OBSERVED = YES`
- `OUTBOUND_FROMME_FILTERED = YES`
- `OUTBOUND_ECHO_ENVELOPE_CREATED = NO`
- `OUTBOUND_ECHO_RECURSIVE_OUTBOX_CREATED = 0`

### 10. Live Replay / Idempotency Test
- **Replay Execution:** Captured real inbound `messages.upsert` payload (with message ID `3EB0F08E1C1E850D2D8D21`) was replayed directly against the webhook endpoint with valid `X-Webhook-Secret`.
- **Response:** HTTP 202 `{"status":"accepted"}`.
- **Audit After 10s Window:**
  - Zero duplicate envelopes created.
  - Zero duplicate users or identities created.
  - Zero duplicate outbox entries created.
  - Exactly zero additional WhatsApp messages dispatched to Canary (`TOTAL_CANARY_REPLIES = 1`).
- `REAL_EVENT_REPLAY_EXECUTED = YES`
- `REPLAY_HTTP_STATUS = 202`
- `REPLAY_DUPLICATE_ENVELOPES = 0`
- `REPLAY_DUPLICATE_USERS = 0`
- `REPLAY_DUPLICATE_OUTBOX_INTENTS = 0`
- `REPLAY_ADDITIONAL_WHATSAPP_SENDS = 0`

### 11. Final Send Operations Count
- Real message Canary -> TapJob: 1
- Real message TapJob -> Canary: 1
- Total real WhatsApp operations: 2
- `TOTAL_REAL_WHATSAPP_SEND_OPERATIONS = 2`

### 12. Latency Evidence
- T0 (Canary send accepted): 1792004467.4 s
- T5 (Canary received reply): 1792004477.5 s
- `END_TO_END_REPLY_MS = 10078.6` (~10.1 seconds, dominated by 5-second Celery Beat dispatch interval)

### 13. Post-Canary Runtime Health
- Public `/ready` endpoint: HTTP 200 (`status = ok`, `postgresql = ok`, `redis = ok`)
- All 5 EasyPanel services (`tapjob-api`, `tapjob-worker`, `tapjob-beat`, `tapjob-postgres`, `tapjob-redis`) running healthy with zero restart loops or runaway retries.
- `API_HEALTH_AFTER = healthy`
- `WORKER_HEALTH_AFTER = healthy`
- `BEAT_HEALTH_AFTER = healthy`
- `POSTGRES_HEALTH_AFTER = healthy`
- `REDIS_HEALTH_AFTER = healthy`

### 14. Phase B Invariants Preserved
- `CUSTOMER_OR_THIRD_PARTY_SENDS = 0`
- `EVOLUTION_RESTARTED = NO`
- `N8N_MUTATED = NO`
- `PRODUCTION_MUTATED = NO`
- `CRITICAL_FINDINGS = 0`
- `HIGH_FINDINGS = 0`
- `MEDIUM_BLOCKING_FINDINGS = 0`

