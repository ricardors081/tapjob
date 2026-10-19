# Security, Privacy & Trust — TapJob

## 1. Security posture

TapJob processes highly personal career data and may connect to e-mail/accounts. Security is product scope, not a later hardening phase.

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

### 3.1. Bases Legais e Privacidade no Canal WhatsApp (LGPD)

Para as interações conversacionais e onboarding de pessoas candidatas no canal WhatsApp:

1. **Bases Legais Candidatas / Posição de Trabalho Preliminar (Working Legal Bases)**:
   - **Art. 7º, V da LGPD (Execução de Contrato / Procedimentos Preliminares)**: Base de trabalho candidata para o tratamento de dados profissionais fornecidos a pedido do titular (candidato) para execução do serviço pessoal de carreira (análise de perfil, matching com vagas e geração de Application Kit).
   - **Art. 7º, IX da LGPD (Legítimo Interesse)**: Base de trabalho complementar para descoberta e recomendação de oportunidades de carreira compatíveis com as legítimas expectativas do titular.
   - **Status de Avaliação Jurídica**: As bases legais acima constituem a posição de trabalho preliminar do controlador e NÃO constituem conclusão jurídica definitiva.
   - `LEGAL_REVIEW_REQUIRED_BEFORE_REAL_USER_PILOT=YES`: Revisão jurídica formal independente é estritamente obrigatória antes de qualquer piloto comercial ou expansão com usuários reais fora do grupo controlado.

2. **Distinção entre Dados Profissionais Ordinários e Dados Pessoais Sensíveis (Art. 11)**:
   - **Dados Profissionais Ordinários**: Nome, telefone/JID de contato, histórico profissional, experiências, formação acadêmica e competências declaradas.
   - **Possíveis Dados Pessoais Sensíveis**: Currículos enviados por candidatos podem conter, de forma incidental e não solicitada, dados sensíveis protegidos pelo Art. 11 da LGPD (ex.: dados de saúde/PCD, raça/etnia, filiação sindical, convicção religiosa ou política).
   - **Diretriz de Minimização e Não-Solicitação**: O TapJob **NÃO solicita intencionalmente** dados sensíveis para o piloto de WhatsApp.
   - Uma base legal para dados ordinários (Art. 7º) **NÃO cobre automaticamente** dados sensíveis (Art. 11).
   - O pipeline de ingestão de currículos deve aplicar minimização contínua: se dados sensíveis forem detectados de forma incidental, eles não devem ser propagados para o Professional Truth Store nem utilizados em matching, devendo o enquadramento jurídico final ser definido na revisão jurídica pré-piloto.

3. **Mecanismos de Transparência e Exercício de Direitos**:
   - **Saudação Inicial**: Transparência ativa e concisa na primeira mensagem de contato, informando a finalidade exclusiva de carreira e instruindo o candidato a digitar `privacidade`.
   - **Consulta de Privacidade**: O comando `privacidade` detalha as categorias de dados coletados, a finalidade de uso, o prazo geral de retenção e os canais para exercício de direitos.
   - **Solicitação de Exclusão de Dados**: O candidato pode enviar `excluir meus dados` a qualquer momento para manifestar seu direito de eliminação. Durante o piloto controlado, para prevenir exclusões indevidas via canal não autenticado, a eliminação definitiva é orientada e processada via procedimento canônico autorizado com o operador do piloto.


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
- LLM provider keys;
- a `ProfileFact`'s own content (`value`) in general logs or audit metadata — salary figures,
  work-authorization answers, experience/education text included (WP1.2,
  `docs/adr/0010-career-profile-truth-store.md` §H). Audit metadata for a Truth Store mutation is
  allowlisted to field names, `fact_type`, `fact_id` and `profile_version` only.

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

**WP1.3 — Resume Import (`docs/adr/0011-resume-import.md`), implementation status (VERIFIED):** real binary
signature/container validation (never trusting `Content-Type`/extension alone), size limits, ZIP-
bomb-style compression/member-count bounds inspected from archive metadata before any
decompression, randomized server-generated storage keys, and a private Cloudflare R2 bucket are all
implemented. **Malware scanning is NOT implemented.** File-format/container-structure validation is
a structural-integrity check, not a malware scan, and this document does not claim otherwise — a
scanner integration remains a documented, open gap for this capability, to be added when a concrete
need/incident justifies the added infrastructure, not implemented speculatively. Signed URLs are not
issued by this checkpoint (no download endpoint exists in WP1.3's accepted API surface).
**Pre-production gate:** raw uploaded source blob retention/deletion lifecycle is not yet implemented
end-to-end (account deletion skeleton does not yet reach stored source blobs in R2/local storage; see
ADR-0011 and Gate 1).

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