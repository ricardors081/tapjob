"""Unit tests for `tapjob_api.identity.firebase` -- the one module allowed to import
`firebase_admin`. Every `firebase_admin.auth` call is monkeypatched; no real Firebase project is
ever contacted (docs/adr/0009-identity-auth-user-account.md "operational prerequisites").
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from tapjob_api.identity.firebase import (
    FirebaseIdentityProvider,
    FirebaseTokenError,
    TokenFailureReason,
)


class _FakeApp:
    """Never actually used by firebase_admin once verify_id_token/revoke_refresh_tokens are
    monkeypatched -- just a distinct sentinel object the provider holds a reference to."""


@pytest.fixture
def provider() -> FirebaseIdentityProvider:
    return FirebaseIdentityProvider(_FakeApp())


def _claims(**overrides: Any) -> dict[str, Any]:
    base = {
        "uid": "firebase-uid-1",
        "email": "person@example.com",
        "email_verified": True,
        "auth_time": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
    }
    base.update(overrides)
    return base


def test_valid_token_returns_verified_identity(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda *a, **k: _claims())

    identity = provider.verify_id_token("some-token")

    assert identity.subject == "firebase-uid-1"
    assert identity.email == "person@example.com"
    assert identity.email_verified is True
    assert identity.auth_time == datetime(2026, 1, 1, tzinfo=UTC)


def test_verify_id_token_always_passes_check_revoked_true(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    captured: dict[str, Any] = {}

    def fake_verify(token: str, app: Any = None, check_revoked: bool = False) -> dict[str, Any]:
        captured["check_revoked"] = check_revoked
        return _claims()

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)

    provider.verify_id_token("some-token")

    assert captured["check_revoked"] is True


def test_missing_optional_claims_resolve_to_none(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    claims = {"uid": "firebase-uid-2", "auth_time": 1_700_000_000}
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda *a, **k: claims)

    identity = provider.verify_id_token("some-token")

    assert identity.email is None
    assert identity.email_verified is None


def test_non_string_email_claim_is_ignored_rather_than_trusted(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda *a, **k: _claims(email=12345))

    identity = provider.verify_id_token("some-token")

    assert identity.email is None


def test_missing_uid_claim_is_rejected_even_though_the_sdk_accepted_it(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    claims = {"auth_time": 1_700_000_000}
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda *a, **k: claims)

    with pytest.raises(FirebaseTokenError) as excinfo:
        provider.verify_id_token("some-token")
    assert excinfo.value.reason == TokenFailureReason.INVALID


def test_missing_auth_time_claim_is_rejected(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    claims = {"uid": "firebase-uid-3"}
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda *a, **k: claims)

    with pytest.raises(FirebaseTokenError) as excinfo:
        provider.verify_id_token("some-token")
    assert excinfo.value.reason == TokenFailureReason.INVALID


@pytest.mark.parametrize(
    ("raised", "expected_reason"),
    [
        (firebase_auth.InvalidIdTokenError("bad token"), TokenFailureReason.INVALID),
        (ValueError("not even a JWT"), TokenFailureReason.INVALID),
        (firebase_auth.ExpiredIdTokenError("expired", None), TokenFailureReason.EXPIRED),
        (firebase_auth.RevokedIdTokenError("revoked"), TokenFailureReason.REVOKED),
        (firebase_auth.UserDisabledError("disabled"), TokenFailureReason.DISABLED),
        (
            firebase_auth.CertificateFetchError("cert fetch failed", None),
            TokenFailureReason.UNAVAILABLE,
        ),
    ],
)
def test_verify_id_token_normalizes_every_sdk_failure(
    monkeypatch: pytest.MonkeyPatch,
    provider: FirebaseIdentityProvider,
    raised: Exception,
    expected_reason: str,
) -> None:
    def fake_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise raised

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)

    with pytest.raises(FirebaseTokenError) as excinfo:
        provider.verify_id_token("some-token")

    assert excinfo.value.reason == expected_reason
    # The real SDK exception's own type/message never crosses this boundary (AGENTS.md §8).
    assert not isinstance(excinfo.value, type(raised))


def test_revoke_refresh_tokens_calls_the_sdk_with_the_subject(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    captured: dict[str, Any] = {}

    def fake_revoke(uid: str, app: Any = None) -> None:
        captured["uid"] = uid

    monkeypatch.setattr(firebase_auth, "revoke_refresh_tokens", fake_revoke)

    provider.revoke_refresh_tokens("firebase-uid-1")

    assert captured["uid"] == "firebase-uid-1"


def test_revoke_refresh_tokens_normalizes_a_firebase_error(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    def fake_revoke(uid: str, app: Any = None) -> None:
        raise firebase_exceptions.FirebaseError(firebase_exceptions.UNKNOWN, "boom")

    monkeypatch.setattr(firebase_auth, "revoke_refresh_tokens", fake_revoke)

    with pytest.raises(FirebaseTokenError) as excinfo:
        provider.revoke_refresh_tokens("firebase-uid-1")
    assert excinfo.value.reason == TokenFailureReason.UNAVAILABLE


def test_revoke_refresh_tokens_normalizes_a_value_error(
    monkeypatch: pytest.MonkeyPatch, provider: FirebaseIdentityProvider
) -> None:
    def fake_revoke(uid: str, app: Any = None) -> None:
        raise ValueError("uid must not be empty")

    monkeypatch.setattr(firebase_auth, "revoke_refresh_tokens", fake_revoke)

    with pytest.raises(FirebaseTokenError) as excinfo:
        provider.revoke_refresh_tokens("")
    assert excinfo.value.reason == TokenFailureReason.UNAVAILABLE
