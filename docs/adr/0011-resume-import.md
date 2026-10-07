# ADR-0011 — Resume Import (WP1.3)

**Status:** Accepted
**Date:** 2026-10-06
**Decision:** D-053 (`docs/DECISIONS.md`)

## Context

WP1.2 (ADR-0010, D-052) delivered the Career Profile & Professional Truth Store and is VERIFIED
(merged, post-merge CI green, including a structural concurrency hotfix — `docs/PROJECT_STATE.md`).
WP1.3 — Resume Import — is the third authorized work package (`docs/IMPLEMENTATION_PLAN.md`) and the
first to accept an untrusted binary document from a user: PDF/DOCX upload, text extraction,
structured candidate parsing, explicit user review, and — only on explicit finalization — confirmed
`ProfileFact`/`CareerProfile` mutations and a master `Resume`/`ResumeVersion`.

Canonical scope (`docs/IMPLEMENTATION_PLAN.md` WP1.3): PDF/DOCX upload; text extraction; structured
parsing; review before save; file storage; master resume creation. Explicitly OUT of scope: Resume
Studio UI, tailored/per-job resumes, ATS scoring, rendering templates, applications, jobs/matching,
WhatsApp adapter, Career Agent, browser extension, WP2.x.

## Core product invariant

Resume import must never directly convert machine extraction into confirmed truth. Canonical flow:

```text
upload -> validate -> private store -> extract text -> parse candidate structure
  -> READY_FOR_REVIEW -> user reviews -> explicit finalize
  -> confirmed Truth Store mutation -> master Resume/ResumeVersion
```

Before explicit finalize: zero new confirmed `ProfileFact`. Every parsed candidate is untrusted,
unconfirmed scratch data (`tapjob_api.resume_import.models.ResumeImportCandidate`), never a
`ProfileFact` — `service.finalize_resume_import` is the ONLY path that ever turns an accepted
candidate into one.

## Import lifecycle

`ImportStatus`: `QUEUED -> PROCESSING -> READY_FOR_REVIEW -> FINALIZED`, with `FAILED` reachable from
`PROCESSING`. `QUEUED -> PROCESSING` is claimed atomically by the worker (conditional
`UPDATE ... WHERE status = 'queued'`), so redelivery of the same Celery task is always a safe no-op.
`READY_FOR_REVIEW -> FINALIZED` is claimed the same way by `finalize_resume_import`'s own
`SELECT ... FOR UPDATE`, which is how two concurrent finalize calls resolve to exactly one real
write without a separate idempotency-key table. Failures carry a normalized `ImportFailureCode`
(`unsupported_document_type` / `document_too_large` / `malformed_document` / `encrypted_document` /
`text_not_extractable` / `internal_error`) — never a raw parser/library exception.

## File security / upload boundary

Only PDF and DOCX are accepted. `Content-Type`/filename extension are never trusted alone — every
accepted document is sniffed by its real binary signature/container structure first
(`tapjob_api.resume_import.file_validation`):

- PDF: scanned for the `%PDF-` signature within the first 1024 bytes (tolerating minor leading
  junk some generators prepend).
- DOCX: must be a well-formed ZIP container containing the required OPC members
  (`[Content_Types].xml`, `word/document.xml`, `_rels/.rels`), inspected via `zipfile.ZipInfo`
  metadata (member count, declared uncompressed size, compression ratio) **before** any member is
  actually decompressed — a zip-bomb-style archive is rejected on its own central-directory
  metadata alone, never by attempting to inflate it first.
- A legacy OLE/CFBF container signature (password-protected legacy Office format) is detected
  explicitly and reported as `EncryptedDocumentError`, not a confusing "unsupported type".
- The upload is streamed in bounded 64 KiB chunks (`router._read_bounded`), never
  `await file.read()` unbounded — the size limit is enforced before the full body is ever held in
  memory.
- `original_filename` is retained only as bounded (255-char) display metadata — it is **never**
  used to derive `storage_key` (`tapjob_api.resume_import.storage.new_storage_key` generates a
  fresh, random, owner-namespaced key).

## Bounded resource policy

Named, documented constants (`tapjob_api.resume_import.limits`), never scattered magic numbers:
`MAX_UPLOAD_BYTES` (10 MiB), `MAX_PDF_PAGES` (30), `MAX_PDF_PAGE_TEXT_CHARS` (50,000 — bounds a
single pathological page's contribution, per pypdf's own documented content-stream memory
warning), `MAX_DOCX_ZIP_MEMBERS` (50), `MAX_DOCX_UNCOMPRESSED_BYTES` (50 MiB),
`MAX_DOCX_COMPRESSION_RATIO` (100, a zip-bomb heuristic inspected from ZIP metadata alone),
`MAX_EXTRACTED_TEXT_CHARS` (200,000 — the hard ceiling the deterministic parser's own cost is
bounded by, regardless of source format), `MAX_CANDIDATES_PER_IMPORT` (200),
`MAX_SOURCE_EXCERPT_CHARS` (300 — a candidate's own source excerpt is hard-truncated server-side
regardless of what the parser returns). All of these are exercised by dedicated tests
(`test_resume_import_file_validation.py`, `test_resume_import_extraction.py`).

