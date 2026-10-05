# Domain & Data Model — TapJob

## 1. Core entities

### User (WP1.1, `docs/adr/0009-identity-auth-user-account.md`)
TapJob-minted UUID is the one canonical identity; never a Firebase UID or any other external
provider's identifier (see `ExternalIdentity` below).
- id (UUID, canonical)
- status (`active`/`disabled`)
- locale (seeded from product-config's `defaultLocale`, user-overridable)
- timezone (neutral default `Etc/UTC`, never inferred from server/request location; device/user
  supplies a real value)
- created_at / updated_at

No email/phone field on `User` itself: email is never the primary identity or an account-merge
key (see `ExternalIdentity.email_snapshot`, a display/audit-only snapshot).

### ExternalIdentity (WP1.1)
Links one external provider identity to exactly one `User`. A `User` may exist with zero rows here
(e.g. a future WhatsApp-first onboarding before Firebase is ever linked).
- id
- user_id (FK -> User)
- provider (`firebase` today; designed to admit future providers, e.g. `whatsapp`, without a
  destructive migration)
- provider_subject (e.g. the Firebase UID) — `UNIQUE(provider, provider_subject)`, the concurrency
  backstop for first-login races and the only key bootstrap resolution ever looks up by (never
  `email_snapshot` — no auto-link by email, ever)
- email_snapshot / email_verified (snapshot only, not re-read live, never a lookup key)
- created_at / last_seen_at

### UserDevice (WP1.1)
A mobile installation, identified by a TapJob-minted random `installation_id` — never an IMEI,
hardware serial, advertising ID or other invasive fingerprint.
- id
- user_id (FK -> User)
- installation_id — `UNIQUE(user_id, installation_id)`
- platform (`ios`/`android`/`web`)
- app_version
- push_token (nullable; future FCM delivery, not wired to anything in WP1.1; never logged)
- created_at / last_seen_at / revoked_at

### ConsentRecord (WP1.1)
Append-only: a changed decision is a new row, never an overwrite. No fixed purpose-key catalog is
enforced yet (no final legal-purpose list approved for the product).
- id
- user_id (FK -> User)
- purpose_key / policy_version
- decision (`granted`/`withdrawn`)
- source
- recorded_at

### AccountDeletionRequest (WP1.1 — skeleton only, never claims data was actually deleted)
- id
- user_id (FK -> User)
- status (`requested`/`cancelled`/`processing`/`completed`/`failed`; at most one
  `requested`/`processing` row per user at a time)
- requested_at / cancelled_at / updated_at

### AccountAuditEvent (WP1.1 — append-only)
Allowlisted metadata only — never args/kwargs, a password, a Firebase token, or an `Authorization`
header value.
- id
- user_id (FK -> User)
- event_type / outcome (`success`/`failure`)
- metadata (JSONB, allowlisted fields only)
- correlation_id (joins back to the originating request's logs, shared with ADR-0006's
  correlation convention)
- recorded_at

### CareerProfile (WP1.2, `docs/adr/0010-career-profile-truth-store.md`)
One canonical `CareerProfile` per `User` (`UNIQUE(user_id)`), lazily created on first mutation —
`GET` never creates a row as a side effect; a `User` with none yet gets a virtual response at
`profileVersion: 0`.
- id
- user_id (FK -> User, `UNIQUE`)
- headline (nullable)
- summary (nullable)
- target_roles (string list; preferences with deeper substructure are `ProfileFact`s instead, see
  below — never duplicated across both)
- profile_version (starts at 0; bumped by exactly one on every logical mutation, see
  "Optimistic concurrency")
- created_at / updated_at

### ProfileFact (WP1.2)
A typed, immutable, provenance-carrying professional claim — never arbitrary JSON and never edited
in place. Replacing a claim's value creates a **new** row in the same `lineage_id` and marks the old
row `superseded`; the old row's original value remains permanently readable by its own id.
Retraction is logical only (`status = retracted`) — a `ProfileFact` row is never hard-deleted.
- id (UUID, stable — future `ResumeVersion`/`ApplicationKit`/agent output reference a fact by this
  id)
- user_id (FK -> User)
- career_profile_id (FK -> CareerProfile)
- fact_type (`experience` / `education` / `skill` / `certification` / `language` / `project` /
  `link` / `preference` / `work_authorization` — exactly these nine; `application_answer` is
  explicitly out of scope, deferred to a future `ApplicationAnswer` entity)
- value (JSONB; always a typed, bounded `FactValue` shape per `fact_type` — see
  `tapjob_api.career_profile.fact_values`, never `{type: string, value: Any}`)
- source_kind (server-assigned only — `user_direct` is the only value WP1.2 defines; a client can
  never choose its own provenance)
- source_ref (nullable, optional)
- evidence_ref (nullable; a reference only, never a dumping ground for full resume/chat text)
- confidence (nullable, `0..1`; meaningful for future machine-derived candidates, not for a
  user-direct confirmed fact)
- user_confirmed (server-assigned `true` for every WP1.2-created fact)
- confirmed_at
- status (`active` / `superseded` / `retracted`)
- lineage_id (equal to `id` at creation; copied onto whatever fact later supersedes this one — at
  most one `active` fact per `lineage_id`, enforced by a partial unique index)
- supersedes_fact_id (nullable, FK -> ProfileFact)
- active_from_profile_version / inactive_from_profile_version (nullable) — **system/version
  validity**: when this fact entered/left the current-truth set, in `profile_version` terms. Never
  confused with a fact's own **domain validity** (e.g. an experience's real 2022–2024 dates), which
  lives inside `value` for the fact types that have one.
