# ADR-0010 — Career Profile & Professional Truth Store (WP1.2)

**Status:** Accepted
**Date:** 2026-10-05
**Decision:** D-052 (`docs/DECISIONS.md`)

## Context

WP1.1 (ADR-0009, D-051) delivered the first authorized product feature — identity, the canonical
`User`, and account lifecycle — and is VERIFIED (merged, post-merge CI green). WP1.2 — Career
Profile & Professional Truth Store — is the second authorized work package
(`docs/IMPLEMENTATION_PLAN.md`), and the first to touch the product's actual professional domain.
`docs/DATA_MODEL.md` already sketched `CareerProfile`/`ProfileFact` conceptually, and D-008 already
recorded the governing principle: *"Professional Truth Store is mandatory for generated career
artifacts."* This ADR turns that principle into a concrete schema, API, and set of invariants that
every future consumer — Resume Import review, the Matching Engine, Tailored Resumes, Application
Answers, the Career Agent, and a future WhatsApp channel — must rely on, never bypass.

WP1.3+ (Resume Import/parsing, Job domain, Matching Engine, Resume Studio, Career Agent, WhatsApp
adapter, full mobile profile UI, any AI/LLM provider call) are explicitly **not authorized** by this
ADR. This work package makes **zero** calls to `AiGateway` or any AI/LLM provider.

## A. The central principle — Truth Store, not arbitrary profile data

A `ProfileFact` is not a free-form JSON blob a client can shape however it likes; it is a
traceable, provenance-carrying professional claim. Any material a future feature generates on the
user's behalf (a tailored resume, an application answer, agent output) may only be grounded in
facts the Truth Store actually confirms — never in chat history or raw model output treated as
fact by itself (`docs/AI_AGENT.md` §6, `AGENTS.md` §6). This ADR's job is to make that boundary
structural, not a convention a future feature could quietly erode: `TruthSnapshot` (§F) is the one
shape any such consumer may read, and its default filter is the enforcement point.

## B. CareerProfile aggregate

### B1. One CareerProfile per User, lazily created

`career_profiles.user_id` is `UNIQUE`. A `User` may have zero or one `CareerProfile` row. `GET
/me/career-profile` never creates a row as a side effect — a `User` with none yet receives a
**virtual** response (`profileVersion: 0`, every field empty/null), not a 404 and not an implicit
write (no surprising side effect in a read path, the same discipline WP1.1's
`find_user_by_firebase_subject` already established). Only a mutation
(`PATCH /me/career-profile`, or the first `POST /me/profile-facts`) actually creates the row, via
an idempotent, concurrency-safe `_get_or_create_career_profile` — the exact SAVEPOINT pattern
`tapjob_api.identity.service.bootstrap_user` already established for the same class of first-write
race (caught by `IntegrityError` on the unique constraint, not by a prior `SELECT`).

A freshly created row starts at `profile_version = 0`, not `1` — identical to the virtual case, so
a client's mental model of "`expectedProfileVersion = 0` means nothing real exists yet" holds
whether or not a row happens to already exist. The first real content mutation is what bumps
`0 → 1`, never row creation by itself.

### B2. Profile-level fields vs. typed facts — no duplication