**Bound placement, not just bound existence:** a resource bound checked only AFTER the expensive
operation it is meant to guard provides no real protection, it only discards the result. Two real
gaps of exactly this kind were found and fixed before this PR's independent review: (1)
`extract_pdf_text`'s per-page character budget was checked after each page's own `extract_text()`
call, so once the overall text budget was already exhausted, every remaining page still ran its own
(potentially expensive) extraction before its output was discarded — fixed by checking the
remaining budget BEFORE each page's `extract_text()` call, so a budget-exhausted document skips
extracting the rest of its pages entirely, never merely discards their output. (2)
`MAX_CANDIDATES_PER_IMPORT` was previously enforced only on the already-validated candidate list in
`service.process_resume_import`, after `parse_resume_text` had already built its own full,
unbounded-in-practice intermediate list (an adversarial comma-separated `skills` section packed
with tens of thousands of short tokens could already make the parser itself materialize far more
candidates than any real résumé would) — fixed by bounding both `_parse_skills_section`'s own loop
and `parse_resume_text`'s cross-section running total directly at the source, never only at the
caller's persistence-time cap.

## PDF / DOCX extraction

`tapjob_api.resume_import.extraction` uses `pypdf` and `python-docx`. Never OCR: a scanned/
image-only PDF, or a DOCX with no meaningful text content, is detected via a conservative minimum-
meaningful-text-character heuristic and reported as `TextNotExtractableError` — never silently
presented as a successful (empty) extraction. An encrypted PDF is rejected outright; TapJob never
attempts any password, including a blank one, even though some encrypted PDFs can technically be
opened that way. DOCX extraction walks paragraphs **and** tables in document order (a résumé
frequently puts skills/dates in table layout) and is bounded by the same character ceiling as PDF
extraction.

## Resume parsing — deterministic, no real AI provider in this checkpoint

`tapjob_api.resume_import.parsing` is a deterministic, non-AI, heuristic text parser. **No
`AiGateway`-backed resume parser is implemented in WP1.3** — this is stated honestly, not implied:
`AiPurpose.RESUME_EXTRACTION` already exists on the canonical `AiGateway` port
(`docs/adr/0005-internal-ports-capability-model.md`), but no provider adapter exists for it yet, and
building one is explicitly out of this checkpoint's scope ("no real AI provider requirement"). The
deterministic parser makes the import flow genuinely functional today: section-heading recognition
(English + pt-BR aliases), block-based entry splitting, conservative date-range recognition (never
inventing a month when only a year is present, never inventing a date at all when none is
recognizable), bullet/achievement extraction, skill/language/certification/project/link extraction,
and an explicit headline/summary low-confidence top-level candidate. An unrecognized section is
simply skipped as ordinary body text of whatever section is open — never guessed into a new,
invented section. Nothing in this parser ever infers `target_roles`, salary/location preference, or
work authorization from surrounding resume text — those fact types never appear in this parser's
output, verified by a dedicated test.

A résumé's own text can contain hostile instructions ("ignore previous instructions", "call this
URL"). This is a non-issue for the deterministic parser specifically, since it never invokes an LLM
at all — injected text is simply inert data that, at most, ends up inside an already-bounded,
already-validated candidate field (e.g. a `summary` candidate). A **future** `AiGateway`-backed
parser would be required to wrap extracted text in `ports.ai_gateway.UntrustedContent` and validate
every provider response through `AiRequest.validate_output` before it can become a candidate — that
discipline is documented here as a requirement on any future implementation, not something this
checkpoint builds.

## ResumeParser port evolution

`tapjob_api.ports.resume_parser.ExtractedFact.value` was `Mapping[str, str | int | float | bool]` —
too shallow for several canonical `FactValue` shapes that nest structure (e.g.
`ExperienceFactValue.start`/`end` are `{"year": int, "month": int | None}` objects;
`achievements`/`technologies` are lists of strings). This checkpoint widens it to a bounded,
recursive `JsonValue = JsonScalar | Mapping[str, JsonValue] | Sequence[JsonValue]` type alias. The
port deliberately does **not** import (and is not coupled to) `tapjob_api.career_profile`'s own
typed `FactValue` union — `tapjob_api.resume_import.candidates.validate_candidate` is the one place
a raw `ExtractedFact` crosses into TapJob's canonical, typed vocabulary, by validating `fact.value`
against the matching Pydantic `FactValue` model (reusing the exact same `FACT_VALUE_MODELS` tuple
WP1.2 already maintains, never a second copy). A candidate that fails validation is simply
**dropped** — fail-closed, never persisted as if it were valid, and never allowed to abort the whole
import for one bad candidate. No bare `Any`, no unchecked cast.

## Candidate data / review

`ResumeImportCandidate` persists a reviewable, unconfirmed extraction result: `candidate_kind` (one
of the nine canonical `FactType` values, plus the two WP1.3-only top-level kinds `headline`/
`summary`), the already-validated/normalized `value`, `confidence`, a hard-truncated
`source_excerpt`, and a `review_state` (`pending` / `accepted` / `rejected`). `CandidateKind` is a
deliberately **separate** enum from `career_profile.models.FactType` — mixing "a kind of
professional claim" with "a kind of review candidate" would let a future caller accidentally
construct a `ProfileFact` with `fact_type="headline"`, which has no meaning in the Truth Store
schema.

## Career Profile top-level fields

A resume may surface a `headline`/`summary` candidate. These belong to `CareerProfile`'s own
top-level fields, never a `ProfileFact`. `target_roles` is **never** inferred from a resume's
(current or most recent) job title — target role is a career goal/preference the user states
explicitly, not automatically derivable from employment history.

## Contact information

