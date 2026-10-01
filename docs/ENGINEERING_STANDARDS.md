# Engineering Standards — TapJob

**Status:** Foundation standard

This document defines the coding/configuration rules that keep TapJob maintainable as the product grows across mobile, WhatsApp, web, browser extension and backend.

## 1. Principle: one source of truth per concern

Every concern has one authoritative owner.

Examples:
- visual values -> Design Tokens;
- product identity -> Product Config;
- deployment-specific values -> Environment Config;
- remote runtime switches -> Feature Flags;
- domain states -> Domain enums/state machines;
- API schema -> OpenAPI/contracts;
- user career facts -> Professional Truth Store;
- external connector capability -> Connector Capability Registry.

Do not solve the same concern in multiple files because it is convenient locally.

## 2. No magic values

Avoid unexplained literals in feature code:
- colors;
- timeouts;
- retry counts;
- page sizes;
- score thresholds;
- file-size limits;
- URLs;
- provider names;
- status strings;
- role strings;
- cache TTLs.

Put them in the correct layer:
- design token;
- typed config;
- domain constant;
- policy;
- capability manifest.

A constant should have semantic meaning. Moving every number into a giant `constants.ts` file is not sufficient architecture.

## 3. Configuration model

Use four distinct configuration classes.

### 3.1 Product configuration
Stable identity/product defaults:
- product name;
- default locale;
- default currency;
- public support/contact metadata;
- deep-link scheme;
- public product domains.

Source: versioned configuration file.

### 3.2 Environment configuration
Deployment-specific values:
- API base URL;
- database URL;
- Redis URL;
- storage endpoint;
- OAuth client IDs where public;
- observability endpoint;
- environment name.

Environments:
- local;
- development;
- staging;
- production.

Validate configuration at application startup. Fail fast on missing required values.

### 3.3 Secrets
Secrets never live in source code or public client configuration.

Examples:
- database passwords;
- OAuth client secrets;
- LLM keys;
- signing keys;
- provider private credentials.

Use server-side environment/secret management.

For Expo:
- treat every `EXPO_PUBLIC_*` variable as public;
- never place a secret in `EXPO_PUBLIC_*`;
- mobile/extension never receive private provider credentials.

### 3.4 Feature flags
Use feature flags for:
- staged rollout;
- risky connector kill switches;
- beta features;
- compliance-gated integrations;
- operational degradation/fallback.

Flags must have:
- owner;
- default value;
- expiry/review date for temporary flags;
- safe fallback.

Do not use feature flags as permanent substitutes for clean domain logic.

## 4. Configuration precedence

When a setting can vary, document precedence explicitly.

Default direction:

```text
code defaults
  < environment config
  < approved runtime/remote feature flag
  < explicit user preference (only when user-controllable)
```

Security policy and server-enforced restrictions cannot be overridden by client/user preferences.

## 5. Typed configuration

No feature should call `process.env` or read raw environment variables directly throughout the codebase.

Target:
- one validated settings module in backend;
- one validated public configuration module in mobile/web;
- typed accessors;
- startup validation.

Backend example direction:

```py
settings.api_timeout_seconds
settings.environment
settings.redis_url
```

Mobile example direction:

```ts
publicConfig.apiBaseUrl
publicConfig.environment
```

## 6. Domain enums and state machines

Never scatter strings like:

```text
"Applied"
"Interview"
"Offer"
"Rejected"
```

across components and services.

Application statuses and transitions belong to one canonical domain model/state machine.

The UI receives typed states and maps them to semantic presentation tokens.

Same rule for:
- apply modes;
- connector states;
- job-source health;
- resume statuses;
- interview stages;
- notification types.

## 7. API contract first

FastAPI/OpenAPI is the source of truth for external API contracts.

Target:
- typed request/response models;
- generated or validated TypeScript client when selected;
- versioned error codes;
- documented pagination/filter conventions;
- backward-compatible change policy.

Mobile code should not hand-build ad hoc response shapes.

Implemented in WP0.2-A: deterministic offline OpenAPI export (`tapjob_api.openapi_export`), `/api/v1` versioning with `/health`/`/ready` exempt, structural contract rules (`tapjob_api.http.contract_rules`), and a generated TypeScript client (`@hey-api/openapi-ts`, pinned exact) behind a `createApiClient`/`isApiError` facade (`packages/contracts`) — see `docs/adr/0003-api-contract-client-generation.md`.

## 8. Error model

Errors need stable machine codes plus human-safe messages.

Example direction:

```json
{
  "code": "JOB_SOURCE_RATE_LIMITED",
  "message": "Não foi possível atualizar esta fonte agora.",
  "requestId": "..."
}
```