`headline`, `summary`, and `target_roles` live directly on `CareerProfile` (matching
`docs/DATA_MODEL.md`'s existing sketch); every other piece of professional truth —
experience, education, skills, certifications, links, languages, projects, preferences, work
authorization — is a `ProfileFact`. Nothing is represented in both places at once.

## C. ProfileFact — identity, immutability, lineage

### C1. Shape

`id` (UUID, stable — future `ResumeVersion`/`ApplicationKit`/agent output will reference a fact by
this id), `user_id`, `career_profile_id`, `fact_type`, `value` (JSONB, but never arbitrary — see
§D), `source_kind`, `source_ref` (optional), `evidence_ref` (optional, a reference only — never a
dumping ground for full resume text or a chat transcript), `confidence` (optional, `0..1`),
`user_confirmed`, `confirmed_at`, `status` (`active` / `superseded` / `retracted`), `lineage_id`,
`supersedes_fact_id` (optional), `active_from_profile_version`, `inactive_from_profile_version`
(optional), `created_at`.

### C2. A changed claim is never an in-place UPDATE

`ProfileFact.value` is never mutated after creation. Replacing "Senior Engineer at Acme" with
"Staff Engineer at Acme" does not alter the original fact — it creates a **new** fact row in the
same `lineage_id`, marks the old row `superseded`, and links `supersedes_fact_id` back to it. The
old row, with its original, un-mutated `value`, remains permanently readable by its own id
(`GET /me/profile-facts/{factId}` is deliberately reachable regardless of `status`). This is the
one deliberate point of divergence from the `career-ops-hq/career-ops` donor (§J): that project's
own `doImport()` merge step mutates a fact's text in place with no history — rejected here as
exactly the behavior WP1.2 must not have (`docs/SOURCE_REUSE_LEDGER.md`).

A partial unique index, `uq_profile_facts_active_lineage ON profile_facts (lineage_id) WHERE status
= 'active'`, is the real database-level invariant: at most one `active` fact per lineage, enforced
by PostgreSQL, not only by application logic. `replace_profile_fact` therefore marks the old row
`superseded` and flushes **before** inserting the new `active` row in the same transaction, or the
index would reject the insert.

### C3. Retraction never hard-deletes

`retract_profile_fact` sets `status = retracted`; the row stays in the database forever, for
auditability and historical reasoning (`docs/ENGINEERING_STANDARDS.md` §23: soft delete only when
product/legal behavior requires it — a Truth Store's own claims are exactly that case). The public
`POST /me/profile-facts/{factId}/retract` endpoint is a logical retraction by design, not an HTTP
`DELETE`, so this is unambiguous from the route shape itself rather than left to a comment on a
generic verb. `list_active_facts`/`GET /me/profile-facts` hide retracted and superseded facts by
default; they remain reachable one at a time by id.

### C4. Replacing or retracting a non-active fact is a conflict, not a silent no-op

Calling `replace`/`retract` on a fact that is already `superseded` or `retracted` raises
`ProfileFactNotActiveError`, surfaced as `PROFILE_FACT_ALREADY_RETRACTED` (409) — never a quiet
no-op and never a second supersession layered onto an already-inactive row.

## D. Typed fact values — no arbitrary JSON

### D1. Discriminated union, not `{type: string, value: Any}`

`tapjob_api.career_profile.fact_values.FactValue` is a Pydantic discriminated union (on `kind`)
over nine typed models: `ExperienceFactValue`, `EducationFactValue`, `SkillFactValue`,
`CertificationFactValue`, `LanguageFactValue`, `ProjectFactValue`, `LinkFactValue`,
`PreferenceFactValue`, `WorkAuthorizationFactValue`. Every shape has explicit bounds (string
lengths, list sizes, numeric ranges) — never unbounded nested JSON a payload-bomb could exploit
(§D7). `AddProfileFactRequest`/`ReplaceProfileFactRequest` additionally validate that the posted
`value.kind` matches the request's own declared `factType` (`replace` also validates it against the
**existing** fact's type) — a mismatch is `422`, never silently stored as an inconsistency.

### D2. `FactType` (exactly nine values, no more)

`experience`, `education`, `skill`, `certification`, `language`, `project`, `link`, `preference`,
`work_authorization` — the canonical set `docs/IMPLEMENTATION_PLAN.md` requires. Deliberately
excludes `application_answer`: that belongs to a future `ApplicationAnswer` entity, out of WP1.2's
scope.

### D3. No invented precision or inference

`YearMonth` (year required, month optional) is the only date precision `Experience`/`Education`/
`Certification`/`Project` ever store — a user who only knows "2024" never becomes "2024-03-01."
`Skill.proficiency`/`years_experience` are never inferred or defaulted; they exist only when a user
actually supplied them. `Language.proficiency` is free-form bounded text, never forced into a CEFR
scale the user never stated.

### D4. Skills are never semantically merged

`SkillFactValue._normalize_name` trims and collapses whitespace for duplicate-key comparison —
nothing more. "Java" and "JavaScript" remain, and always will remain, distinct skills; there is no
skill taxonomy/ontology in WP1.2.

### D5. Money is never a float

`SalaryPreferenceValue` uses integer minor units (`minor_unit_min`/`minor_unit_max`) plus a
separate ISO-4217 `currency` string — exactly `docs/ENGINEERING_STANDARDS.md` §11's documented
`amountMinor`/`currency` convention, never a float. No currency conversion happens anywhere in this
WP.

### D6. Links accept only safe schemes

`LinkFactValue.url`/`CertificationFactValue.credential_url`/`ProjectFactValue.url` are
`pydantic.HttpUrl`, which structurally rejects `javascript:`/`data:`/`file:` and every non-`http(s)`
scheme. No URL is ever fetched by this work package.

### D7. Bounds are named, not magic numbers

`fact_values.py` defines named bound constants (`_SHORT_TEXT`, `_LABEL_TEXT`, `_LONG_TEXT`,
`_BULLET_TEXT`, `_MAX_BULLETS`, `_MAX_TECHNOLOGIES`, `_MIN_YEAR`/`_MAX_YEAR`) applied consistently
across every fact type, per `docs/ENGINEERING_STANDARDS.md` §2.

### D8. Work authorization — sensitive, never inferred

`WorkAuthorizationFactValue` is its own `FactType`, distinct from `preference` (FASE 8's canonical
list names it separately, and it warrants separate auditability/logging treatment, §H). `answer` is
bounded text scoped by `jurisdiction`/`question_key` — never a bare, context-free boolean, and this
WP invents no global legal-question catalog (only the shape; the catalog is a future product
decision). The server never infers this value from nationality, location, language, resume content,
company, or any LLM output — it is only ever written through an explicit, confirmed user action,
structurally guaranteed by §E2 (the add-fact endpoint is the only way to create one, and it always
assigns `user_direct`/`confirmed = true`).

### D9. Preferences are intentions, not facts about reality

`PreferenceFactValue` wraps a second, nested discriminated union on `preference_kind` — `salary`,
`location`, `workplace_type`, `employment_type` — under the single `FactType.PREFERENCE` (the
canonical list names "preference" once, not once per kind). `target_roles`, the one preference-like
concept with no deeper substructure, stays at the `CareerProfile` level instead (§B2). Location
preferences store plain bounded text (country/region/city) plus workplace type; there is no
geocoding and no invented coordinates in WP1.2.

## E. Provenance, confirmation — no source spoofing

### E1. `source_kind` is always server-assigned

`AddProfileFactRequest` has no `sourceKind`/`sourceRef`/`confidence`/`userConfirmed` fields at all.
A hostile client that posts those keys anyway gets them silently dropped by Pydantic (no
`extra="forbid"` is needed because the fields simply do not exist on the schema); the server
unconditionally sets `source_kind = user_direct`, `user_confirmed = true`,
`confirmed_at = now()` for every fact created through this public endpoint — proven directly
(`test_client_cannot_spoof_source_kind_or_confirmation_metadata`). `FactSourceKind` currently has
exactly one member, `user_direct` — mirroring WP1.1's `IdentityProvider` precedent (only `firebase`
defined though `whatsapp` is expected later) — left open for `resume_import`,
`whatsapp_user_statement`, `agent_suggestion` to join later via internal, trusted use cases this WP
does not build, without a destructive migration.

### E2. Explicit user-direct save is the only kind of confirmation WP1.2 has

A write through `POST /me/profile-facts` **is** the field-level explicit user confirmation — there
is no separate unconfirmed-candidate staging mechanism in WP1.2 (deliberately: "não complique WP1.2
sem necessidade"). A future AI suggestion or resume-parser inference will require its own staging
state in WP1.3+, explicit and excluded from `TruthSnapshot` by construction, not retrofitted onto
this WP's `ProfileFact` rows.

## F. Truth snapshot

### F1. One function, current and historical

`get_truth_snapshot(session, user, profile_version=None)` is the single read path any future
consumer (resume generation, matching, application answers, the Career Agent, WhatsApp) may use to
ground generated material. Default filter: owned by the caller, `user_confirmed = true`, and
active at the target version — `active_from_profile_version <= target AND
(inactive_from_profile_version IS NULL OR inactive_from_profile_version > target)`. For the current
snapshot (`profile_version=None`, defaulting to the profile's real current version, or `0` if no
profile row exists yet) this is mathematically equivalent to "status = active"; an explicit
historical `profile_version` reconstructs what was true at that version using the same half-open
window, without a second event-sourcing framework. The `user_confirmed` filter is kept as explicit
defense-in-depth even though WP1.2 only ever produces confirmed facts — it guards against a future
bug that sets `status = active` on something that was never confirmed.

### F2. Never leaks another user's facts or a retracted/superseded fact

Every query in `get_truth_snapshot` is scoped to the caller's own `user_id`; retracted and
superseded facts fail the `active_from`/`inactive_from` window by construction and never appear.

## G. Optimistic concurrency

### G1. One atomic conditional UPDATE is the real guard

Every logical mutation — profile field change, add/replace/retract fact — goes through
`_bump_profile_version`: `UPDATE career_profiles SET profile_version = profile_version + 1 WHERE
user_id = :user AND profile_version = :expected RETURNING *`, in the same transaction as the
mutation itself. PostgreSQL's own row lock on the matched row is what actually serializes two
concurrent conflicting writes — the loser's `WHERE` clause simply no longer matches once the winner
commits. Zero matched rows raises `ProfileVersionConflictError`, surfaced as `409
PROFILE_VERSION_CONFLICT`. This is the exact pattern WP1.1's `cancel_deletion_request` fix (ADR-0009
round-2 finding 6b) already proved correct for the same class of race — never a plain
SELECT-then-write, which would race a concurrent mutation the same way that bug once did.

### G2. `expectedProfileVersion`, not If-Match/ETag

Every mutating request body carries an explicit `expectedProfileVersion` field (not an HTTP
conditional-request header) — one pattern, used uniformly across `PATCH /me/career-profile`,
`POST /me/profile-facts`, `.../replace`, `.../retract`. A conflict never auto-merges or
last-write-wins; the caller must refetch and retry explicitly.

### G3. Exactly one version bump per logical mutation

`update_career_profile`/`add_profile_fact`/`replace_profile_fact`/`retract_profile_fact` each call
`_bump_profile_version` exactly once. A no-op `PATCH` (an empty field set, only probing the
expected version) still goes through the same bump — there is no cheaper "just validate" path once
a mutation was intended, and this keeps "a version increment means something really changed"
true without a second, parallel no-op case to reason about.

### G4. First-write race

Two concurrent first mutations for the same user must never create two `CareerProfile` rows.
`UNIQUE(user_id)` is the real backstop (§B1); `_get_or_create_career_profile`'s SAVEPOINT/
`IntegrityError`-retry is the same pattern WP1.1's `bootstrap_user` already established. Verified
under real concurrent load against real PostgreSQL
(`test_concurrent_first_writes_for_the_same_user_create_exactly_one_profile_row`, 8 threads) and at
a non-zero version (`test_concurrent_conflicting_writes_at_a_non_zero_version_never_lose_an_update`).

## H. Audit, logging, and privacy

### H1. Audit reuses WP1.1's mechanism, does not duplicate it

WP1.2 adds four `AuditEventType` members (`PROFILE_UPDATED`, `PROFILE_FACT_ADDED`,
`PROFILE_FACT_REPLACED`, `PROFILE_FACT_RETRACTED`) to the existing, append-only
`AccountAuditEvent` table (ADR-0009 §C3) rather than building a second, near-identical audit
mechanism — a Truth Store mutation is an account-level event in exactly the sense that table already
models. Metadata is allowlisted and minimal: field names changed, `fact_type`, `fact_id`,
`supersedes_fact_id` where applicable — **never** `value`/headline/summary content, a salary figure,
or a work-authorization answer (`docs/SECURITY_PRIVACY.md` §10). Proven directly, including the
work-authorization case specifically
(`test_work_authorization_answer_is_never_recorded_in_audit_metadata`).

### H2. Audit is atomic with the mutation it describes, and never recorded on failure

The matching audit event is staged in the same session as the mutation, before the router's single
`session.commit()` — both land in the same transaction, the same discipline ADR-0009 §D3/§C3
established. A version-conflict or already-retracted failure raises before any audit event is
staged, so a rejected mutation is never audited as a success
(`test_a_version_conflict_on_add_fact_is_never_audited_as_a_success`,
`test_a_conflicting_patch_is_never_audited_as_a_success`,
`test_retracting_an_already_retracted_fact_is_never_audited_a_second_time`).

### H3. No fact content, salary detail, or work-authorization answer in logs

`ProfileFact.value` is never written to application logs in general. Logs may contain `user_id`,
`fact_id`, `fact_type`, `action`, `profile_version`, `correlation_id`, and outcome — never the
factual content itself.

## I. Ownership, rate limiting, channel-first design

### I1. `/me/...` routes, server-resolved identity only

Every WP1.2 route lives under `/api/v1/me/...`; no route accepts a caller-supplied `user_id`.
`find_owned_fact` scopes every fact lookup to `(fact_id, user_id)` together — a fact belonging to a
different user is indistinguishable from a nonexistent one, resolving to `404
PROFILE_FACT_NOT_FOUND` either way, never a distinguishing 403 that would confirm the id exists
(the same IDOR-safe convention ADR-0009 §A/device ownership already established). Proven directly
for read, replace, and retract
(`test_user_b_cannot_read_user_as_fact`/`..._replace_user_as_fact`/`..._retract_user_as_fact`), and
for the truth snapshot (`test_snapshot_never_leaks_another_users_facts`).

### I2. Rate limiting reuses WP1.1's limiter, no new framework

`tapjob_api.core.rate_limit.RedisFixedWindowRateLimiter` is reused as-is. Two new named policies
(`tapjob_api.career_profile.dependencies`): `PROFILE_MUTATION_RATE_LIMIT`
(`rl:me:career-profile`, 30/60s) and `PROFILE_FACT_MUTATION_RATE_LIMIT` (`rl:me:profile-facts`,
60/60s) — writes only; reads are unthrottled. Keyed by the internal `User` UUID, never a raw token
or client IP, matching ADR-0009 §G's convention.

### I3. Channel-first: HTTP is one adapter, not the business logic

`tapjob_api.career_profile.service` accepts no HTTP concepts — plain `session`, `user: User`, and
typed kwargs. `router.py` is the only HTTP-aware caller. A future `MessagingChannel`/WhatsApp
adapter calls the exact same functions
(`set_profile_summary`-equivalent via `update_career_profile`, `add_profile_fact`,
`replace_profile_fact`, `retract_profile_fact`, `get_truth_snapshot`) the HTTP router calls — never
a second copy of these use cases. Each is a small, atomic, incremental command (add one fact,
replace one fact, retract one fact) rather than one giant "save the whole profile" endpoint, so a
future WhatsApp command like "adicione Python às minhas skills" can map onto the same
`add_profile_fact` call a mobile client would make — the mapping/interpretation itself (and any AI
involved in producing it) is explicitly **not** built by this WP.

**Factual correction (round-1 independent review):** an earlier draft of this section called these
commands "idempotent-by-design." That was wrong and has been corrected above: repeating
`add_profile_fact` with identical input creates a NEW fact/lineage/version each time, by design --
every call is a new, independent assertion, not a replay-safe one. Genuine duplicate-submission
protection, if a future feature ever needs it, is a separate, explicitly named invariant, never
assumed from "atomic."

### I4. No generic JSON Patch

Profile updates are a typed `PATCH` body (`UpdateCareerProfileRequest`) with `model_fields_set`
distinguishing "field absent" (untouched) from "field explicitly `null`" (cleared) — not a generic
JSON Patch document, which would weaken per-field validation and ownership checks for the sake of
endpoint count.

## J. Source reuse

Full entries in `docs/SOURCE_REUSE_LEDGER.md`. Summary:

- **`career-ops-hq/career-ops`** (SHA `d6a6c12db6d579ae0f3e6875d247409f6b8c9806`,
  `career-profile.mjs`): **PORT** of the source-evidence/provenance concept
  (`ProfileFact.source_ref`/`evidence_ref`/`user_confirmed`/`confirmed_at`) and the discipline that
  parsed/claimed material is evidence, not instruction. Explicitly **not** ported: the donor's
  content-addressed `stableId(kind, value, source, parent)` fact identity (TapJob uses a plain
  UUID, stable by assignment, not by content hash) and, critically, its `doImport()` in-place
  mutation of a fact's own text on re-import — rejected as the opposite of WP1.2's immutability
  requirement (§C2), not something portable as-is. No filesystem storage, no Node runtime, and no
  donor schema were imported; TapJob PostgreSQL remains canonical.
- **`roysahar11/appliable`** (SHA `2a900e892420039d228ceeb63f03b0480eb72fe4`,
  `.claude/skills/customize-resume/SKILL.md`): **ADAPT** of the Source-of-Truth-Discipline concept
  (every claim traceable to an approved source; a binary approved/unusable gate; an explicit
  confirmation step before material becomes an approved source) into `get_truth_snapshot`'s default
  filter. Explicitly **not** adopted: the donor's directory-structure-as-source-of-truth
  architecture (its own `CLAUDE.md`: "no separate tracking spreadsheet") and its WhatsApp
  implementation — TapJob's PostgreSQL `ProfileFact` table is the canonical store, never the
  filesystem.

## K. Migration and existing data

Alembic revision `0003` (`20261005_0003_career_profile_truth_store.py`), chained off `0002`,
hand-written (not importing live ORM models, matching `0002`'s own convention and the caution ADR-
0009 §C1 already recorded). Creates `career_profiles`/`profile_facts`, the partial unique index on
`(lineage_id) WHERE status = 'active'`, and both `CHECK` constraints (version-ordering,
confidence-range). Safe for existing `User` rows: no `CareerProfile`/`ProfileFact` is backfilled or
invented for any existing user — every profile starts empty and is populated only by an explicit
future write. No real migration is executed against staging/production by this work package.

## Testing implications

- Backend unit (no DB): every typed `FactValue` model's validation rules, bounds, start<=end
  checks, money-as-integer-minor-units, link-scheme safety, no-invented-precision
  (`test_career_profile_fact_values.py`, 40 tests).
- Backend integration (real PostgreSQL/Redis): profile CRUD/virtual-profile/versioning/absent-vs-
  null semantics/bounds/audit (`test_career_profile_profile_integration.py`); fact lifecycle/
  immutability/lineage/supersession/retraction/ownership/provenance/bounds
  (`test_career_profile_facts_integration.py`); truth snapshot filtering and historical
  reconstruction (`test_career_profile_truth_snapshot_integration.py`); real concurrent writes —
  first-write race and non-zero-version conflict, both against real PostgreSQL with actual threads
  (`test_career_profile_concurrency_integration.py`); audit success/failure semantics and
  content-exclusion (`test_career_profile_audit_integration.py`).
- Contracts: `packages/contracts/openapi.json` regenerated and re-validated against
  `tapjob_api.http.contract_rules`; the TypeScript client regenerated; the facade
  (`packages/contracts/src/index.ts`) re-exports the new WP1.2 types/operations, never a direct
  `./generated/*` import.
- No mobile UI was built for this work package beyond what contract regeneration itself requires
  (§L).

## Relationship to other decisions

- Builds on D-008 ("Professional Truth Store is mandatory for generated career artifacts") and the
  existing conceptual sketch in `docs/DATA_MODEL.md` — neither is reopened, both are made concrete.
- Reuses, rather than duplicates, three WP1.1 foundations: the append-only `AccountAuditEvent`
  mechanism (§H1), `RedisFixedWindowRateLimiter` (§I2), and the SAVEPOINT first-write-race pattern
  from `bootstrap_user` (§B1/§G4) — none of WP1.1's own code is modified by this ADR.
- Does not touch ADR-0007/ADR-0008 (temporary security exceptions, D-047/D-049) or ADR-0006's
  deployment contract; this checkpoint performs no real deploy and no real migration.

## Consequences

- `services/api/src/tapjob_api/career_profile/` is a new, self-contained package: ORM models, typed
  fact-value schemas, HTTP DTOs, the channel-first service/use-case layer, FastAPI dependencies
  (rate-limit policies), and the router. `tapjob_api.identity.models.AuditEventType` gains four new
  members. `tapjob_api.http.errors.ErrorCode` gains three new members
  (`PROFILE_VERSION_CONFLICT`, `PROFILE_FACT_NOT_FOUND`, `PROFILE_FACT_ALREADY_RETRACTED`).
- WP1.2 does not, by itself, make itself VERIFIED — that requires independent review, CI green at
  the latest HEAD, merge, and post-merge CI green, the same pattern WP1.1 followed. WP1.3 and every
  downstream work package remain **not authorized** by this ADR.

## Addendum — round 2 independent re-review

A second independent re-review approved the overall architecture again and found a further layer
of blockers at the domain/channel-first boundary — the HTTP router had correctly enforced several
invariants that `service.py` itself did not, meaning a future non-HTTP channel calling `service.py`
directly would not have gotten the same safety. Resolved on this same PR/branch:

1. **The channel-first boundary still depended on the router.** `add_profile_fact`/
   `replace_profile_fact` took a plain `dict`/an already-resolved `ProfileFact`, trusting the
   router to have validated `value.kind`/ownership first. **Fixed:** both now take a typed
   `FactValue` and validate `value.kind` against the fact type THEMSELVES;
   `replace_profile_fact`/`retract_profile_fact` now take a bare `fact_id` and perform their own
   ownership-scoped lookup internally (`ProfileFactNotFoundError` if it does not resolve to a fact
   this `user` owns) — a caller can never pass in an incorrectly-resolved ORM object and have it
   trusted. `update_career_profile` replaced its open `fields: dict[str, object]` + `setattr`
   boundary with explicit typed parameters (`headline`/`summary`/`target_roles`) and a private
   `UNSET` sentinel distinguishing "absent" from "explicit null" — there is no keyword a caller
   could use to reach `profile_version`/`user_id`/`id`. A new direct-service test file
   (`test_career_profile_service_direct_integration.py`) calls `service.py` with no HTTP adapter at
   all and proves every one of these guarantees holds there too.
2. **`ProfileFactResponse.value` was `dict[str, object]`.** The generated TypeScript client
   produced `Record<string, unknown>` for the Truth Store's own primary read contract — every
   future consumer (Resume, Matching, Agent, WhatsApp, mobile) would have received untyped data on
   read even though writes were fully typed. **Fixed:** `value` is now the same `FactValue`
   discriminated union the request side uses; the router parses a fact's stored `value_json` dict
   through an explicit `TypeAdapter(FactValue)` before constructing the response, and a new
   model validator on `ProfileFactResponse` fails closed (raises, becoming an unhandled 500) if a
   stored `value.kind` ever disagrees with its own `fact_type` column — a corrupted/inconsistent
   DB row is never handed back as if it were valid Truth. The generated
   `packages/contracts/src/generated/types.gen.ts` now shows a real discriminated union for both
   `ProfileFactResponse.value` and (transitively) `TruthSnapshotResponse.facts`.
3. **`YearMonth`'s `ordinal()` invented a month for comparison.** `year*12 + (month or 1)` treated
   an unknown month as January, so e.g. `start=2024-12`/`end=2024` falsely registered as "start
   after end" even though the user never asserted a January end — only a year. **Fixed:**
   `ordinal()` removed entirely; `_check_start_before_end` now only rejects when the two dates are
   UNAMBIGUOUSLY out of order given the precision actually known (same year, both months known);
   an unknown month's ordering relative to a same-year date is left undecidable, never invalid and
   never assumed valid via a fabricated month.
4. **Structured preferences could represent "nothing."** `{"preferenceKind": "location"}` with
   every field empty, or `{"preferenceKind": "salary", "currency": "BRL", "period": "monthly"}`
   with no amount bound, expressed no actual preference. **Fixed:** `LocationPreferenceValue`
   requires at least one of country/region/city/`workplaceTypes`; `SalaryPreferenceValue` requires
   at least one of `minorUnitMin`/`minorUnitMax` — no product semantics for a "currency/period
   only" preference were invented to avoid this.
5. **Control-character rejection was silently bypassed by whitespace collapsing.** `SkillFactValue`
   and `target_roles` both collapsed internal whitespace via `" ".join(value.split())` BEFORE any
   control-character check ran — `.split()` treats a literal newline/tab as a word boundary, so
   `"Python\nDeveloper"` silently passed as `"Python Developer"` instead of being rejected.
   **Fixed:** a shared `normalize_label_collapsing_spaces` helper rejects control characters on the
   TRIMMED string FIRST, and only then collapses runs of regular ASCII spaces.
6. **Audit metadata had no version-transition information.** `profile_version_before`/
   `profile_version_after` are now recorded for every profile/fact mutation audit event (plus
   `source_kind` for fact mutations), alongside the existing `fact_type`/`fact_id`/
   `supersedes_fact_id`/`fields_changed` — still never the fact's own content, a salary figure, or
   a work-authorization answer.

**Additional review finding, resolved the same round:** a same-value `PATCH /me/career-profile`
(submitting exactly the values already current) still bumped `profile_version`, wrote a revision,
and produced a success audit event, even though professional truth had not actually changed —
contradicting the documented invariant that a version bump means truth changed. **Fixed:**
`update_career_profile` now uses `SELECT ... FOR UPDATE` (a deliberate, documented departure from
the atomic-conditional-UPDATE pattern every other mutation here uses) to read the current state,
compare it against the requested state within the SAME transaction, and skip the bump/revision/
audit entirely when nothing would actually change — while a genuinely stale `expected_version`
still conflicts even when the submitted values happen to match the current ones, since the version
check always runs before the content comparison, inside the same lock. Two concurrent requests for
the same profile now serialize on this row lock rather than racing a fail-fast conditional UPDATE
— a different latency characteristic under contention, not a different correctness guarantee.

**DB hardening:** `career_profile_revisions.profile_version >= 0` added as a `CHECK` constraint,
matching the one already on `career_profiles`. The "replacement of User A referencing User B's
fact" concern raised in this round is structurally covered by finding 1 above (the ownership-scoped
lookup inside `replace_profile_fact`/`retract_profile_fact` itself) — no separate graph-consistency
mechanism was added, since one `find_owned_fact` call already makes this impossible by
construction.