The primary donor's own extraction schema (JobSync) includes a `contactInfo` block (name/email/
phone/address). TapJob has no canonical contact-profile model for imported resume contact data, and
this checkpoint deliberately does not invent one: contact-looking text is simply never extracted as
a structured candidate at all (the deterministic parser has no contact-field extraction path).
Firebase/external identity data is never overwritten from a resume under any circumstance.

## Sensitive / preference fields are never inferred

Salary, location preference, protected/demographic attributes, legal answers, and work-authorization
status are never inferred by this checkpoint's parser, verified by a dedicated golden-dataset test
asserting these fact types never appear in parser output regardless of resume content (a physical
address is not a location preference; a past salary figure is not a salary preference; nationality/
employer history is never used to infer work authorization). The `candidates` validation layer would
still accept a well-formed `work_authorization` candidate if one were ever produced (proving the
validator itself does not special-case the type away) — the actual safety guarantee is that the
deterministic parser never produces one, and any future parser must preserve that discipline or
require it to always surface as an explicit, UNCONFIRMED candidate requiring the user's own
affirmative acceptance (never auto-accepted).

## Provenance

WP1.2 defined only `FactSourceKind.USER_DIRECT`. This checkpoint adds `FactSourceKind.RESUME_IMPORT`
(migration `0004` extends the `profile_fact_source_kind` CHECK constraint). When an imported
candidate is explicitly accepted at finalization: `user_confirmed=True`, `confirmed_at` set,
`source_kind=RESUME_IMPORT`, `source_ref` identifies the resume import id, `evidence_ref` references
the originating candidate id (never raw extracted text), and `confidence` preserves the parser's own
machine-extraction confidence.

## Finalization

High-integrity, atomic. `finalize_resume_import` acquires `SELECT ... FOR UPDATE` on the
`resume_imports` row as its entire concurrency guard. Unlike the WP1.2 post-merge hotfix case
(ADR-0010 "Addendum — round 4": a `SELECT ... FOR UPDATE` query could return an already
identity-mapped, pre-mutation object because a PRIOR unlocked full-entity load of the same row had
already populated the session's identity map), this lock query is correctly refresh-safe by
construction here: it is always a brand-new `Session`'s **first** load of this particular
`ResumeImport` row — one session per HTTP request, and the HTTP handler never performs its own
unlocked lookup of the same row before calling into this function. The two bugs are structurally
different, not coincidentally both safe.

`decisions` must cover **every** `PENDING` candidate of the target import, no more and no fewer — no
acceptance by omission. An `edit_accept` decision's edited value is revalidated through the exact
same fail-closed `validate_candidate` path a freshly-parsed candidate went through, never a looser
second code path. One logical finalization is exactly one `profile_version` transition
(`career_profile_service.bump_profile_version`, generalized in this checkpoint to also accept
optional `headline`/`summary` so the version bump and the top-level field mutation are the single
same atomic `UPDATE ... RETURNING` statement — never N independent bumps for N accepted facts, and
never a bump followed by a separate top-level PATCH) and exactly one new `ResumeVersion`. A stale
`expectedProfileVersion` raises `ProfileVersionConflictError` (→ `409 PROFILE_VERSION_CONFLICT`)
before any candidate/fact/revision write happens — zero partial writes.

## Finalization idempotency

A second `finalize` call against an already-`FINALIZED` import observes that status, loads its own
previously-created `ResumeVersion`, and returns the identical result (`FinalizeResult.is_replay =
True`) without writing anything again — no second audit event, no duplicate `ProfileFact`/
`ResumeVersion`/`profile_version` bump. Proven both as a sequential retry
(`test_finalize_is_idempotent_on_retry`) and as a genuine 6-thread concurrent race against the exact
same import
(`test_concurrent_finalizations_of_the_same_import_produce_exactly_one_logical_finalization`) —
every caller observes the identical `resumeVersionId`, and the database shows exactly one `Resume`/
`ResumeVersion` row regardless of how many callers raced.

## Master resume creation

At most one MASTER resume per user — enforced by a real database invariant (`uq_resumes_user_id_
master`, a partial unique index on `resumes.user_id WHERE is_master`), not only application logic.
`ResumeVersion` is immutable: a new finalization always creates a new version row (`version` strictly
increasing per resume), never updates an old one. `target_job_id` is always `null` for a master-
resume-import version (per-job tailoring is out of this checkpoint's scope). `profile_version` on the
new `ResumeVersion` is the EXACT resulting confirmed profile version. `generated_by` identifies the
resume-import origin. No PDF/DOCX is rendered here — rendering is explicitly deferred to a future
Resume Studio/renderer work package (`docs/DATA_MODEL.md`'s `ResumeArtifact` concept remains
unimplemented by this checkpoint).

## Resume version grounding

Every career claim a master `ResumeVersion` carries traces back to confirmed `ProfileFact` ids via
`content_json`'s `fact_ids` list (`tapjob_api.resume_import.schemas.MasterResumeSnapshot`) — never a
copy of fact text divorced from its own provenance. A future Resume Tailoring/Application Kit
consumer reconstructs the full fact records by id rather than trusting any text copied into this
snapshot directly. `content_json` is a bounded, typed Pydantic model, not an arbitrary JSON dump.

## Object storage

A local, internal `Protocol` (`tapjob_api.resume_import.storage.ObjectStoragePort`) — deliberately
**not** an addition to the canonical seven WP0.2-B ports (`docs/adr/0005-...md`), since storage is
not one of the externally-pluggable provider capabilities that blueprint enumerates, and there is
exactly one production adapter (Cloudflare R2, `docs/ARCHITECTURE.md` §3). `put`/`get`/`delete` all
operate on an already-computed, random `storage_key`; the port never derives a key from a
user-supplied filename. The R2 adapter uses `boto3`'s S3-compatible client (`region_name="auto"`,
`signature_version="s3v4"`, R2's own documented requirements), server-side credentials only (never
`EXPO_PUBLIC_*`, never logged). No bucket is ever public. **No signed-URL/download endpoint is
implemented** — WP1.3's accepted API surface has no download requirement, so none was built;
revisit only if a future consumer genuinely needs it. When R2 settings are unset (local/CI/test), an
in-memory fake adapter is used instead — zero real network calls, zero real bucket mutations in any
test.

### Object storage failure semantics

If the database write that persists a `ResumeImport` row fails **after** the storage upload already
succeeded, `create_resume_import` issues a best-effort compensating `storage.delete()` before
re-raising — a DB failure must never silently leave an orphaned, unreferenced object in the bucket
forever. This is accepted as a best-effort mitigation, not an absolute guarantee: a process crash
between the compensating delete call and its completion could still leak the object. This residual
risk is documented, not pretended away; a future periodic orphan-sweep job is a reasonable follow-up
if this is ever observed in practice, not built speculatively here.

## Malware scanning — explicit limitation

`docs/SECURITY_PRIVACY.md` §12 calls for a malware-scanning strategy for uploads. **No malware
scanner is integrated in this checkpoint.** File-format/container validation (signature sniffing,
ZIP metadata bounds) is a structural-integrity check, not a malware scan, and is never represented as
one. This is a real, acknowledged security gap for this checkpoint, not a false "CLEAN" claim — see
`docs/SECURITY_PRIVACY.md`'s own WP1.3 addendum.

## Async processing / worker idempotency

Extraction/parsing runs in the existing Celery/Redis worker (`services/worker`) — WP1.3 is the first
checkpoint whose worker tasks need direct PostgreSQL access, via the same
`tapjob_api.db.session.create_session_factory` helper the API process already uses (no second
database-access pattern). The task is defined in `tapjob_api.resume_import.tasks` using
`celery.shared_task` (bound lazily to whichever `Celery` app is "current"), never forcing the HTTP
API process to own a Celery consumer — the API process only ever calls `.send_task(name, args=...)`
by task name string (`tapjob_api.core.tasks.create_task_producer`); it never imports or executes the
task itself, preserving the existing worker → api dependency direction
(`docs/adr/0006-worker-correlation-deployment-cd-contract.md`).

Retry-safety: `process_resume_import` claims the row (`QUEUED -> PROCESSING`) via the same atomic
conditional-`UPDATE` pattern `bump_profile_version` uses, before doing any slow work, and commits
that claim immediately — so Celery's own delivery-safety defaults (`task_acks_late`,
`task_reject_on_worker_lost`, inherited unchanged from WP0.1) redelivering the same task message is
always a safe no-op once a prior delivery already claimed or finished it (proven by
`test_worker_redelivery_of_an_already_processed_import_never_duplicates_candidates`). A known,
normalized `ResumeImportError` (bad document) is recorded as `FAILED` with its reason code, never
raised back to Celery. An unexpected exception is **also** recorded as `FAILED` first (so a retry is
a safe no-op, not a repeat attempt against the same pathological input) and then re-raised purely for
observability (Sentry/Celery logs) — it never leaves the row in an ambiguous, non-terminal state.

