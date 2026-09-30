# Delivery Order — TapJob

**Status:** Accepted execution order  
**Date:** 2026-09-30  
**Scope:** delivery sequencing only; existing product scope, architecture, security and source-reuse decisions remain unchanged.

## 1. Canonical order

TapJob will be delivered in this order:

1. **Engineering Foundation**
2. **Minimum Career Core**
3. **WhatsApp-first product**
4. **Mobile App**
5. **Browser Extension**
6. **Inbox Intelligence / Interviews / advanced analytics / official integrations expansion**

This is a sequencing decision, not a reduction in product scope.

## 2. Why WhatsApp comes before the full app

WhatsApp can deliver meaningful TapJob value without depending on the mobile app.

The WhatsApp channel talks directly to the same Career Core used later by mobile and the extension.

```text
WhatsApp ───────┐
Mobile App ─────┼──> TapJob Career Core
Extension ──────┘
                    ├── Profile / Truth Store
                    ├── Jobs / Matching
                    ├── Resume / Application Kit
                    ├── Applications / Tracker
                    ├── Career Agent
                    └── Notifications
```

No channel owns canonical business state.

## 3. Minimum Career Core before WhatsApp

WhatsApp must not become a scripted chatbot disconnected from the product.

Before the WhatsApp alpha is useful, the backend must support at minimum:

- internal User identity;
- external/channel identity linking;
- Professional Truth Store;
- PDF/DOCX resume intake and extraction;
- career preferences;
- manual job URL/description import;
- minimal job normalization;
- explainable match;
- resume tailoring grounded in confirmed facts;
- basic Application Kit;
- basic application tracker/timeline;
- Career Agent tool layer;
- audit/idempotency;
- document storage;
- asynchronous jobs.

These are minimal vertical-slice versions. Full advanced modules continue to evolve in their normal work packages.

## 4. WhatsApp-first alpha journey

Target first meaningful product flow:

```text
User says "oi"
  -> TapJob identifies/creates channel identity
  -> asks for resume or basic profile
  -> receives PDF/DOCX
  -> extracts facts
  -> asks user to confirm facts
  -> asks target-role/preferences
  -> receives/imports a job
  -> calculates/explains match
  -> prepares tailored resume + basic Application Kit
  -> delivers result/link/document
  -> records application intent/status
  -> supports reminders/follow-up
```

The user can receive real value before installing the mobile app.

## 5. Relationship between WhatsApp and mobile

### WhatsApp excels at
- conversational onboarding;
- sending a resume;
- job alerts;
- natural commands;
- "vale a pena essa vaga?";
- preparing an application;
- receiving generated documents;
- quick status updates;
- reminders/follow-ups;
- interview reminders;
- proactive agent actions.

### Mobile excels at
- Resume Studio editing;
- detailed ATS views;
- job-feed browsing/filtering;
- Kanban/pipeline;
- full application timeline;
- analytics;
- document management;
- integrations/settings;
- privacy controls;
- large Application Kit review.

The mobile app is a richer visual control plane over the same Career Core, not a prerequisite for WhatsApp.

## 6. Browser extension position

The extension is not required for WhatsApp or mobile to work.

Its primary job is Assisted Apply on desktop:
- detect supported application page;
- map fields to confirmed profile facts;
- preview fill plan;
- fill repetitive fields;
- attach approved resume;
- show generated answers;
- stop for unresolved/sensitive fields;
- sync confirmed application result back to TapJob.

It enters after the core WhatsApp and mobile experiences are stable enough to provide the profile/resume/application contracts it consumes.

## 7. WhatsApp transport strategy

### Initial transport
Use the owner's existing **Evolution API** deployment to accelerate development and controlled early validation at low cost.

The TapJob domain must never import Evolution-specific business semantics.

Evolution sits behind a provider-neutral `MessagingChannel` adapter.

### Migration target
The architecture must remain compatible with the official **WhatsApp Cloud API**.

Before broad commercial scale, re-evaluate and migrate the production transport to the official Cloud API when policy, reliability, account ownership and messaging-volume requirements make it appropriate.

Evolution API currently supports both a Baileys/WhatsApp-Web mode and the official WhatsApp Cloud API. This allows the TapJob adapter boundary to preserve product logic while changing transport.

### Non-negotiable
No Career Core rule may depend on:
- QR/session quirks;
- Baileys-specific IDs;
- Evolution-specific webhook shapes;
- provider-specific retry semantics.

Normalize all inbound/outbound events at the channel adapter boundary.

## 8. n8n role

n8n is an **auxiliary integration/orchestration tool**, not the TapJob brain.

Good uses:
- temporary integration bridge;
- operational workflow;
- internal alert;
- prototype before code promotion;
- non-critical webhook transformation;
- administrative automation;
- support/ops workflows.

Not allowed as canonical owner of:
- user state;
- Professional Truth Store;
- matching logic;
- application state machine;
- authorization;
- idempotency;
- Career Agent policy;
- payment/entitlement truth;
- critical business rules.

Critical logic belongs in versioned/tested TapJob code (FastAPI/domain/Celery/PostgreSQL).

## 9. EasyPanel role

The existing EasyPanel installation may be used as an operations/deployment UI for Docker services on the owner's Linux server.

It is an operational convenience, not an architectural dependency.

TapJob services must remain reproducible from repository configuration/containers without requiring hidden EasyPanel-only state.

## 10. Identity across channels

Canonical identity is internal:

```text
User (TapJob UUID)
  ├── Firebase identity
  ├── WhatsApp identity
  └── future identities
```

WhatsApp-first onboarding may create a channel-linked user before a Firebase login exists.

When the user later signs into the mobile app, the account-linking flow associates the Firebase identity with the same internal User after verified ownership/confirmation.

Never create separate canonical career profiles merely because the user arrived through a different channel.

## 11. What does not change

This delivery-order decision does **not** change:

- AIApply-first product positioning;
- Huntr/Simplify complementary references;
- Brazil-first strategy;
- the 10 audited repository donors;
- Professional Truth Store;
- Apply Engine modes;
- no universal headless auto-apply promise;
- Firebase as mobile auth;
- PostgreSQL/Redis/Celery/R2 baseline;
- Linux + Docker initial hosting;
- design-system standards;
- security/LGPD requirements;
- PR/CI/review governance.

## 12. Immediate execution

The currently authorized work remains:

**WP0.1 — Engineering Foundation**

After WP0.1, WP0.2 must finalize the contracts required for the minimum Career Core and channel adapters.

The post-foundation checkpoint plan will be sliced to reach the WhatsApp vertical journey as early as possible without bypassing domain, security or test boundaries.