Round 1's fixes (historical top-level revision via `CareerProfileRevision`, future-`profileVersion`
rejection, the composite `(career_profile_id, user_id)` ownership FK, empty-PATCH rejection,
string non-blank/normalization, `targetRoles` dedupe, the currency/idempotency wording corrections)
are unchanged by this round and continue to pass their own tests.

## Addendum — round 3 independent re-review

A third independent review approved the architecture again and found two further objective
blockers plus one micro-correction, all resolved on this same PR/branch:

1. **The top-level `CareerProfile` command still depended on the HTTP DTO.** Round 2 moved
   `update_career_profile` off `setattr`/an open `dict`, but `headline`/`summary`/`target_roles`'s
   own validation (trim, non-blank, control-character rejection, bounds, `targetRoles` dedupe)
   still lived only in `router.py`'s `UpdateCareerProfileRequest` -- a future channel adapter
   calling `service.py` directly would have had to duplicate those rules itself, or could skip
   them entirely. **Fixed:** a new `tapjob_api.career_profile.commands.CareerProfilePatchCommand`
   (a validated Pydantic model, reusing the exact same `fact_values.py` normalization helpers)
   is what `service.update_career_profile` now REQUIRES as its `command` parameter --
   `UpdateCareerProfileRequest` is a thin HTTP-specific subclass adding only
   `expectedProfileVersion` and the `targetRoles` camelCase alias; the validators and the "at least
   one mutable field" rule are inherited, never duplicated. A new
   `test_career_profile_commands.py` (17 tests) proves every one of these rules holds by
   constructing `CareerProfilePatchCommand` directly, with zero HTTP/router involvement.