## Error taxonomy

Normalized `ImportFailureCode` (persisted, for the async processing outcome) and `ErrorCode`
additions (synchronous HTTP responses): `UNSUPPORTED_DOCUMENT_TYPE`, `DOCUMENT_TOO_LARGE`,
`MALFORMED_DOCUMENT`, `ENCRYPTED_DOCUMENT`, `TEXT_NOT_EXTRACTABLE`, `RESUME_IMPORT_NOT_FOUND`,
`RESUME_IMPORT_NOT_READY`. Candidate-decision validation failures (missing coverage, invalid edited
value) reuse the existing `VALIDATION_FAILED` code rather than adding two more narrow ones. No raw
parser/library exception ever reaches `router.py` or a client.

## Ownership / IDOR

Every route is `/me`-scoped; `find_owned_import` resolves ownership in one predicate
(`ResumeImport.id == import_id AND ResumeImport.user_id == user.id`) — a resume import belonging to
a different user is indistinguishable from a nonexistent one (`RESUME_IMPORT_NOT_FOUND`, never a
distinguishable forbidden response). Tested with two real users for read, review, and finalize.

## Rate limiting

Upload and finalize are the abuse-sensitive writes (10/5 min upload, 20/5 min finalize) — reads
(polling import status, fetching the master resume) are deliberately unthrottled, the same
discipline `career_profile.dependencies` already established. Redis outage follows the existing
fail-closed policy (`RedisFixedWindowRateLimiter`).

## Audit

Reuses WP1.1's append-only `AccountAuditEvent` — two new event types, `resume_import_created` and
`resume_import_finalized` — with allowlisted metadata only (import id, media type, byte size, resume
id/version, profile-version-after, accepted-fact-count). Never resume text, a candidate's own value,
salary, work-authorization answers, email/phone, a signed URL, or an object-storage credential.
Verified directly by an integration test asserting known résumé content strings are absent from a
real persisted audit event's metadata.

