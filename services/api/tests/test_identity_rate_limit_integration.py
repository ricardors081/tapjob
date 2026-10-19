"""Integration test: the bootstrap rate limit against REAL Redis (docs/adr/0009-identity-auth-
user-account.md "rate limit"), proving the fail-closed wiring end to end -- not just the pure
`RedisFixedWindowRateLimiter` logic already covered by test_identity_rate_limit.py's unit tests.
"""

from datetime import UTC, datetime
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity

from tapjob_api.identity.dependencies import BOOTSTRAP_RATE_LIMIT, DELETION_REQUEST_RATE_LIMIT

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
DELETION_PATH = "/api/v1/me/deletion-request"


@pytest.fixture(autouse=True)
def freeze_rate_limit_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze datetime in rate limiter so requests never cross minute boundaries during test."""
    frozen_now = datetime(2026, 10, 19, 12, 0, 15, tzinfo=UTC)
    mock_dt = MagicMock(wraps=datetime)
    mock_dt.now.return_value = frozen_now
    monkeypatch.setattr("tapjob_api.core.rate_limit.datetime", mock_dt)


def test_bootstrap_is_rejected_once_the_real_limit_is_exceeded(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))

    responses = [
        identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("token-1"))
        for _ in range(BOOTSTRAP_RATE_LIMIT.limit + 1)
    ]

    assert all(r.status_code == HTTPStatus.OK for r in responses[: BOOTSTRAP_RATE_LIMIT.limit])
    last = responses[-1]
    assert last.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert last.json()["code"] == "RATE_LIMITED"
    assert "Retry-After" in last.headers


def test_cancelling_a_deletion_request_shares_the_real_rate_limit_budget_with_requesting_one(
    identity_client: TestClient, fake_identity_verifier: FakeIdentityVerifier
) -> None:
    """`cancelAccountDeletion` deliberately reuses `DELETION_REQUEST_RATE_LIMIT` (same key, same
    principal) rather than a second policy -- both mutate the same resource family, so churning
    request/cancel/request/... is bounded by one combined budget (2026 independent-review
    finding 3)."""
    fake_identity_verifier.issue_token("token-1", make_identity(subject="uid-1"))
    identity_client.post(BOOTSTRAP_PATH, headers=bearer_header("token-1"))

    def alternating_call(index: int) -> Response:
        if index % 2 == 0:
            return identity_client.post(  # type: ignore[no-any-return]
                DELETION_PATH, headers=bearer_header("token-1")
            )
        return identity_client.delete(  # type: ignore[no-any-return]
            DELETION_PATH, headers=bearer_header("token-1")
        )

    within_budget = [alternating_call(i) for i in range(DELETION_REQUEST_RATE_LIMIT.limit)]
    assert all(r.status_code in (HTTPStatus.ACCEPTED, HTTPStatus.OK) for r in within_budget), [
        r.json() for r in within_budget
    ]

    over_budget = alternating_call(DELETION_REQUEST_RATE_LIMIT.limit)

    assert over_budget.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert over_budget.json()["code"] == "RATE_LIMITED"
