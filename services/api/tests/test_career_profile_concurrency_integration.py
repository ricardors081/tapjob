"""Integration tests: real concurrent requests against real PostgreSQL -- optimistic-concurrency
conflict resolution and the first-profile-create race (docs/adr/0010-career-profile-truth-store.md
"optimistic concurrency", "first-write race"). Mirrors the exact pattern WP1.1 established in
`test_identity_bootstrap_integration.py`'s `test_concurrent_first_bootstraps_...` test: builds its
own app/client (not the shared `identity_client` fixture) because several independent concurrent
client instances must hit the SAME database, and does its own manual TRUNCATE cleanup.
"""

import threading
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, func, select, text

from tapjob_api.career_profile.models import CareerProfile, CareerProfileRevision
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.dependencies import get_identity_verifier
from tapjob_api.identity.models import AccountAuditEvent
from tapjob_api.main import create_app

pytestmark = pytest.mark.integration

BOOTSTRAP_PATH = "/api/v1/auth/bootstrap"
PROFILE_PATH = "/api/v1/me/career-profile"

_TRUNCATE_SQL = (
    "TRUNCATE TABLE account_audit_events, account_deletion_requests, consent_records, "
    "user_devices, external_identities, profile_facts, career_profile_revisions, "
    "career_profiles, users CASCADE"
)


def _make_client(verifier: FakeIdentityVerifier) -> TestClient:
    settings = ApiSettings()
    app = create_app(settings)
    app.dependency_overrides[get_identity_verifier] = lambda: verifier
    return TestClient(app)


