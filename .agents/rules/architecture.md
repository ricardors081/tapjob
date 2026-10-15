---
trigger: model_decision
description: Core architecture boundaries, Career Core invariants, channel decoupling, and durable data stores
---

# Architecture Invariants & Boundaries

## 1. Single Career Core

- **One Backend Core**: A single Career Core (`services/api` and `services/worker`) serves all customer surfaces: WhatsApp, Mobile, and future Browser Extensions.
- **Business Logic Placement**: All business logic, matching algorithms, ATS rules, and profile truth belong strictly inside Career Core.
- **No Duplicate Logic**: Mobile clients and channel adapters must consume Career Core API contracts and must never re-implement or duplicate business rules locally.

## 2. Channel Decoupling

- **Channels as Adapters**: Channels (WhatsApp, Mobile, Web) are interfaces/adapters that translate surface-specific protocols into Career Core contracts.
- **Evolution API Boundaries**: Evolution API is an external provider transport, NOT an owner of business logic, state, or user flows.
- **n8n Scope**: n8n must NOT own critical business state or the primary TapJob business path; it is strictly limited to auxiliary ops/integrations.

## 3. Durable Storage and Execution Infrastructure

- **Canonical State**: PostgreSQL is the durable, canonical store for user profiles, career truth, jobs, and applications.
- **Execution Infrastructure**: Redis and Celery provide asynchronous queueing, scheduled tasks, and transient execution infrastructure.
- **Blob Storage**: Cloudflare R2 provides object storage for resumes and artifacts; local disk storage must not be relied upon in staging or production.

## 4. Commercial & Delivery Priority

- **WhatsApp-First**: Finish WhatsApp completely -> controlled testers -> hardening -> commercially usable WhatsApp.
- **Mobile Next**: Mobile becomes the primary next surface once the WhatsApp commercial gate is achieved.
