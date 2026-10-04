"""The one bounded, log-injection-safe identifier policy shared by every correlation identifier
TapJob propagates across a trust boundary (docs/adr/0006-worker-correlation-deployment-cd-
contract.md).

An inbound identifier -- an HTTP ``X-Request-ID`` header, or a Celery message's TapJob correlation
header -- is echoed into structured logs and (for HTTP) response headers, so it must never be
trusted blindly: an attacker-controlled value could otherwise inject newlines into log lines or
carry an unbounded string. `CORRELATION_ID_PATTERN` and `resolve_correlation_id` are the single
place this policy lives; neither HTTP nor the worker should grow its own, slightly different regex
for the same semantic concern (docs/ENGINEERING_STANDARDS.md §1 "one source of truth per concern").

``candidate`` is typed as ``object``, not ``str | None`` (2026 review, Finding 3): both call sites
read this value out of attacker-reachable, untyped runtime data -- an HTTP header string from
Starlette, or a Celery message header pulled out of a broker message's ``Mapping[str, Any]``. A
broker message is not a Python call site enforcing a type hint; a malformed or hostile producer can
put an int, a list, a dict, or anything else under the same header name. `fullmatch` on a non-string
would raise `TypeError` before this function ever got a chance to reject it -- `isinstance` is
checked first, so any non-string candidate is treated exactly like an absent one: resolved to
`fallback`, never a crash.

Callers differ only in what they fall back to when the inbound value is absent, the wrong type, or
fails this policy: HTTP mints a fresh random id (docs/adr/0002-mobile-foundation.md's `X-Request-ID`
contract); the worker prefers a deterministic, already-stable identity (a task's ``root_id``) over
inventing a new random one, since a task already carries a stable workflow identity even with no
inbound header.
"""

import re
from typing import TypeGuard

CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def is_valid_correlation_id(candidate: object) -> TypeGuard[str]:
    """True only for a `str` that fully matches `CORRELATION_ID_PATTERN` -- the one predicate every
    caller in this codebase uses to decide whether an untrusted, runtime-arbitrary value is safe to
    use as a correlation id or to place into a structured log field. A `TypeGuard` so a caller's own
    `if is_valid_correlation_id(x):` branch narrows `x` to `str` for the type checker too, not just
    at runtime."""
    return isinstance(candidate, str) and CORRELATION_ID_PATTERN.fullmatch(candidate) is not None


def resolve_correlation_id(candidate: object, *, fallback: str) -> str:
    """Returns `candidate` unchanged if `is_valid_correlation_id(candidate)`, otherwise `fallback`.

    Never raises, regardless of `candidate`'s runtime type: an invalid, wrong-typed or absent
    candidate silently resolves to the caller-supplied fallback, exactly like the HTTP
    `X-Request-ID` behavior this policy was first extracted from -- a bad inbound value degrades to
    "mint one" (or, for the worker, "use the task's own stable id"), never to an error response, a
    crashed task hook, or a dropped task.
    """
    if is_valid_correlation_id(candidate):
        return candidate
    return fallback
