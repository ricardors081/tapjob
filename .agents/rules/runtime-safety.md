---
trigger: always_on
description: Runtime safety gates, production protection, staging boundaries, and messaging controls
---

# Runtime Safety & Autonomy Boundaries

## 1. Autonomy Tiers

- **Tier A — Autonomous Development**:
  - Unrestricted local work: branch code, tests, documentation, builds, linting, formatting, local containers, atomic commits, feature branch pushes, PR creation, and internal diagnosis/repair loops.
- **Tier B — Controlled Staging**:
  - Permitted only when the task explicitly grants staging authorization: EasyPanel staging deployments, database migrations on staging, Evolution staging configurations, and canary test runs.
- **Tier C — Human Gate (Strictly Blocked for Autonomous Agents)**:
  - Always requires explicit human authorization and confirmation:
    - Production deployments;
    - Production database mutations or irreversible migrations;
    - Customer or third-party WhatsApp sends;
    - Secret rotation, credential invalidation, or key generation;
    - DNS, domain, or hosting infrastructure transfers.

## 2. Messaging & Live Transport Controls

- **No Uncontrolled WhatsApp Sends**: Agents must never trigger unapproved live sends to real WhatsApp recipients or third parties.
- **Canary Scope**: Real messaging tests must be constrained to dedicated test numbers/identities under explicit canary scopes.
- **No Mock Bypasses**: Never mock or bypass external dependencies in a manner that manufactures false verification evidence.