Do not make business logic depend on English/Portuguese error message text.

Define:
- domain validation errors;
- authentication/authorization errors;
- integration errors;
- rate-limit errors;
- temporary/unavailable errors;
- user-action-required errors.

Implemented in WP0.2-A: `tapjob_api.http.errors.ErrorResponse`/`ErrorCode` and the exception handlers that guarantee every non-2xx response (including an unhandled exception) uses this envelope with a populated `requestId`; cursor pagination (`tapjob_api.http.pagination`) and the `Idempotency-Key` convention — see `docs/adr/0004-http-errors-pagination-correlation-idempotency.md`.

## 9. Internationalization

No user-facing string should be scattered as an untracked hardcoded literal once the i18n layer exists.

Initial language:
- Portuguese (Brazil).

Architecture must support later:
- Spanish;
- English;
- additional locales.

Centralize:
- UI strings;
- dates;
- currencies;
- pluralization;
- relative time;
- locale-aware numbers.

Do not concatenate sentences from fragments when translation grammar can change.

## 10. Date, time and timezone

- store timestamps in UTC;
- preserve user timezone separately;
- convert for presentation at the edge;
- use ISO 8601 across APIs;
- avoid ambiguous local datetime strings;
- jobs may preserve source-local published date when relevant.

Scheduling and reminders must always define which timezone owns the time.

## 11. Money

Money is never a floating-point business value.

Store/transport with explicit:
- amount;
- currency;
- period when salary-related;
- gross/net semantics where known.

Example:

```json
{
  "amountMinor": 300000,
  "currency": "BRL"
}
```

Or use a precise decimal representation approved by the data model.

## 12. Identifiers

- internal entities use canonical internal IDs;
- external provider IDs are separate fields;
- never expose database assumptions as provider identity;
- idempotency keys are explicit for retryable external actions.

## 13. Dependency direction

Preferred module dependency direction:

```text
UI / transport
  -> application/use-cases
  -> domain
  -> ports/interfaces
  <- infrastructure/adapters
```

External provider SDKs and scraping/apply implementations must remain adapters, not leak through the domain.

## 14. Provider abstraction

Product logic must not depend directly on one provider when a replaceable capability exists.

Examples:
- LLM -> AiGateway;
- object storage -> StoragePort;
- job source -> JobSource;
- notifications -> MessagingChannel;
- inbox -> InboxConnector;
- application execution -> ApplyConnector.

Avoid over-abstraction for internal code that has no realistic alternative.

## 15. State management boundaries

### Mobile
- TanStack Query: server state/cache;
- Zustand: local UI/editor state only when justified;
- SecureStore: sensitive device tokens;
- persistent business truth remains backend canonical.

Do not duplicate backend entities into multiple permanent local stores.

## 16. Data validation

Validate at every trust boundary:
- API input;
- external job source;
- Gmail payload;
- AI structured output;
- uploaded document metadata;
- browser-extension DOM extraction;
- webhook.

Never trust data just because it came from another internal module if that module is an external-input adapter.

## 17. AI output is untrusted data

LLM output must be:
- schema validated;
- grounded where required;
- policy checked;
- bounded in size;
- prevented from directly executing privileged side effects.

Prompt text is versioned when behavior matters.

## 18. Logging and observability

Use structured logs.

Required concepts:
- request/correlation ID;
- user ID only when safe/necessary and preferably internal pseudonymous ID;
- connector/source;
- operation;
- duration;
- outcome;
- safe error code.

Never log secrets or unnecessarily copy resume/e-mail content.

Metrics and traces should use stable names, not free-text labels.

## 19. Feature ownership and boundaries

Each feature/module should own:
- domain logic;
- use cases;
- tests;
- API endpoints if applicable;
- adapters;
- documentation.

Avoid giant generic folders such as:
- `utils/` containing unrelated business behavior;
- `helpers/` as a dumping ground;
- `common/` with unclear ownership.

Shared code must be shared because its semantics are genuinely cross-domain.

## 20. Naming conventions

Names explain intent, not implementation accidents.

Preferred:
- `calculate_match_score`;
- `ApplicationStatus`;
- `ResumeVersion`;
- `GmailInboxConnector`.

Avoid:
- `doStuff`;
- `data2`;
- `tempFinal`;
- `helperNew`.

Use one language for code identifiers: English.

Product copy remains localized.

## 21. Function/module size

No arbitrary line-count law, but split code when a unit:
- has multiple reasons to change;
- mixes policy and I/O;
- is difficult to unit-test;
- contains repeated conditional behavior;
- knows too much about unrelated domains.