## Migration

`0004`, chained off `0003` (the current, and until this checkpoint only, head). Extends
`profile_facts`'s `profile_fact_source_kind` CHECK constraint to additionally allow
`'resume_import'`. Adds four new tables: `resumes`, `resume_versions`, `resume_imports`,
`resume_import_candidates` — every column/constraint/index name computed by hand to match
`tapjob_api.db.base.NAMING_CONVENTION` exactly, deliberately not importing the ORM models module
(the same standard Alembic caution every prior migration already documents). No existing migration
(`0001`–`0003`) was altered.

## Dependencies

`pypdf` (PDF text extraction), `python-docx` (DOCX text extraction), `boto3`/`botocore` (R2/S3-
compatible object storage client), `python-multipart` (FastAPI multipart/form-data upload support,
required by `UploadFile`). `celery[redis]` is now an explicit `services/api` dependency too (the API
process needs a Celery producer, not just the worker). `boto3`/`botocore`/`python-docx` ship without
inline type stubs — scoped `mypy` overrides added, each contained to the one module that imports
them, same precedent as the existing `firebase-admin` override.

## What this checkpoint does NOT do

Zero AI/LLM calls (no real `AiGateway` provider adapter exists for `RESUME_EXTRACTION` yet — the
parser is deterministic). Zero PDF/resume rendering (`ResumeArtifact` remains unimplemented). Zero
malware scanning (documented limitation, see above). Zero signed-URL/download endpoint. Zero Resume
Studio UI, ATS scoring, per-job tailoring, applications, jobs/matching, WhatsApp adapter, Career
Agent, or browser extension. Zero full mobile profile/import UI — this is a backend/Career-Core
capability only, reachable identically by a future WhatsApp or mobile channel through the exact same
`tapjob_api.resume_import.service` functions `router.py` calls (channel-first, no HTTP concepts leak
into the service layer). Zero real deploy, zero real R2 bucket mutation (every test uses the
in-memory fake adapter), zero real migration execution against a production database.

## Source reuse

