# ADR-0004 — HTTP error envelope, pagination, correlation and idempotency conventions (WP0.2-A)

**Status:** Accepted (merge of the WP0.2-A pull request is the acceptance gate)
**Date:** 2026-10-01
**Decision register:** D-046
**Scope:** cross-cutting HTTP conventions WP0.2-A was chartered to decide (`docs/IMPLEMENTATION_PLAN.md` WP0.2). No Accepted decision (D-001…D-045) is changed, and no product/business endpoint is added by this checkpoint. OpenTelemetry remains deliberately deferred (`docs/IMPLEMENTATION_PLAN.md` WP0.2 "Decide" list; Sentry is already the accepted observability tool, D-028).

## Context

`docs/ENGINEERING_STANDARDS.md` §8 sketched an error envelope direction without a canonical implementation. Nothing yet defined pagination, request correlation beyond the existing `X-Request-ID` header, or an idempotency-key convention. These all need to exist before the first `/api/v1` business route (jobs feed, applications, agent actions) so every future endpoint shares one contract instead of inventing its own per PR (`docs/ENGINEERING_STANDARDS.md` §1).

## Decisions

### 1. `ErrorResponse`: one envelope for every non-2xx response

```json
{
  "code": "VALIDATION_FAILED",
  "message": "Os dados enviados são inválidos.",
  "requestId": "3f2c...",
  "errors": [{ "location": ["query", "name"], "code": "missing" }],
  "details": { }
}
```

- `code` (`tapjob_api.http.errors.ErrorCode`, a `StrEnum`) is the only field business/UI logic may branch on. `message` is safe to display but is never a logic input, and the backend does **not** localize it by `Accept-Language` at this stage — mobile/web localize their own UI from `code` (WhatsApp, being server-rendered, localizes server-side from the same `code`). This was the one point flagged as a real ambiguity in the pre-implementation analysis; resolved this way because every current client (mobile, future web, the WhatsApp adapter) already owns its own i18n layer (`docs/ENGINEERING_STANDARDS.md` §9), and a second, server-side localization path for just this one field would be a second source of truth for no behavioral gain yet.
- `requestId` is always populated from the same context the `X-Request-ID` response header carries (item 2) — the two must always agree, and `test_errors.py` asserts this.
- `errors[]` (422 only) carries Pydantic's normalized `type` as `code` and `loc` as `location` — **never** Pydantic's raw `input`/`ctx`/`msg`, which can echo submitted data (including personal data) back into the response body.
- `details` is an opaque, per-code-typed object, omitted (not `null`) when absent.
- Deliberately **not** in the envelope: a redundant `status` field (already the HTTP status) or a full RFC 9457 `type`/`title` `problem+json` shape — the simpler, already-documented shape in `docs/ENGINEERING_STANDARDS.md` §8 covers every case this checkpoint needs.

`ErrorCode` today: `INVALID_REQUEST`, `VALIDATION_FAILED`, `AUTHENTICATION_REQUIRED`, `PERMISSION_DENIED`, `RESOURCE_NOT_FOUND`, `METHOD_NOT_ALLOWED`, `CONFLICT`, `RATE_LIMITED`, `SERVICE_UNAVAILABLE`, `UPSTREAM_FAILED`, `UPSTREAM_TIMEOUT`, `INVALID_CURSOR`, `INTERNAL_ERROR`. Every member is reachable today (not speculative): the first eleven and the last one via `tapjob_api.http.errors`' status→code default table (exercised by raising the matching `HTTPException`/`ApiError`, or by Starlette's own 404/405 defaults), `INVALID_CURSOR` via `tapjob_api.http.pagination.decode_cursor` (item 3). `IDEMPOTENCY_KEY_REUSED`/`IDEMPOTENCY_IN_PROGRESS` (item 4) are **not** added to the enum yet, precisely because nothing emits them this checkpoint — they arrive with the idempotency middleware itself, in whichever work package first needs it, to avoid an enum member no code path can produce.

### 2. Exception handling: `register_exception_handlers` plus a correction to `RequestContextMiddleware`

`tapjob_api.http.errors.register_exception_handlers(app)` installs handlers for `ApiError` (the type application code should raise — `code`, `status_code`, optional `message`/`details`/`retry_after_seconds`), `RequestValidationError`, and `StarletteHTTPException` (covering Starlette's own 404/405 defaults too). A `Retry-After` header is forwarded whenever the raiser supplied one (`ApiError.retry_after_seconds`, or any `HTTPException(headers={"Retry-After": ...})`) — never synthesized with a guessed value.

**A real bug was found and fixed while writing `test_errors.py`'s unhandled-exception case**, not left for a future PR: Starlette promotes a handler registered for the bare `Exception` class (or status `500`) to `ServerErrorMiddleware`, which sits *outside* every `add_middleware`-registered middleware — including `RequestContextMiddleware` (ADR-0002/WP0.1-B) — in the ASGI stack. A response built there can never carry `X-Request-ID`, and by the time it runs, `RequestContextMiddleware`'s own `finally` block has already reset `request_id_var` back to `None` — so `ErrorResponse.requestId` came back as `""` too. Both were confirmed empirically (a standalone reproduction before the fix showed a missing header and an empty `requestId`; the same reproduction after the fix showed both correct) before being written up here.

Fix: `tapjob_api.http.errors.handle_unhandled_exception` is no longer registered with FastAPI for the bare `Exception` class. Instead, `RequestContextMiddleware.__call__` wraps its inner call in `try/except Exception`, and — only if no response has started yet (tracked the same way the header-injection wrapper already tracked `status_code`) — calls `handle_unhandled_exception` itself and sends the result through `send_with_request_id`, **then re-raises**. The re-raise is deliberate and mirrors Starlette's own `ServerErrorMiddleware` pattern: it still propagates the exception to the ASGI server/Sentry for observability exactly as before, while the client has already received the correctly-headered `ErrorResponse`. Because `ServerErrorMiddleware` sees `response_started = True` by the time it would otherwise act, its own (now unconfigured) default fallback never actually sends anything to the client. This means an app built without `RequestContextMiddleware` loses the ErrorResponse-shaped 500 guarantee for a truly unhandled exception (falling back to Starlette's generic plaintext response) — acceptable because every app this codebase builds (`create_app()`, and every test app in `test_errors.py`) always includes this middleware.

