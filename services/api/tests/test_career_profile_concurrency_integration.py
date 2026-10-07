"""Integration tests: real concurrent requests against real PostgreSQL -- optimistic-concurrency
conflict resolution and the first-profile-create race (docs/adr/0010-career-profile-truth-store.md
"optimistic concurrency", "first-write race"). Mirrors the exact pattern WP1.1 established in
`test_identity_bootstrap_integration.py`'s `test_concurrent_first_bootstraps_...` test: builds its
own app/client (not the shared `identity_client` fixture) because several independent concurrent
client instances must hit the SAME database, and does its own manual TRUNCATE cleanup.
"""

import threading
import uuid
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier, bearer_header, make_identity
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from tapjob_api.career_profile import service
from tapjob_api.career_profile.commands import CareerProfilePatchCommand
from tapjob_api.career_profile.models import CareerProfile, CareerProfileRevision
from tapjob_api.core.config import ApiSettings
from tapjob_api.identity.dependencies import get_identity_verifier
from tapjob_api.identity.models import AccountAuditEvent, User
from tapjob_api.main import create_app
from tapjob_api.resume_import.storage import InMemoryObjectStorage

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
    app = create_app(settings, resume_storage_override=InMemoryObjectStorage())
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


def test_a_stale_identity_mapped_profile_is_refreshed_by_the_lock_query_before_the_version_check(
    fake_identity_verifier: FakeIdentityVerifier,
) -> None:
    """Deterministic (no thread timing) reproduction of the post-merge identity-map hotfix
    (docs/adr/0010-career-profile-truth-store.md addendum, round 4): a caller session that already
    holds a `CareerProfile` instance loaded WITHOUT a lock must still observe the REAL, current
    `profile_version` once `update_career_profile`'s own `SELECT ... FOR UPDATE` runs against that
    same row -- never the stale in-memory copy its identity map was already holding, even though no
    thread here ever actually contends for the Postgres row lock. This isolates
    `execution_options(populate_existing=True)` as the mechanism doing real work, independent of
    thread-scheduling luck: against the pre-fix code (an un-refreshed `with_for_update()` query)
    this test fails deterministically, every run, because the stale `expected_version=1` incorrectly
    still matches the stale cached object instead of conflicting against the real, current
    version 2."""
    settings = ApiSettings()
    fake_identity_verifier.issue_token("stale-id-token", make_identity(subject="stale-id-uid"))

    with _make_client(fake_identity_verifier) as client:
        bootstrap = client.post(BOOTSTRAP_PATH, headers=bearer_header("stale-id-token"))
        assert bootstrap.status_code == HTTPStatus.OK
        user_id = uuid.UUID(bootstrap.json()["user"]["id"])

        seed = client.patch(
            PROFILE_PATH,
            headers=bearer_header("stale-id-token"),
            json={"expectedProfileVersion": 0, "headline": "v1"},
        )
        assert seed.status_code == HTTPStatus.OK
        assert seed.json()["profileVersion"] == 1

    engine = create_engine(str(settings.database_url))
    try:
        with Session(engine) as session_b:
            user_b = session_b.get(User, user_id)
            assert user_b is not None
            # Un-locked full-entity load -- identity-maps a `CareerProfile` instance at
            # profile_version=1 in session_b, exactly what a prior read earlier in the same request
            # (e.g. a `get_career_profile` call) would also leave behind.
            stale = service.get_career_profile(session_b, user=user_b)
            assert stale is not None
            assert stale.profile_version == 1

            # A second, independent session performs and COMMITS a real mutation -- the real DB row
            # is now at version=2 -- entirely outside session_b's own knowledge.
            with Session(engine) as session_a:
                user_a = session_a.get(User, user_id)
                assert user_a is not None
                winner = service.update_career_profile(
                    session_a,
                    user=user_a,
                    expected_version=1,
                    command=CareerProfilePatchCommand(headline="v2"),
                )
                session_a.commit()
                assert winner.profile is not None
                assert winner.profile.profile_version == 2

            # session_b's own cached object is still stale here -- nothing has refreshed it yet.
            # The question this test answers is whether `update_career_profile`'s OWN lock query,
            # run on THIS session, sees past that stale cached copy to the real, current row.
            assert stale.profile_version == 1

            with pytest.raises(service.ProfileVersionConflictError):
                service.update_career_profile(
                    session_b,
                    user=user_b,
                    expected_version=1,  # stale -- the real row is already at version 2
                    command=CareerProfilePatchCommand(headline="v3-should-never-land"),
                )
            session_b.rollback()

        with engine.connect() as connection:
            revision_count = connection.execute(
                select(func.count()).select_from(CareerProfileRevision)
            ).scalar_one()
            final = connection.execute(
                select(CareerProfile.profile_version, CareerProfile.headline)
            ).one()
    finally:
        with engine.begin() as connection:
            connection.execute(text(_TRUNCATE_SQL))
        engine.dispose()

    # Exactly the winner's three revisions -- v0 (empty state, at creation), v1 (the seed), v2 (the
    # real winner) -- never a fourth, duplicate revision at version=2 from the rejected session_b
    # attempt that the pre-fix code would have let through.
    assert revision_count == 3
    assert final.profile_version == 2
    assert final.headline == "v2"