2. **A same-value patch against a NEVER-created `CareerProfile` still created one.** Round 2's
   same-value no-op fix called `_get_or_create_career_profile` unconditionally before deciding
   whether anything had actually changed -- so even a genuine no-op (`{"headline": null}` against
   a brand-new user, whose virtual state is already `headline: null`) materialized a real
   `career_profiles` row, a `career_profile_revisions` row, and a real `createdAt`, contradicting
   "`CareerProfile` is lazily created only on first REAL mutation." **Fixed:** `update_career_profile`
   now compares the command against the VIRTUAL default state (`headline`/`summary: null`,
   `target_roles: []`, `profile_version: 0`) using a lock-free, write-free tentative check FIRST;
   only a genuine content change, or an `expected_version` that itself disagrees with "nothing has
   ever been created," falls through to materializing/locking the real row and re-validating
   against its ACTUAL version (never trusting the pre-materialization assumption once a row might
   need to be written to -- a race the first draft of this very fix still had, caught and closed
   before this reached CI). A virtual no-op returns `profile: None` from `ProfileUpdateResult`,
   which `_profile_response(None)` already renders as the correct virtual response. New tests cover
   every required case: `headline`/`summary`/`targetRoles` null-or-empty against a brand-new user
   (0 rows, 0 revisions, 0 audits, `profileVersion: 0`, `createdAt: null`), a real mutation
   afterward still creating the row normally, a stale `expectedProfileVersion` against a
   never-created profile still conflicting (never silently accepted as a no-op), and a dedicated
   concurrency test racing several no-op attempts against one real first mutation for the same
   brand-new user -- proving the no-op path can never corrupt a concurrent real first-write (exactly
   one row, one revision, one audit event, regardless of how the race interleaves).

