# Delivery Order — TapJob

**Status:** Accepted execution order  
**Date:** 2026-10-09  
**Scope:** delivery sequencing only; existing product scope, architecture, security and source-reuse decisions remain unchanged.

## 1. Canonical order

TapJob will be delivered in this order:

1. **Engineering Foundation** (VERIFIED: WP0.1, WP0.2)
2. **Minimum Career Core** (VERIFIED: WP1.1, WP1.2, WP1.3, WP2.1-A, WP2.1-B, WP2.2)
3. **WhatsApp-first product** (Next Product Goal: Minimum WhatsApp Alpha Vertical Slice)
4. **Mobile App** (Mobile Readiness is parallel-safe; full Mobile Product Experience after WhatsApp Alpha)
5. **Browser Extension**
6. **Inbox Intelligence / Interviews / advanced analytics / official integrations expansion**

This is a sequencing decision, not a reduction in product scope.

## 2. Canonical Principle of Channels & Why WhatsApp comes before the full app

TapJob é **UM produto com UM Career Core**. WhatsApp, Mobile App e Browser Extension são superfícies/canais sobre o mesmo estado canônico e as mesmas regras de negócio:

```text
WhatsApp ──┐
Mobile ────┼──> TapJob Core
Extension ─┘
```

Mesma:
- User identity;
- Truth Store;
- Jobs;
- Matching;
- Resume;
- Application Kit;
- Applications/Tracker;
- Career Agent;
- Notifications policy.

### Invariantes de Canais
Nenhum canal pode possuir:
- profile truth próprio;
- matching próprio;
- application state próprio;
- resume truth próprio;
- Career Agent policy própria;
- business rule própria.

O backend/Core permanece estritamente **channel-neutral**. Canais podem variar APRESENTAÇÃO e CAPACIDADES DE INTERFACE, nunca a verdade canônica.

WhatsApp can deliver meaningful TapJob value without depending on the mobile app. The WhatsApp channel talks directly to the same Career Core used later by mobile and the extension. No channel owns canonical business state.

## 3. Minimum Career Core before WhatsApp

WhatsApp must not become a scripted chatbot disconnected from the product.

A fundação técnica e os blocos essenciais do Career Core já estão verificados:
- WP0.1 & WP0.2 (Foundation, HTTP conventions, internal ports, correlation & deployment contracts) = **VERIFIED**;
- WP1.1 (Identity & User Account, canonical internal User UUID) = **VERIFIED**;
- WP1.2 (Career Profile & Professional Truth Store) = **VERIFIED**;
- WP1.3 (Resume Import, bounded extraction, deterministic fact candidates) = **VERIFIED**;
- WP2.1-A & WP2.1-B (Jobs domain, manual/URL intake, hosted ATS connectors Greenhouse/Lever/Ashby) = **VERIFIED**;
- WP2.2 (Deterministic Matching Engine, hard-filter gates, lexical scoring, explainability) = **VERIFIED**.

O backend suporta a verdade profissional e a inteligência determinística de matching. O próximo passo de produto é integrar a fatia mínima ponta a ponta para a jornada no WhatsApp.

## 4. WhatsApp-first alpha journey (Minimum WhatsApp Alpha Vertical Slice)

O próximo checkpoint de produto é o **Minimum WhatsApp Alpha Vertical Slice**.

Target first meaningful product flow:

```text
User says "oi"
  -> channel identity
  -> TapJob User linkage
  -> resume/basic profile
  -> PDF/DOCX intake
  -> fact extraction
  -> confirmation
  -> target role/preferences
  -> job import/recommendation
  -> Matching Engine
  -> match explanation
  -> minimal grounded resume tailoring
  -> basic Application Kit
  -> document/link handoff
  -> basic application tracking
  -> reminders/follow-up
  -> Career Agent tools
  -> WhatsApp interaction surface
```

The user can receive real value before installing the mobile app.

### Natureza Cross-WP do Slice
Este slice antecipa estritamente a intersecção mínima de capacidades dos WPs subsequentes (WP2.3 feed/alerts, WP3.x tailoring/kit, WP4.x tracker/agent/WhatsApp) necessária para viabilizar a experiência conversacional completa.
**Isso NÃO reduz nem remove os escopos completos existentes de WP2.3, WP3.x e WP4.x**, cujas versões completas continuam planejadas no `IMPLEMENTATION_PLAN.md`.