### 3. Pagination: cursor, not offset

`tapjob_api.http.pagination.CursorPage[T]` (`{ items, nextCursor }`, camelCase via alias) is the canonical list envelope, encoding a keyset cursor rather than an offset:

- the job feed is mutable and ordered by relevance/freshness (`docs/ARCHITECTURE.md` §10, `docs/FOUNDATION_DECISIONS.md` §7 "Core product metrics"); offset pagination silently skips or duplicates rows under concurrent writes, which a feed sees constantly;
- `DEFAULT_PAGE_SIZE = 20` / `MAX_PAGE_SIZE = 100` are named constants (`docs/ENGINEERING_STANDARDS.md` §2), not literals a future route re-invents;
- `CursorPayload { v, sort, filters_fingerprint, last_key }` is encoded as unpadded URL-safe base64 of its own JSON. `decode_cursor` rejects (as `ApiError(INVALID_CURSOR)`, never a 500) anything malformed, the wrong schema version (`CURSOR_SCHEMA_VERSION`), or a `sort`/`filters_fingerprint` mismatch against the current request — a cursor minted for one sort/filter combination cannot be replayed against a different one. A real cursor is a few hundred characters; `decode_cursor` rejects anything over `MAX_CURSOR_LENGTH` (2048) before spending any base64/JSON-parsing work on it, the same class of guard `X-Request-ID` already has (ADR-0002);
- **the cursor is explicitly documented as not tamper-evident** — there is no HMAC/signature in this checkpoint. Authorization/ownership must always be re-checked independently of cursor contents on every request; nothing may ever be inferred as "already authorized" merely because a value round-tripped through a cursor. A signed cursor is a deferred enhancement, not assumed;
- `total` is deliberately not part of the envelope: an exact count over a mutable, relevance-ordered feed is expensive and stale the instant it's computed. A resource that genuinely needs one exposes it as its own explicitly-named field.

No endpoint uses `CursorPage` yet (WP2.3's job feed is the first real consumer) — this checkpoint ships the codec and its tests (round-trip, malformed input, wrong version, sort/filter mismatch), not a feed.

### 4. Idempotency-Key: convention only, no middleware yet

Per the approved scope, this checkpoint defines the convention and nothing else: an `Idempotency-Key` header is required on a POST with an external or costly side effect (official application submission, outbound message send, AI-generated kit). The key is scoped to `(user, operation)` and must be durably recorded (PostgreSQL, not Redis — D-026: Redis is non-durable infrastructure, and losing this record could mean a duplicate real-world submission) once a route actually needs it. No table, middleware, or `ErrorCode` member for this is added in WP0.2-A; they arrive together, in whichever work package first needs them, so no enum member or migration exists without a code path that uses it.

`X-Request-ID` is unchanged in behavior from ADR-0002 (still validated, still generated when absent or unsafe) and is now additionally guaranteed to equal `ErrorResponse.requestId` on every error response (item 2). OpenTelemetry stays a future extension point: Sentry's existing instrumentation already propagates W3C `traceparent`/tracing for the accepted observability baseline (D-028), and adding a second tracing SDK without a second consumer needing it would be exactly the "mega-framework" `docs/AI_AGENT.md` §10 warns against for the AI Gateway, applied here to observability instead.

## Consequences

- `ErrorCode`'s mapping in `tapjob_api.http.errors._DEFAULT_CODE_BY_STATUS`/`_DEFAULT_MESSAGE_BY_CODE` is the single place a future route's unusual status code needs a default; `test_errors.py` asserts every `ErrorCode` member has a default message, so adding an enum member without one fails CI immediately rather than only when first hit at runtime.
- Because `handle_unhandled_exception` is called directly by `RequestContextMiddleware` instead of being dispatched by Starlette for every exception, Sentry's own automatic capture for that bare-`Exception` path is unaffected (it still observes the re-raised exception independently of this handler) — only the TapJob-specific handler registration path changed, not how Sentry's SDK observes exceptions.
- `fingerprint_filters` (used to bind a cursor to its filter set) is a non-cryptographic stability fingerprint, not a security control; do not repurpose it as one.
- The next consumer of `CursorPage`/`decode_cursor` (WP2.3) should re-read this ADR before adding a second ordering dimension (e.g. "newest" vs "relevance") to confirm the `sort` discriminator in `CursorPayload` is still sufficient, or whether per-sort cursor shapes diverge enough to need their own payload fields.