**Micro-correction:** `retract_profile_fact`'s audit metadata was missing `source_kind` (present on
`add`/`replace` since round 2) -- added for consistency; still never the fact's own content, a
salary figure, or a work-authorization answer.

**Latest HEAD:** see the PR's "Review round 3" section for the exact commit. **Validation:**
`ruff`/`mypy`/`pytest` clean; the full backend suite green (unit + real-PostgreSQL/Redis
integration, including the new concurrency and direct-service/command tests); `npm run check:js`/
`check:py` clean across the repo; migration unchanged this round (no schema change was needed);
security audit-gate 94/94 self-test, real gate PASS, both pre-existing exceptions unchanged, no
third HIGH/CRITICAL advisory.

## Addendum — round 4 (post-merge hotfix: `update_career_profile`'s stale identity map)

WP1.2 merged, then a real post-merge CI failure under genuine concurrent load on GitHub's hosted
runners (`test_concurrent_conflicting_writes_at_a_non_zero_version_never_lose_an_update`, 6 threads,
`expectedProfileVersion=1` against an already-existing profile) exposed a structural defect in the
exact row-locking mechanism round 2's addendum above introduced and described.

**Root cause.** `SELECT ... FOR UPDATE` genuinely serializes two concurrent Postgres transactions at
the SQL level -- that part of round 2's claim was correct. What round 2's wording did NOT
distinguish is a SEPARATE guarantee: whether the Python ORM object a caller reads back afterward is
refreshed from that newly-fetched row, or is the SAME already-identity-mapped object SQLAlchemy was
already holding for that primary key. By default it is the latter. The pre-hotfix
`update_career_profile` ran an un-locked `select(CareerProfile).where(CareerProfile.user_id == ...)`
immediately before its own lock query, in the common "profile already exists" path -- this
populated the session's identity map with a `CareerProfile` instance at the PRE-mutation
`profile_version`. The subsequent `SELECT ... FOR UPDATE` on the SAME primary key then waited
correctly at the SQL level, but by default returned that SAME, now-stale Python object rather than
one refreshed from the row it had just waited to read -- so a loser could wake up after the winner's
commit and still see the winner's OWN pre-commit `profile_version`, pass an `expected_version` check
it should have failed, and race the winner to write the identical new version -- surfaced as an
unhandled `psycopg.errors.UniqueViolation` on `uq_career_profile_revisions_version` (a real `500`)
instead of a clean `409 PROFILE_VERSION_CONFLICT`. **The two guarantees -- "Postgres serializes
concurrent writers" and "the ORM object in hand reflects the row's current state" -- are
independent; round 2's wording conflated them.**

