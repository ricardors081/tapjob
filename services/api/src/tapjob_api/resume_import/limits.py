"""Named, bounded resource limits for WP1.3 (docs/ENGINEERING_STANDARDS.md §2 "no magic values",
§26 "file/upload standards"). A malicious or pathological resume must never turn upload/parsing
into an unbounded RAM/CPU/storage operation (docs/adr/0011-resume-import.md "bounded resource
policy"). These are product/policy constants, not deployment-specific values, so they live here
rather than in `ApiSettings` -- same precedent as `career_profile.fact_values`' own `_SHORT_TEXT`
etc.
"""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB -- generous for a text resume, far below abuse range
MAX_ORIGINAL_FILENAME_CHARS = 255

MAX_PDF_PAGES = 30
# A single pathological PDF content stream can itself be a memory-exhaustion vector even within an
# otherwise-small file (pypdf's own documented warning, docs/adr/0011-...md "PDF memory defense");
# bounding the decompressed text taken from any ONE page keeps a single page from dominating.
MAX_PDF_PAGE_TEXT_CHARS = 50_000
# Round-1 independent review "Finding 6": a pre-`extract_text()` guard on a page's own DECODED
# content-stream size. 5 MiB is generous for any real résumé page (a page's drawing/text operators
# are typically a few KB even uncompressed) while still rejecting a pathological single page
# before `extract_text()`'s own more expensive font/layout-aware parsing ever runs on it. See
# `extraction.py`'s own docstring for the honest limitation this measurement has.
MAX_PDF_PAGE_CONTENT_STREAM_BYTES = 5 * 1024 * 1024

MAX_DOCX_ZIP_MEMBERS = 50
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB -- generous ceiling, not a bomb vector
# A legitimate DOCX (already-compressed XML/media) rarely exceeds ~20x; a zip-bomb-style archive
# achieves orders of magnitude more. This ratio is inspected from ZIP metadata alone, before any
# actual decompression (docs/adr/0011-...md "inspect ZIP metadata BEFORE expensive decompression").
MAX_DOCX_COMPRESSION_RATIO = 100

# Hard ceiling on extracted text handed to the parser, regardless of source format or how many
# pages/paragraphs it came from -- the deterministic parser's own cost is bounded by this alone.
MAX_EXTRACTED_TEXT_CHARS = 200_000

MAX_CANDIDATES_PER_IMPORT = 200
# Extracted resume text is highly personal data (docs/SECURITY_PRIVACY.md §10) -- a candidate's own
# `source_excerpt` is hard-truncated to this length server-side regardless of what the parser
# returns, never the full matching passage.
MAX_SOURCE_EXCERPT_CHARS = 300

# Worker lease/retry (round-1 independent review "Finding 2", docs/adr/0011-resume-import.md
# "worker idempotency"). `PROCESSING_LEASE_SECONDS` bounds how long a single claim is presumed
# alive before another delivery may reclaim it -- generous relative to the bounded extraction/
# parsing work this task actually does (a document capped at `MAX_UPLOAD_BYTES`), so a lease is
# never reclaimed out from under a worker that is still genuinely running. This is no longer just
# a hopeful assumption (round-2 independent review "Finding 1"/"Finding 3"): it is a PROVABLE
# invariant, because `RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS` below is strictly less than this
# value -- a genuinely still-running worker is hard-killed well before its own lease could ever
# expire out from under it.
PROCESSING_LEASE_SECONDS = 300
# A transient failure (e.g. a storage read error) releases the row for an immediate bounded retry
# rather than waiting out the full lease -- `MAX_PROCESSING_ATTEMPTS` is the hard ceiling across
# BOTH that path and lease-expiry reclaims, so a pathological document can never retry forever.
MAX_PROCESSING_ATTEMPTS = 3
# How long the Celery task itself waits before retrying after a transient failure -- short enough
# to recover quickly, long enough not to hot-loop against a dependency that is still down.
TRANSIENT_RETRY_COUNTDOWN_SECONDS = 30

