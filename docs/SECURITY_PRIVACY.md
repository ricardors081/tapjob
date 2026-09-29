# Security, Privacy & Trust — Tap Vaga

## 1. Security posture

Tap Vaga processes highly personal career data and may connect to e-mail/accounts. Security is product scope, not a later hardening phase.

## 2. Baseline controls

### Secrets
- environment/secret manager only;
- no secrets in git;
- no `EXPO_PUBLIC_*` for private provider keys;
- no ATS API keys in browser/mobile;
- rotate compromised credentials.

### Authentication
- secure token storage;
- short-lived access tokens;
- refresh rotation;
- device/session revocation;
- MFA-ready architecture.

### Authorization
- ownership checks on every user-scoped resource;
- admin boundary separated;
- external connector callbacks authenticated.

### Encryption
- TLS in transit;
- managed encryption at rest;
- application-level encryption for OAuth refresh tokens/critical external secrets.

## 3. LGPD principles

- purpose limitation;
- adequacy;
- necessity;
- free access;
- data quality;
- transparency;
- security;
- prevention;
- non-discrimination;
- accountability.

Required product capabilities:
- privacy notice;
- consent records where consent is lawful basis;
- export;
- correction;
- disconnect;
- deletion;
- data retention policy.

## 4. Gmail special handling

`gmail.readonly` is a Google Restricted Scope.

Before public launch of Inbox Intelligence:
- OAuth verification;
- privacy policy;
- scope minimization;
- security assessment when required by Google's rules;
- documented data flow;
- avoid full-body persistence by default.

Prefer:
metadata/snippet -> heuristic -> selective body fetch -> normalized event.

## 5. Sensitive application questions

The system must not infer protected/sensitive answers.

Always require explicit user confirmation for:
- disability;
- demographic self-ID;
- race/ethnicity;
- gender;
- veteran status;
- health;
- criminal/legal declarations;
- sponsorship/work authorization if not stored as confirmed fact.

Matching must not penalize users based on sensitive traits.

## 6. AI trust rules

- no fabricated career facts;
- no model-generated external action without policy;
- structured outputs validated;
- hallucination-sensitive workflows use deterministic checks;
- source facts included in artifact metadata;
- high-impact actions auditable.

## 7. Prompt injection

Job descriptions and e-mails are hostile input.

Mitigations:
- delimit untrusted content;
- system policies outside user/external content;
- tool allowlist;
- no secret exposure tools;
- JSON schemas;
- reject instructions inside external content;
- log detected attacks.

## 8. Browser extension

- minimal permissions;
- site-scoped activation;
- no password fields;
- no hidden credential capture;
- no final submit in initial version;
- sanitize DOM-derived text;
- signed backend session.

## 9. Application submission

- idempotency;
- duplicate detection;
- rate limiting;
- consent;
- evidence;
- connector kill switch;
- audit log.

## 10. Logging

Never log:
- raw access/refresh tokens;
- passwords;
- full CPF;
- full e-mail bodies by default;
- complete resume text in generic logs;
- LLM provider keys.

Use correlation IDs and redacted structured logs.

## 11. Abuse prevention

Protect against:
- mass unsolicited applications;
- account farming;
- spam recruiter outreach;
- credential stuffing;
- prompt injection;
- malicious uploaded files;
- webhook spoofing;
- notification abuse.

## 12. File security

Uploads:
- MIME validation;
- size limits;
- malware scanning strategy;
- randomized storage keys;
- private buckets;
- signed URLs;
- metadata stripping where appropriate.

## 13. External API trust

Every connector must have:
- scoped credentials;
- timeout;
- retry policy;
- rate limit;
- circuit breaker/kill switch;
- audit;
- capability registry.

## 14. Incident readiness

Before production:
- incident severity model;
- secret rotation playbook;
- data breach response;
- provider outage fallback;
- restore test;
- dependency vulnerability scanning.

## 15. Security gates before launch

- threat model complete;
- authz tests;
- secrets scan;
- dependency scan;
- mobile secure storage verified;
- token encryption verified;
- webhook validation;
- backup/restore test;
- account deletion test;
- privacy/data map reviewed.