See `docs/SOURCE_REUSE_LEDGER.md` for the full entries. Summary: `Gsync/jobsync`'s resume-import
system prompt and eval harness are **PORTED** (concepts/rules/eval-assertion shape rewritten into
Python/Pydantic — never the TypeScript/Zod code, never its `contactInfo`-bearing schema, which does
not map onto TapJob's canonical `FactValue` union). `AbhishekMandapmalvi/AutoApply`'s
`core/resume_parser.py` heading-synonym-map **concept** (not its markdown/regex machinery, which
operates on ATX-style Markdown headings, not plain text extracted from a real PDF/DOCX) is
**ADAPTED** into the deterministic parser's own `_SECTION_ALIASES` table.

## Addendum — round 1 independent review

A first independent review found real, objective gaps across architecture, concurrency, and
input-validation boundaries. All resolved on this same PR/branch.

1. **Storage was not genuinely shared across the API and worker processes.** `create_storage_
   adapter` fell back to `InMemoryObjectStorage` whenever R2 was unset, in EVERY environment --
   but the API and worker are always separate OS processes, so an object the API `put()`s would be
   genuinely unreadable by the worker's own separate in-memory fake. Tests hid this by reusing the
   SAME Python instance across both sides of the call. **Fixed:** staging/production now require
   complete R2 configuration and fail fast at startup (`ObjectStorageConfigError`) if it is absent
   or only partially set -- never a silent fallback; `infra/compose.deploy.yaml` now wires all four
   `R2_*` variables into both `api` and `worker` as required (`:?`) substitutions, verified by a new
   CI step. Local/development gets a genuinely shared durable adapter instead of a fake one:
   `LocalFileObjectStorage` over a Docker named volume (`resume-storage-data`) mounted at the same
   path by both `api` and `worker` in `infra/compose.yaml`. A new test constructs TWO independent
   `LocalFileObjectStorage` instances against the same directory and proves one's write is readable
   by the other -- the structural proof `InMemoryObjectStorage` could never provide.
2. **A worker that crashed after claiming a row left it stuck in `PROCESSING` forever.**
   `claim_import_for_processing` only matched `QUEUED`, so Celery redelivering the same task after
   a crash found the row already `PROCESSING`, treated it as a no-op, and never recovered it.
   **Fixed:** the claim is now a LEASE (`processing_lease_expires_at`), reclaimable once expired,
   bounded by `processing_attempts`/`MAX_PROCESSING_ATTEMPTS`. A storage failure is now classified
   TRANSIENT (`TransientProcessingError`, releases the row back to `QUEUED` for an immediate bounded
   retry) versus a document-content problem (terminal `FAILED`, unchanged). Redelivery while a
   lease is genuinely still active raises `ImportLeaseActiveError`, which the Celery task wrapper
   (now `bind=True`, `max_retries=MAX_PROCESSING_ATTEMPTS`) turns into an explicit, bounded retry --
   never silently swallowed as success. Six new deterministic tests
   (`test_resume_import_worker_lease.py`) cover active-lease rejection, expired-lease reclaim,
   transient-failure release-and-retry, bounded exhaustion into terminal failure, and
   no-duplicate-candidates-after-recovery.

   **Follow-up finding, same round (a second automated review, after the lease mechanism above was
   already in place): the lease had no FENCING.** The lease prevents a second worker from
   CONCURRENTLY claiming the same row, but nothing stopped a FIRST worker -- whose lease had
   already expired and been reclaimed by someone else, while it was still slowly (not dead)
   running its own extraction/parsing -- from reaching its own final write afterward and
   unconditionally overwriting whatever the reclaiming attempt had already done, including
   inserting a second, duplicate set of candidates. **Fixed:** the exact `processing_attempts`
   value a claim is granted is captured once, as a plain Python int, immediately after claiming
   (never re-read from the ORM object later, which SQLAlchemy would silently refresh to a NEWER
   value after any commit) -- this is the fencing TOKEN. Every later state-transition write this
   invocation makes (`READY_FOR_REVIEW`, `_fail_import`, `_release_for_retry`) is now conditioned
   on `processing_attempts` STILL equalling that token; if the conditional `UPDATE` matches zero
   rows, this invocation's lease was invalidated by a reclaim, and it rolls back everything it was
   about to commit (including any candidates already `session.add()`-ed) instead of writing.
3. **A broker failure right after `session.commit()` could lose the dispatch.** `create_resume_
   import`'s row is durable at `QUEUED` the moment it commits -- that row IS the recorded intent to
   process, no separate outbox table needed -- but nothing ever re-published a lost `send_task`
   message. **Fixed:** `send_task` failures are now logged and swallowed (never an unhandled 500 for
   a row that still durably exists); `service.should_redispatch_processing` judges a `QUEUED` import
   stale once it has sat unclaimed past `DISPATCH_RECOVERY_THRESHOLD_SECONDS`, and `router.
   get_resume_import`'s own poll re-enqueues it -- recovery piggybacks on ordinary client polling,
   never a new internal polling loop. The worker's own idempotent claim makes any eventual duplicate
   dispatch always safe. Three new tests (`test_resume_import_dispatch_recovery.py`) prove the
   upload-time failure is swallowed, a stale import is redispatched on the next poll, and a fresh
   import is never redispatched prematurely.
4. **A duplicate `candidateId` across finalize decisions could desynchronize review state from
   Truth Store writes.** `{decision.candidate_id for decision in command.decisions}` silently
   collapsed duplicates, so `[ACCEPT, REJECT]` for the same id could pass the coverage check while
   an earlier `ACCEPT` had already queued a `ProfileFact`. **Fixed:** `FinalizeResumeImportCommand`
   itself now rejects any repeated `candidateId` (and bounds `decisions` at
   `MAX_CANDIDATES_PER_IMPORT`) at the command boundary -- structurally unreachable by `service.py`,
   by construction, for every channel.
5. **The candidate wire contract was untyped.** `ResumeImportCandidateResponse.value` was `dict[str,
   Any]`. **Fixed:** a `CandidateValue` discriminated union (the nine `FactValue` kinds plus
   `HeadlineCandidateValue`/`SummaryCandidateValue`) replaces it; the generated TypeScript client now
   carries a real union, never `Record<string, unknown>`. Top-level text candidates now embed their
   own `kind` in `value_json`, the same self-describing discipline every other candidate kind
   already had.
6. **The PDF per-page text bound was checked only after `extract_text()` already ran.** Investigated
   against the pinned pypdf version (6.19.0): a compressed content stream's length is not exposed
   without resolving the stream, so a true pre-decompression guard is not available through the
   public API. **Fixed, with the limitation stated honestly:** `_page_content_stream_size` measures
   the page's DECODED content-stream size immediately before deciding whether to call
   `extract_text()` at all, rejecting a pathological single page before `extract_text()`'s own more
   expensive font/layout-aware parsing ever runs on it. This does not close a decompression-bomb
   risk during the measurement itself -- closing that fully would mean parsing the raw PDF
   xref/object table by hand, an explicit non-goal for this checkpoint -- but the blast radius stays
   bounded by `MAX_UPLOAD_BYTES` (10 MiB) and PDF's single-level (not recursively-nested)
   compression. A monkeypatch-instrumented test proves `extract_text` is never called once the
   bound is exceeded.
7. **The extension allowlist could be bypassed by a matching signature alone.** A `.txt`-named
   upload whose body happened to be a real PDF was accepted via a signature-only fallback path.
   **Fixed:** the filename extension is now a hard, independent gate (`.pdf`/`.docx` only);
   `Content-Type` is purely advisory and can never promote OR demote acceptance. Four new tests
   cover the bypass, both cross-type filename/body mismatches, and that a generic Content-Type still
   works when extension and body genuinely agree.
8. **A `READY_FOR_REVIEW` import could have zero candidates, permanently unfinalizable.** Text
   extraction succeeding with zero recognized candidates previously still reached
   `READY_FOR_REVIEW`, but `FinalizeResumeImportCommand.decisions` requires `min_length=1` --
   unfinalizable by construction. **Fixed:** `NoReviewableCandidatesError` -> terminal `FAILED` with
   a new `NO_REVIEWABLE_CANDIDATES` code, never `INTERNAL_ERROR` for a known domain outcome.
9. **`resume_import_candidates` had two independent ownership FKs.** Same class of gap WP1.2's own
   round-1 review found for `ProfileFact`: nothing in the schema rejected a candidate whose
   `user_id` belongs to one user but whose `resume_import_id` points at a different user's import.
   **Fixed:** a composite FK `(resume_import_id, user_id) -> resume_imports(id, user_id)`, backed by
   a new `uq_resume_imports_id_user_id` constraint, proven by two real-Postgres tests (a matching
   pair is accepted, a mismatched pair is rejected by Postgres itself).
10. **Only the skills parser capped itself at the source.** The other six section parsers
    (experience, education, languages, certifications, projects, links) could each still
    materialize up to their own section's full token/block count before the cross-section budget
    ever truncated the result. **Fixed:** every section-producing function now also caps itself at
    `MAX_CANDIDATES_PER_IMPORT` directly inside its own loop. Five new adversarial tests (tens of
    thousands of links/language-lines/education-blocks/project-blocks/experience-blocks each) prove
    none of them can be made to produce more than the cap.

Migration `0004` was revised in place (not yet merged, same PR) to add the two new
`resume_imports` columns (`processing_lease_expires_at`, `processing_attempts`), the new
`no_reviewable_candidates` failure code, and the composite FK -- no `0005` was needed.

## Addendum -- round 2 independent review

A second independent review found 7 further findings and 2 hardening items after round 1's own
fixes had already landed (CI green at that HEAD). All fixed on the same branch/PR, same migration
`0004` revised in place again.

1. **The active-lease retry countdown was a fixed guess, divorced from the lease's own real
   lifetime.** A fixed 60s countdown, bounded by the SAME retry budget as genuine processing
   attempts, could in principle exhaust that budget before a 300s lease ever actually expired,
   leaving a row stuck `PROCESSING` forever. **Fixed structurally:** `ImportLeaseActiveError` now
   carries the lease's own real `processing_lease_expires_at` as `retry_at`; the Celery task
   wrapper (`tasks.lease_wait_retry_countdown`) computes its countdown from that real value plus a
   safety margin, and uses a SEPARATE, larger retry budget (`LEASE_WAIT_MAX_RETRIES=10`) that this
   one call explicitly overrides -- waiting for someone else's active lease to resolve is not
   itself a processing attempt and must never consume the same bounded budget one does. Also
   closed the loop structurally: `RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS` (Finding 3, below) is now
   strictly less than `PROCESSING_LEASE_SECONDS`, so a genuine worker is PROVABLY killed before its
   own lease could ever expire out from under it -- not merely assumed generous. Tests exercise the
   TASK WRAPPER itself (`test_resume_import_task_wrapper.py`), not only the service layer, including
   the exact scenario asked for: worker A claims and crashes, immediate redelivery schedules a
   retry past the real lease expiry, and a later attempt recovers without the row ever remaining
   `PROCESSING`.
2. **Lease fencing relied on `processing_attempts`, an attempt NUMBER, not a claim's real OWNER.**
   A duplicate delivery observing `processing_attempts` already at its bound could mark a row
   `FAILED` even while a genuinely different, currently active attempt still held a valid lease on
   it. **Fixed structurally:** a fresh, random `processing_lease_token` (UUID) is generated on
   EVERY successful claim; every subsequent transition out of `PROCESSING`
   (`service._owner_transition`) now requires, atomically, `status == PROCESSING AND
   processing_lease_token == <the exact token this claim generated>` -- never only an
   attempt-count coincidence. `process_resume_import` also now distinguishes "no active owner,
   attempts exhausted" (fails terminally) from "a different owner is ACTIVELY holding a valid
   lease right now" (always retried, regardless of the attempt budget) by checking the lease's
   real expiry, not just the attempt count. Six tests cover: a duplicate delivery against an
   attempt-exhausted-but-actively-leased row never fails it; a stale token is rejected for all
   three possible transitions (FAILED/QUEUED/READY_FOR_REVIEW); the current, genuinely-owning token
   is always accepted; and exhausted-attempts-with-no-active-owner fails terminally exactly once,
   safely, even under a second duplicate delivery.
3. **No hard execution timeout on the worker task.** `docs/ENGINEERING_STANDARDS.md` §24 requires
   a timeout for every async job; without one, "a genuine worker will never still be running when
   the lease expires" was an assumption, not a guarantee. **Fixed:** named
   `RESUME_PROCESS_SOFT_TIME_LIMIT_SECONDS=120`/`RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS=180`,
   satisfying `soft < hard < lease(300)` as a real, asserted invariant. A soft-limit firing
   (`celery.exceptions.SoftTimeLimitExceeded`, confirmed by direct inspection to be a plain
   `Exception` subclass in this pinned Celery version, not `BaseException`-only) is already caught
   by `process_resume_import`'s own generic exception handler -- terminal `FAILED`, correctly
   lease-fenced, re-raised for observability, never automatically retried (a bounded,
   resource-capped parsing job timing out is itself an anomalous condition). A hard-limit firing
   kills the process outright with no in-process cleanup possible; recovery relies purely on the
   lease naturally expiring, which the `hard < lease` invariant guarantees will always eventually
   happen -- and the round-2 sweep (Finding 4) is also a backstop for the case where the task's own
   retry chain gave up before ever reclaiming the row.
4. **Dispatch recovery still depended on the client's own `GET` poll.** A user who uploaded, got a
   202, and never polled again could leave a broker-outage-lost dispatch stuck forever; worse,
   every `GET` of a stale import re-dispatched with no persisted throttle, making an ordinary read
   cause broker traffic. **Fixed with the smallest correct mechanism, not a new queue framework:** a
   SELF-RESCHEDULING Celery task (`tasks.sweep_stale_resume_import_dispatches_task`), kicked off
   once per worker process by Celery's own `worker_ready` signal, that reschedules its own next run
   via `self.apply_async(countdown=...)` -- never Celery beat, never a new scheduler process.
   `service.sweep_stale_queued_imports` owns the actual DB query (no Celery dependency at all) and
   stamps `last_dispatch_attempt_at` so the SAME row is never re-selected again before another full
   backoff period passes -- a persistent outage causes one redispatch attempt per period, never a
   hot loop. Also catches a `PROCESSING` row whose lease has already expired, not only `QUEUED`
   rows, as the backstop for Finding 3's own residual edge case. `router.get_resume_import` is now
   a pure read with zero dispatch side effect. Tests prove: zero client `GET` is ever required for
   recovery; the sweep's own backoff bookkeeping prevents re-selecting the same row immediately;
   and the sweep TASK itself reschedules exactly once per invocation, never recursively.
5. **`create_storage_adapter` still implicitly returned `InMemoryObjectStorage` in
   `local`/`development` with no shared directory configured** -- reopening the exact cross-process
   bug Finding 1 (round 1) already fixed for staging/production, for a real local/development
   deployment missing its own compose-provided env var. **Fixed:** that branch now raises
   `ObjectStorageConfigError` instead. `InMemoryObjectStorage` is only ever constructed directly now
   -- `tapjob_api.main.create_app` gained an explicit `resume_storage_override` parameter, and every
   test fixture that needs the fake now passes it there, never relying on a runtime fallback.
6. **A DB failure after a durable storage write was only compensated for the FIRST flush**, not
   the full upload transaction (the audit-event write, or the final commit itself). **Fixed:** the
   router now wraps the audit-write-through-commit sequence in its own compensating-delete
   boundary too, capturing `storage_key` as a plain value BEFORE any rollback (the exact same
   expire-on-rollback hazard the lease-fencing token capture already had to work around).
   `LocalFileObjectStorage.delete` now also normalizes a raw `OSError` to `ObjectStorageError`, the
   same boundary `put`/`get` already have. Five tests cover the initial flush failing, the
   audit/commit failing, a failed compensating delete never masking the original failure (while
   still being observable/logged), and the happy path leaving the object in place.
7. **Migration `0004`'s downgrade was not data-safe.** A real, finalized import leaves
   `profile_facts` rows with `source_kind = 'resume_import'`; the downgrade unconditionally
   re-created a narrower CHECK constraint allowing only `'user_direct'`, which would either fail
   against real data or (worse, if ever "fixed" carelessly) falsify provenance. **Fixed:** the
   downgrade now counts `resume_import`-sourced facts first and fails fast with a clear,
   actionable `RuntimeError` if any exist -- never silently falsifying provenance, never silently
   deleting data; that remains an explicit, separately-approved operational decision. A new
   migration integration test inserts a real such fact, proves the downgrade fails, then restores
   the database to `head` and cleans up regardless of outcome.
8. **(Hardening) Section parsers capped themselves against the full `MAX_CANDIDATES_PER_IMPORT`
   constant, not the caller's own REMAINING budget.** With a small `max_facts` (or most of the
   budget already consumed by an earlier section), a later section's own parser could still
   materialize far more candidates internally than it had any use for, before `_add` truncated the
   result afterward. **Fixed:** every section-producing function now takes an explicit
   `remaining_budget` parameter, computed fresh immediately before each call; `summary`/`headline`
   now also respect `max_facts` correctly, including `max_facts=0`. White-box tests call each
   private parser function directly with a small `remaining_budget` over an adversarially huge
   section, proving the INTERMEDIATE construction itself is capped -- never merely that the final
   return value was sliced. Additional tests cover `max_facts=1,2,3` end to end.