Prefer small cohesive modules over both giant files and pointless micro-files.

## 22. Comments

Comments explain:
- why;
- tradeoffs;
- external quirks;
- security reasoning;
- non-obvious invariants.

Do not narrate obvious syntax.

Important decisions belong in docs/ADRs, not only comments.

## 23. Database standards

- all schema changes through Alembic;
- constraints enforce invariants where appropriate;
- indexes justified by query patterns;
- external IDs indexed where lookup requires;
- migrations tested;
- destructive migrations explicitly approved;
- timestamps consistent;
- soft delete only when product/legal behavior requires it;
- audit/timeline data separated from mutable current state where needed.

## 24. Background jobs

Every async job should define:
- idempotency;
- retry policy;
- backoff;
- timeout;
- dead-letter/failure handling;
- observability;
- duplicate behavior.

Never assume "the queue will only deliver once".

## 25. HTTP/external integration standards

Every outbound client should define:
- connect/read timeout;
- bounded retries;
- rate-limit behavior;
- user agent where appropriate;
- circuit/kill-switch policy;
- normalized error mapping.

No unbounded retry loops.

## 26. File/upload standards

Central policy defines:
- allowed MIME types;
- max sizes;
- storage namespace;
- filename handling;
- malware/security scan path;
- signed URL TTL;
- retention.

Feature screens must not invent upload limits independently.

## 27. Testing standards

### Backend
- unit tests for deterministic domain logic;
- contract tests for adapters;
- integration tests with real PostgreSQL/Redis where behavior depends on them;
- migration tests;
- AI evals for model behavior.

### Mobile
- unit tests for pure logic;
- component tests for meaningful interaction;
- E2E for critical journeys when infrastructure exists.

Bug fix rule:
when practical, reproduce with a failing test first.

## 28. Test data

- synthetic/fake personal data;
- no real private resume/e-mail/token fixtures committed;
- deterministic clocks/IDs where tests need repeatability;
- snapshots only when they provide real signal.

## 29. Linting, formatting and static analysis

WP0.1 should make style automatic.

Target:
- Python formatter/linter/type-check;
- TypeScript lint/type-check;
- formatting in CI;
- secret scan;
- dependency vulnerability scan.

Developer preference must not create formatting debates in PRs.

## 30. Dependency policy

Before adding a dependency ask:
- is it actively maintained?
- is the license acceptable?
- can standard library/current dependency solve it?
- what is bundle/runtime cost?
- does it bring transitive risk?
- is it compatible with Expo/server target?

Pin/lock reproducibly.

Do not add overlapping libraries for the same job without explicit rationale.

## 31. Feature flags and connector kill switches

Every risky external connector should be disableable without redeploying the entire product when practical.

Examples:
- Gmail ingestion;
- ATS apply connector;
- experimental job source;
- high-cost AI workflow.

The safe state is usually disabled/degraded, not uncontrolled retry.

## 32. Security defaults

- deny by default;
- least privilege;
- server-side secrets;
- explicit ownership checks;
- no mass assignment;
- safe upload handling;
- output escaping;
- prompt-injection separation;
- audit high-impact actions.

Security/privacy docs remain authoritative for details.

## 33. Performance budgets

Performance is an acceptance criterion, not an afterthought.

At minimum track when relevant:
- API latency;
- job ingestion duration;
- matching batch duration;
- AI latency/cost;
- mobile startup;
- large-list rendering;
- document render time.

Do not optimize blindly; measure before introducing complexity.

## 34. Graceful degradation

External systems fail.

The product should degrade deliberately:
- AI unavailable -> deterministic features remain usable where possible;
- one job source fails -> other sources continue;
- Gmail unavailable -> manual tracker still works;
- apply connector unsupported -> deep-link/manual mode;
- notification channel fails -> state remains consistent and retry is controlled.

## 35. Repository structure

Prefer feature/domain clarity.

Target direction:

```text
apps/
  mobile/
  web/
  extension/
services/
  api/
  worker/
packages/
  design-tokens/
  product-config/
  contracts/
infra/
docs/
```

Exact scaffold is finalized in WP0.1/WP0.2.

## 36. Review checklist for every implementation PR

Reviewer asks:
- Did this add a magic value?
- Did this duplicate existing configuration?
- Did this hardcode product/brand text?
- Did this leak a provider SDK into the domain?
- Did this add an untyped status string?
- Did this expose a secret to the client?
- Did this create a second source of truth?
- Are failure/retry paths defined?
- Are tests at the correct layer?
- Is documentation/state updated?
- If upstream code was reused, is the Source Reuse Ledger complete?

If the answer reveals architectural drift, fix the abstraction before adding more features.