# Round-2 independent review "Finding 1": a redelivery that finds the row under a STILL-ACTIVE
# lease must never retry on a FIXED countdown divorced from that lease's own remaining lifetime --
# a fixed 60s countdown against a 300s lease, bounded by the SAME `max_retries` budget as genuine
# processing attempts, could exhaust its retry budget entirely before the lease ever actually
# expires, leaving the row stuck `PROCESSING` forever. Fixed structurally: the Celery task computes
# its own countdown from the lease's real `processing_lease_expires_at` (see
# `tasks._lease_wait_retry_countdown`), plus this safety margin for clock skew/scheduling jitter,
# and uses a SEPARATE, larger retry budget (`LEASE_WAIT_MAX_RETRIES`) that is never shared with
# `MAX_PROCESSING_ATTEMPTS` -- merely waiting for someone else's active lease to resolve is not
# itself a processing attempt, and must not consume the same bounded budget one.
LEASE_WAIT_SAFETY_MARGIN_SECONDS = 10
LEASE_WAIT_MIN_COUNTDOWN_SECONDS = 5
# Finite, not infinite (the review's own explicit requirement) -- but generous enough that, given
# `PROCESSING_LEASE_SECONDS`/`RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS` above bound a genuine lease's
# real maximum lifetime, this budget is never exhausted by a legitimately still-running worker in
# practice; it exists purely as a hard backstop against a genuinely stuck/unreclaimable row.
LEASE_WAIT_MAX_RETRIES = 10

# Round-2 independent review "Finding 3" (docs/ENGINEERING_STANDARDS.md §24 "every async job
# should define... timeout"): a hard Celery execution bound on `resume_import.process`, so
# `PROCESSING_LEASE_SECONDS` above is a PROVABLE bound on a genuine worker's own maximum lifetime,
# never merely an assumption. `RESUME_PROCESS_SOFT_TIME_LIMIT_SECONDS` fires first, inside the
# task, as a normal Python exception (`celery.exceptions.SoftTimeLimitExceeded` -- confirmed, by
# direct inspection of this pinned Celery version, to subclass `Exception`, not `BaseException`
# alone) -- caught by `service.process_resume_import`'s own generic exception handler exactly like
# any other unexpected failure: the row is marked terminally `FAILED` (fenced by the SAME lease
# token that call already holds), then the exception is re-raised purely for observability. A
# bounded, resource-capped parsing job (10 MiB upload, 200-candidate cap) legitimately exceeding
# this is itself an anomalous, worth-surfacing condition, not a presumed-transient one -- so this
# is never automatically retried. `RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS` fires second: the OS
# process itself is killed (`SIGKILL`, via Celery/billiard), with no further in-process cleanup
# possible at all -- recovery then relies purely on the lease naturally expiring, which this
# constant's own margin below `PROCESSING_LEASE_SECONDS` guarantees will always eventually happen.
# The ordering `soft < hard < lease` is a real invariant (`test_resume_import_timeouts.py` asserts
# it statically), not merely documentation.
RESUME_PROCESS_SOFT_TIME_LIMIT_SECONDS = 120
RESUME_PROCESS_HARD_TIME_LIMIT_SECONDS = 180

# Durable dispatch recovery (round-1 independent review "Finding 3", restructured by round-2
# independent review "Finding 4"): the `ResumeImport` row's own durable `QUEUED` status -- written
# in the SAME transaction as its own creation -- IS the recorded intent to process; no separate
# outbox table is needed. A `QUEUED` import older than this threshold, still unclaimed, is presumed
# to have had its original (or most recent recovery) dispatch lost. Recovery is now SERVER-OWNED
# (`tasks.sweep_stale_resume_import_dispatches_task`, a self-rescheduling Celery task that scans
# for exactly this condition) -- never triggered as a side effect of a client's own `GET` poll,
# which previously made an ordinary read cause an operational side effect and gave an indefinitely
# stuck row no recovery path at all if the client simply never polled again. The worker's own
# idempotent claim (`service.claim_import_for_processing`) makes a duplicate dispatch always safe,
# so this is never itself a source of double-processing -- only ever a safety net for a lost
# message. Also doubles as this same sweep's own re-dispatch backoff interval (see
# `service.should_redispatch_processing`): a row that was already redispatched is not considered
# stale again until ANOTHER full threshold period has passed, so a persistent broker outage causes
# one redispatch attempt per threshold period, never a hot loop.
DISPATCH_RECOVERY_THRESHOLD_SECONDS = 60
# How often the server-owned sweep re-schedules its own next run (round-2 independent review
# "Finding 4") -- a self-rescheduling Celery task, not Celery beat/a new scheduler process: the
# SMALLEST correct mechanism that is still genuinely server-owned, bounded (never a hot loop, never
# unbounded polling), and introduces no new infrastructure/framework.
RESUME_IMPORT_SWEEP_INTERVAL_SECONDS = 30

# Outbound R2 client policy (round-2 independent review "Hardening 9",
# docs/ENGINEERING_STANDARDS.md §25 "every outbound client should define... connect/read timeout...
# bounded retries"): never the SDK's own invisible defaults.
R2_CONNECT_TIMEOUT_SECONDS = 5.0
R2_READ_TIMEOUT_SECONDS = 15.0
R2_MAX_RETRY_ATTEMPTS = 3