9. **(Hardening) The R2 client had no named connect/read timeout or bounded retry policy**, and
   processing never verified a retrieved object's integrity against what was actually uploaded.
   **Fixed:** `R2ObjectStorage`'s `boto3` client now sets `connect_timeout`/`read_timeout` and a
   `mode="standard"` bounded-retry policy (named constants, never invisible SDK defaults,
   docs/ENGINEERING_STANDARDS.md §25). `process_resume_import` now verifies a retrieved object's
   `byte_size`/`checksum_sha256` against what was recorded at upload time; a mismatch (at least as
   plausibly a transient truncated read as genuine tampering) is treated as
   `TransientProcessingError`, reusing the EXACT same bounded-retry machinery a flaky storage
   backend already has -- a persistent mismatch still converges to terminal `FAILED`, never a
   second, parallel retry mechanism.

**Data retention / deletion ownership (explicit pre-production gate, not yet closed):** WP1.3 now
persists the raw, highly personal uploaded PDF/DOCX indefinitely (R2 in staging/production, a
shared local volume in development). WP1.1's account-deletion flow is currently a request/query/
cancel skeleton only -- it does not yet reach into resume-import storage at all. Before real user
data can flow through this path in production: (a) a concrete retention/lifecycle policy for the
source blob must be defined and approved; (b) the account-deletion flow must be extended to delete
or supersede it; (c) an orphan-reconciliation story (objects with no corresponding `ResumeImport`
row, e.g. from the residual best-effort-compensation gap documented in Finding 6 above) must exist.
None of this is implemented by WP1.3 -- marked here as an explicit, open gate, owner: the team
implementing real account deletion, removal condition: before this flow is exposed to real user
data in production.

Migration `0004` was revised in place again (not yet merged) for round 2's own additions
(`processing_lease_token`, `last_dispatch_attempt_at`, the data-safe downgrade precondition) --
still no `0005`.