- created_at

`get_truth_snapshot(user, profile_version?)` is the one read path any future AI/resume/matching/
WhatsApp consumer may ground on: owned, `user_confirmed`, active at the target version, never
retracted/superseded, never another user's fact. Supports both the current version and a historical
reconstruction via the `active_from`/`inactive_from_profile_version` window — no separate
event-sourcing framework.

**Optimistic concurrency:** every mutating request supplies `expectedProfileVersion`; the server
applies one atomic conditional `UPDATE career_profiles SET profile_version = profile_version + 1
WHERE user_id = :user AND profile_version = :expected RETURNING *` in the same transaction as the
mutation. Zero matched rows → `409 PROFILE_VERSION_CONFLICT` — never a silent last-write-wins, never
more than one version bump per logical mutation.

### Resume
Logical resume family.
- id
- user_id
- title
- is_master

### ResumeVersion
Immutable rendered/content snapshot.
- resume_id
- version
- target_job_id optional
- profile_version
- content_json
- generated_by
- created_at

### ResumeArtifact
- pdf/docx
- storage_key
- checksum
- mime
- size

### Company
Canonical employer.

### Job
Canonical normalized role.
- company_id
- title
- description_normalized
- location
- workplace_type
- employment_type
- salary
- published_at
- expires_at

### JobSource
One external representation.
- job_id
- provider
- external_id
- url
- raw_payload_ref
- fetched_at
- last_seen_at
- hash

### Match
- user_id
- job_id
- profile_version
- deterministic_score
- semantic_score
- llm_score optional
- final_score
- hard_blockers
- missing_requirements
- explanation
- model_version

### SavedJob
User intent around job.
- state: saved/dismissed
- reason optional

### Application
- user_id
- job_id
- state
- apply_mode
- provider
- provider_application_id
- submitted_at
- last_event_at
- source_of_truth

### ApplicationKit
Immutable package.
- application_id
- resume_version_id
- cover_letter_id
- answer_set_id
- match_id
- profile_version
- created_at

### ApplicationAnswer
- question_normalized
- answer
- fact_refs
- sensitivity
- user_confirmed

### ApplicationEvent
Append-only timeline.
- type
- source
- timestamp
- payload
- evidence_ref

### Interview
- application_id
- round/type
- scheduled_at
- timezone
- location/link
- status
- notes

### InterviewStory
STAR/STAR+R story grounded in ProfileFacts.

### Contact
Recruiter/hiring manager/reference.
- name
- organization
- email
- linkedin
- relation
- consent/source notes

### Task
- entity_type/id
- type
- due_at
- status
- priority

### Notification
- type
- channel
- scheduled_for
- delivered_at
- dedupe_key

### ExternalAccount
- provider
- scopes
- token_ref
- status
- expires_at
- last_sync_at

### InboxEvent
Normalized e-mail lifecycle event.
- provider_message_id
- application_id optional
- event_type
- company
- role
- event_date
- confidence
- evidence_excerpt
- processed_at

## 2. State machine

Application transitions:

```text
DISCOVERED
 -> SAVED
 -> PREPARING
 -> READY
 -> APPLIED
 -> ASSESSMENT
 -> INTERVIEW
 -> OFFER
 -> HIRED

Terminal/side:
REJECTED
WITHDRAWN
EXPIRED
```

Transitions from inbox are proposals unless confidence/policy allows automatic update. Ambiguous events require review.

## 3. Data classification

### Public/external
Job descriptions, company public data.

### Personal
Profile, CV, application history, contacts.

### Sensitive/high-risk
- CPF/document number;
- disability/health;
- demographic/EEO answers;
- criminal/legal declarations;
- work authorization/immigration status depending context;
- mailbox content;
- OAuth tokens.

Sensitive fields require explicit purposes and tighter access controls.

## 4. Provenance

Generated content must be traceable:
- source ProfileFact IDs;
- model/prompt version;
- generation timestamp;
- user edits.

## 5. Dedupe

Job dedupe inputs:
- provider IDs when same source;
- canonical company;
- normalized title;
- location;
- description fingerprint;
- apply URL;
- temporal window.

Never merge uncertain duplicates destructively.

## 6. Deletion

Account deletion must:
- revoke linked-account tokens;
- delete or anonymize user data;
- delete stored documents;
- preserve only legally required minimal audit records if necessary;
- record deletion completion without retaining deleted content.