## 5. Relationship between WhatsApp and Mobile: Mobile Readiness vs Mobile Product Experience

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

Existe uma distinção arquitetural e de entrega estrita entre:
1. **Mobile Readiness** (infraestrutura de execução e compilação do app);
2. **Mobile Product Experience** (telas, fluxos visuais e features completas).

### A) Mobile Readiness é Parallel-Safe
A prontidão técnica do aplicativo mobile pode avançar em paralelo com o desenvolvimento do WhatsApp, desde que não bloqueie nem atrase a entrega do vertical slice do WhatsApp.

**Escopo estrito permitido de Mobile Readiness:**
- Expo Development Build;
- Android native/build readiness;
- iOS native/build readiness;
- EAS configuration/profiles quando aplicável;
- environment configuration;
- Firebase mobile/auth wiring;
- generated OpenAPI API client integration foundation;
- session/secure-storage foundation;
- navigation shell;
- design tokens/design-system primitives;
- error/loading/network foundations;
- deep-link infrastructure;
- push-notification plumbing quando houver contrato backend;
- Sentry/observability;
- build/compile CI;
- package/bundle identifiers/configuration quando necessário;
- icon/splash/native config quando necessário.

*Essas atividades de prontidão técnica NÃO significam que o Mobile Product começou.*

### B) Mobile Product Experience continua depois do WhatsApp Alpha
Até o WhatsApp Alpha vertical slice estar funcional de ponta a ponta, **NÃO priorizar** a construção de:
- visual Job Feed;
- Job Detail completo;
- Match visual completo;
- Resume Studio completo;
- Kanban/Application Tracker visual;
- analytics;
- document manager completo;
- large Application Kit review;
- settings/integrations UI completos;
- dezenas de telas de produto.

Esses elementos pertencem exclusivamente ao estágio de Mobile Product. O app móvel poderá oferecer MAIS possibilidades visuais e de controle que o WhatsApp, mas sempre operando sobre o mesmo Core.

### 5.1 Macro-Delivery Sequencing Direction
Após este alinhamento documental, a próxima execução de produto deve ser planejada preferencialmente em dois macro-checkpoints:

- **CHECKPOINT A — WhatsApp Alpha Core:**
  Capacidades mínimas compartilhadas no Core:
  - channel identity model/linking;
  - minimal feed/alert backend se necessário ao vertical slice;
  - grounded resume tailoring mínimo;
  - basic Application Kit;
  - minimal Application Tracker;
  - Career Agent Core/tools necessários ao alpha.

- **CHECKPOINT B — WhatsApp Alpha Surface + Mobile Readiness:**
  - MessagingChannel adapter;
  - Evolution API initial adapter;
  - normalized inbound/outbound contracts;
  - webhook;
  - conversational orchestration;
  - confirmations;
  - document handoff;
  - reminders/follow-up;
  - deep links;
  - Mobile Readiness infrastructure.

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

Estado atual dos work packages:
- WP0.1 & WP0.2 = **VERIFIED**
- WP1.1, WP1.2 & WP1.3 = **VERIFIED**
- WP2.1-A & WP2.1-B = **VERIFIED**
- WP2.2 = **VERIFIED** (PR #40, squash commit `2aedd2b6d281214296009d3ff058d6fdb5677720`, post-merge CI `37997452936` 8/8 green, independent review APPROVED)

**Próximo objetivo autorizado:**
**Minimum WhatsApp Alpha Vertical Slice** (cross-WP minimum slice para entregar o primeiro produto ponta a ponta utilizável sobre o Core compartilhado).

**Parallel-Safe:**
Apenas **Mobile Readiness** (infraestrutura nativa, compilação/build CI, Expo dev client, wiring de autenticação e contratos de API gerados).

**Mobile Product Experience:**
Permanece estritamente após a validação do WhatsApp Alpha vertical slice (telas completas de Job Feed, Job Detail, Match visual, Resume Studio, Kanban/Tracker, analytics, document manager, large Application Kit review e settings UI).

**Browser Extension:**
Permanece após as superfícies Mobile/Core conforme já definido.