**Fixed, structurally, two ways (not a caught-and-converted exception):**
1. The existence check ahead of the lock no longer loads a full `CareerProfile` entity --
   `select(CareerProfile.id)` resolves only the scalar primary key, so the common "profile already
   exists" path never populates the identity map before the lock query runs at all.
2. The lock query itself now passes `.execution_options(populate_existing=True)`, which forces
   SQLAlchemy to overwrite an already-identity-mapped object's attributes from the freshly fetched
   row -- an independent safety net covering the one remaining path where a full entity IS loaded
   unlocked first (`_get_or_create_career_profile`'s own necessarily-unlocked lookup, which the
   first-write race still requires), so the lock query is refresh-safe regardless of what loaded the
   row into the identity map beforehand.

`uq_career_profile_revisions_version` is unchanged -- it correctly did its job as the last-resort
backstop; the fix makes the application-level race that could reach it structurally impossible
instead of loosening or removing it. No `IntegrityError` is caught-and-converted anywhere; a stale
version is now rejected by the `expected_version` comparison itself, before any write is attempted,
so the unique constraint is never actually contended by two "winners" again.

A new deterministic (non-probabilistic, no thread timing) regression test
(`test_a_stale_identity_mapped_profile_is_refreshed_by_the_lock_query_before_the_version_check`,
`test_career_profile_concurrency_integration.py`) proves this directly with two real `Session`
objects against real PostgreSQL: session B loads a `CareerProfile` unlocked at version 1; session A
commits a real mutation taking the row to version 2; session B's own subsequent
`update_career_profile` call, called with its own stale `expected_version=1`, must now raise
`ProfileVersionConflictError` -- proving the lock query refreshed past session B's own
already-cached, stale object. This test fails deterministically against the pre-fix code, every
run, independent of thread scheduling. All three existing concurrency tests (first-write race,
non-zero-version race, virtual-no-op-vs-real-first-mutation race) are unchanged in thread count and
shape and continue to pass.

No migration change was needed -- the schema's `UNIQUE` constraint was already correct; this was
purely an application/ORM-layer defect.