def test_concurrent_first_writes_for_the_same_user_create_exactly_one_profile_row(
    fake_identity_verifier: FakeIdentityVerifier,
) -> None:
    """N concurrent requests, each believing `expectedProfileVersion=0` (the virtual starting
    point), race to create the very first `CareerProfile` row for the same user. Exactly one must
    win the version bump; the rest must see `PROFILE_VERSION_CONFLICT`, never a lost update and
    never two rows (docs/adr/0010-...md "first-write race")."""
    settings = ApiSettings()
    fake_identity_verifier.issue_token("race-token", make_identity(subject="race-uid"))

    with _make_client(fake_identity_verifier) as client:
        bootstrap = client.post(BOOTSTRAP_PATH, headers=bearer_header("race-token"))
        assert bootstrap.status_code == HTTPStatus.OK

        statuses: list[int] = []
        lock = threading.Lock()

        def _call(index: int) -> None:
            response = client.patch(
                PROFILE_PATH,
                headers=bearer_header("race-token"),
                json={"expectedProfileVersion": 0, "headline": f"headline-{index}"},
            )
            with lock:
                statuses.append(response.status_code)

        threads = [threading.Thread(target=_call, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    engine = create_engine(str(settings.database_url))
    try:
        assert statuses.count(HTTPStatus.OK) == 1
        assert statuses.count(HTTPStatus.CONFLICT) == 7

        with engine.connect() as connection:
            profile_count = connection.execute(
                select(func.count()).select_from(CareerProfile)
            ).scalar_one()
            final_version = connection.execute(select(CareerProfile.profile_version)).scalar_one()
    finally:
        with engine.begin() as connection:
            connection.execute(text(_TRUNCATE_SQL))
        engine.dispose()

    assert profile_count == 1
    assert final_version == 1


def test_concurrent_conflicting_writes_at_a_non_zero_version_never_lose_an_update(
    fake_identity_verifier: FakeIdentityVerifier,
) -> None:
    """Two concurrent requests both believing `expectedProfileVersion=N` (a real, already-existing
    version, not the first-write case) must still resolve to exactly one winner and one
    `PROFILE_VERSION_CONFLICT` -- the version must advance by exactly one, never two, and the
    final state must match the winner's own write, never a silently merged/lost one
    (docs/adr/0010-...md "aggregate versioning")."""
    settings = ApiSettings()
    fake_identity_verifier.issue_token("race-token-2", make_identity(subject="race-uid-2"))

    with _make_client(fake_identity_verifier) as client:
        bootstrap = client.post(BOOTSTRAP_PATH, headers=bearer_header("race-token-2"))
        assert bootstrap.status_code == HTTPStatus.OK
        seed = client.patch(
            PROFILE_PATH,
            headers=bearer_header("race-token-2"),
            json={"expectedProfileVersion": 0, "headline": "seed"},
        )
        assert seed.status_code == HTTPStatus.OK
        assert seed.json()["profileVersion"] == 1

        results: list[tuple[int, str]] = []
        lock = threading.Lock()

        def _call(label: str) -> None:
            response = client.patch(
                PROFILE_PATH,
                headers=bearer_header("race-token-2"),
                json={"expectedProfileVersion": 1, "headline": label},
            )
            with lock:
                results.append((response.status_code, label))

        threads = [threading.Thread(target=_call, args=(f"race-{i}",)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [label for status, label in results if status == HTTPStatus.OK]
        conflicts = [label for status, label in results if status == HTTPStatus.CONFLICT]

        final = client.get(PROFILE_PATH, headers=bearer_header("race-token-2"))

    engine = create_engine(str(settings.database_url))
    try:
        with engine.begin() as connection:
            connection.execute(text(_TRUNCATE_SQL))
    finally:
        engine.dispose()

    assert len(winners) == 1
    assert len(conflicts) == 5
    assert final.json()["profileVersion"] == 2
    assert final.json()["headline"] == winners[0]


def test_a_concurrent_virtual_no_op_can_never_corrupt_a_real_first_mutation(
    fake_identity_verifier: FakeIdentityVerifier,
) -> None:
    """Several concurrent same-value (`headline: null`, matching the VIRTUAL default) no-op
    attempts race against ONE real first mutation (`headline: "Engineer"`), all claiming
    `expectedProfileVersion=0` for a brand-new user (round-3 independent review "BLOCKER 2"/
    concurrency). Each no-op either lands BEFORE the real mutation (sees no row yet, correctly
    returns `200`/`profileVersion: 0` without writing anything) or AFTER it (sees the now-real row
    at version 1, disagrees with its own stale `expectedProfileVersion=0`, correctly conflicts) --
    which one is racy and expected; what must NEVER happen is a wrong final version, a duplicate
    row, a duplicate revision, or a spurious audit event."""
    settings = ApiSettings()
    fake_identity_verifier.issue_token("race-token-3", make_identity(subject="race-uid-3"))

    with _make_client(fake_identity_verifier) as client:
        bootstrap = client.post(BOOTSTRAP_PATH, headers=bearer_header("race-token-3"))
        assert bootstrap.status_code == HTTPStatus.OK

        results: list[tuple[str, int, int | None]] = []
        lock = threading.Lock()

        def _no_op_call(index: int) -> None:
            response = client.patch(
                PROFILE_PATH,
                headers=bearer_header("race-token-3"),
                json={"expectedProfileVersion": 0, "headline": None},
            )
            version = response.json().get("profileVersion") if response.status_code == 200 else None
            with lock:
                results.append((f"no-op-{index}", response.status_code, version))

        def _real_call() -> None:
            response = client.patch(
                PROFILE_PATH,
                headers=bearer_header("race-token-3"),
                json={"expectedProfileVersion": 0, "headline": "Engineer"},
            )
            version = response.json().get("profileVersion") if response.status_code == 200 else None
            with lock:
                results.append(("real", response.status_code, version))

        threads = [threading.Thread(target=_no_op_call, args=(i,)) for i in range(6)]
        threads.append(threading.Thread(target=_real_call))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    real_results = [r for r in results if r[0] == "real"]
    no_op_results = [r for r in results if r[0] != "real"]

    engine = create_engine(str(settings.database_url))
    try:
        with engine.connect() as connection:
            profile_count = connection.execute(
                select(func.count()).select_from(CareerProfile)
            ).scalar_one()
            final_profile = connection.execute(
                select(CareerProfile.profile_version, CareerProfile.headline)
            ).one()
            revision_count = connection.execute(
                select(func.count()).select_from(CareerProfileRevision)
            ).scalar_one()
            audit_count = connection.execute(
                select(func.count())
                .select_from(AccountAuditEvent)
                .where(AccountAuditEvent.event_type == "profile_updated")
            ).scalar_one()
    finally:
        with engine.begin() as connection:
            connection.execute(text(_TRUNCATE_SQL))
        engine.dispose()

    # The real mutation always wins (nothing else ever competes to bump expectedVersion=0 --
    # no-op attempts never write).
    assert len(real_results) == 1
    assert real_results[0][1] == HTTPStatus.OK
    assert real_results[0][2] == 1

    # Every no-op attempt is EITHER an early 200/version-0 OR a late 409 conflict -- never
    # anything else (never a non-zero successful version, never a 500).
    for _, status, version in no_op_results:
        assert status in (HTTPStatus.OK, HTTPStatus.CONFLICT)
        if status == HTTPStatus.OK:
            assert version == 0

    # Exactly one row, at exactly one real version, with exactly one audit event -- no corruption
    # from any interleaving of the no-op attempts. Exactly TWO revisions is correct here, by design
    # (docs/adr/0010-...md "historical top-level revision"): one at profile_version=0 (the empty
    # state, written when the row is first materialized by the real mutation) and one at
    # profile_version=1 (the real values) -- a no-op attempt never writes a revision regardless of
    # how the race interleaves, so these two are solely from the one real mutation.
    assert profile_count == 1
    assert final_profile.profile_version == 1
    assert final_profile.headline == "Engineer"
    assert revision_count == 2
    assert audit_count == 1
