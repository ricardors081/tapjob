"""Use-case layer for WP1.2 (docs/adr/0010-career-profile-truth-store.md). Channel-first
(docs/adr/0010-...md "channel-first design"): every function here is a plain, ownership-scoped
call over an already-authenticated `User` -- nothing accepts a caller-supplied `user_id`, and
nothing here knows about HTTP. A future `MessagingChannel`/WhatsApp adapter calls the exact same
functions the HTTP router calls (`tapjob_api.career_profile.router`); HTTP is just one caller, not
a privileged one. Round-2 independent review's BLOCKER 1 made this literal: every invariant a
future adapter must respect is enforced HERE, never only in `router.py`'s Pydantic DTOs --
`add_profile_fact`/`replace_profile_fact` take a typed `FactValue` (never an arbitrary dict) and
validate `value.kind` against the fact type themselves; `update_career_profile` takes explicit
typed parameters (never an open `fields: dict[str, object]` fed through `setattr`);
`replace_profile_fact`/`retract_profile_fact` take a bare `fact_id` and perform their OWN
ownership-scoped lookup, never trusting an already-looked-up ORM object a caller might have
resolved incorrectly.

Transaction convention (same as WP1.1's `tapjob_api.identity.service`): every function here defers
the final `session.commit()` to its caller (the router), which stages the matching audit event
first so both land in the same transaction. The fact-mutation profile-version bump is a single
atomic conditional `UPDATE ... WHERE profile_version = :expected ... RETURNING`, the real
concurrency guarantee (docs/adr/0010-...md "optimistic concurrency") -- a plain SELECT-then-write
would race a concurrent mutation the same way WP1.1's `cancel_deletion_request` bug once did.
`update_career_profile` alone uses a different, pessimistic (`SELECT ... FOR UPDATE`) pattern --
see its own docstring for why.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tapjob_api.career_profile.commands import CareerProfilePatchCommand
from tapjob_api.career_profile.fact_values import FactValue
from tapjob_api.career_profile.models import (
    CareerProfile,
    CareerProfileRevision,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.identity.models import User


class ProfileVersionConflictError(Exception):
    """Raised when `expected_profile_version` no longer matches the profile's real current
    version -- the caller must refetch and retry explicitly, never silently overwritten
    (docs/adr/0010-...md "optimistic concurrency")."""


class ProfileFactNotActiveError(Exception):
    """Raised when attempting to replace/retract a fact that is no longer `ACTIVE` (already
    superseded or retracted by an earlier call) -- the router maps this to
    `PROFILE_FACT_ALREADY_RETRACTED` (409), never a silent no-op."""


class ProfileFactNotFoundError(Exception):
    """Raised by `replace_profile_fact`/`retract_profile_fact` when `fact_id` does not resolve to a
    fact owned by `user` -- indistinguishable from a nonexistent fact (docs/adr/0010-...md
    "ownership / IDOR testing"). Enforced INSIDE the service (round-2 independent review "BLOCKER
    1"), never only by a caller's own prior lookup: a channel adapter that got its own lookup wrong
    can never still mutate another user's fact through this function."""


class ProfileFactValueMismatchError(Exception):
    """Raised when a fact value's own `kind` does not match the declared/existing fact type --
    enforced INSIDE the service (round-2 independent review "BLOCKER 1"), not only by
    `router.py`'s Pydantic DTOs, so a future channel adapter calling `service.py` directly cannot
    bypass this invariant."""


class ProfileVersionNotFoundError(Exception):
    """Raised when an explicitly requested `profileVersion` is greater than the profile's real
    current version (or greater than 0 when no `CareerProfile` row exists yet) -- a snapshot is
    never fabricated for a version that was never actually reached (docs/adr/0010-...md "truth
    snapshot filter", round-1 independent review "future version error")."""


def _record_profile_revision(session: Session, *, profile: CareerProfile) -> None:
    """Captures the profile's own top-level truth (`headline`/`summary`/`target_roles`) AT its
    current `profile_version` (docs/adr/0010-...md "historical top-level revision"). Called once
    when the row is first created (`profile_version=0`, the empty initial state) and once per
    `update_career_profile` call (the new, post-mutation state) -- never on a fact-only mutation,
    since those never change these fields."""
    session.add(
        CareerProfileRevision(
            user_id=profile.user_id,
            career_profile_id=profile.id,
            profile_version=profile.profile_version,
            headline=profile.headline,
            summary=profile.summary,
            target_roles=list(profile.target_roles),
        )
    )


def _get_or_create_career_profile(session: Session, *, user: User) -> CareerProfile:
    """Idempotent, concurrency-safe (docs/adr/0010-...md "first-write race"): two concurrent first
    mutations for the same `User` race to insert, exactly one wins, the loser re-queries the
    now-committed row -- the exact SAVEPOINT pattern `tapjob_api.identity.service.bootstrap_user`
    already established for the same class of race. A freshly created row starts at
    `profile_version=0`, semantically identical to "no profile yet" (`get_profile` below) so a
    caller's `expectedProfileVersion=0` means the same thing whether or not a row already
    existed."""
    existing = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user.id))
    if existing is not None:
        return existing

    profile = CareerProfile(user_id=user.id)
    try:
        with session.begin_nested():
            session.add(profile)
            session.flush()
            # The initial (empty) revision at profile_version=0 -- same SAVEPOINT as the profile
            # row itself, so a losing concurrent racer's rollback also discards this, never leaving
            # an orphaned revision for a profile row that didn't actually win the race.
            _record_profile_revision(session, profile=profile)
            session.flush()
    except IntegrityError:
        resolved = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user.id))
        if resolved is None:
            raise
        return resolved
    return profile


def get_career_profile(session: Session, *, user: User) -> CareerProfile | None:
    """Read-only -- never creates a row (docs/adr/0010-...md "profile creation": no surprising
    side effect in a read path, mirroring WP1.1's `find_user_by_firebase_subject`). The router
    builds a virtual `profileVersion: 0` response when this returns `None`."""
    return session.scalar(select(CareerProfile).where(CareerProfile.user_id == user.id))


def _bump_profile_version(session: Session, *, user: User, expected_version: int) -> CareerProfile:
    """The one real concurrency guard for every logical mutation (docs/adr/0010-...md "aggregate
    versioning"): a single atomic conditional `UPDATE ... WHERE profile_version = :expected
    ... RETURNING`. Postgres's own row lock on the matched row is what actually serializes two
    concurrent conflicting writes -- the loser's `WHERE` clause simply no longer matches once the
    winner commits, never a lost update. Ensures the profile row exists first (idempotent) so a
    fact can be added before any top-level profile field was ever set."""
    _get_or_create_career_profile(session, user=user)
    updated = session.execute(
        update(CareerProfile)
        .where(CareerProfile.user_id == user.id, CareerProfile.profile_version == expected_version)
        .values(profile_version=CareerProfile.profile_version + 1)
        .returning(CareerProfile)
    )
    profile = updated.scalars().one_or_none()
    if profile is None:
        raise ProfileVersionConflictError
    return profile


class ProfileUpdateResult:
    """Return value of `update_career_profile`. `profile` is `None` exactly when the command was a
    genuine no-op against a `CareerProfile` that has NEVER been created (round-3 independent review
    "BLOCKER 2") -- the caller (router) passes this straight to `_profile_response`, which already
    builds the correct virtual response (`profileVersion: 0`, `createdAt: null`) for `None`.
    `fields_changed` is empty exactly when the command was a no-op (either the virtual case above,
    or an existing profile whose resulting state equals its current state) -- the caller uses this
    to decide whether to audit a success event at all."""

    __slots__ = ("fields_changed", "profile")

    def __init__(self, *, profile: CareerProfile | None, fields_changed: list[str]) -> None:
        self.profile = profile
        self.fields_changed = fields_changed


def update_career_profile(
    session: Session, *, user: User, expected_version: int, command: CareerProfilePatchCommand
) -> ProfileUpdateResult:
    """Requires a validated `CareerProfilePatchCommand` -- never a plain `dict`/loosely-typed
    kwargs (round-3 independent review "BLOCKER 1"). `headline`/`summary`/`target_roles`'s own
    trim/non-blank/control-character/bounds/dedupe validation already happened when `command` was
    constructed (`commands.py`), so this function only ever sees already-valid content; a future
    channel adapter gets the identical guarantee by constructing the SAME command type, never a
    duplicated (and possibly out-of-sync) copy of these rules. "Absent" vs. "explicit `None`" is
    read from `command.model_fields_set`, exactly as the HTTP router used to compute it itself.
    `target_roles` never becomes a literal `None` on the ORM row -- the column is `NOT NULL`; an
    explicit `target_roles: None` on the command means "clear to an empty list."

    Uses `SELECT ... FOR UPDATE` rather than the atomic conditional-UPDATE pattern every other
    mutation here uses (round-2 independent review "ADDITIONAL REVIEW"): a genuine same-value patch
    (the resulting state would be IDENTICAL to the current state) must be a no-op -- no version
    bump, no revision row, no success audit -- while a STALE `expected_version` must still conflict
    even when the submitted values happen to match. When NO `CareerProfile` row has ever been
    created yet, the "current state" being compared against is the VIRTUAL default (`headline`/
    `summary=None`, `target_roles=[]`, `profile_version=0`) -- round-3 independent review "BLOCKER
    2": the row is only ever materialized once we already know this is a REAL change (or that the
    caller's own `expected_version` itself disagrees with "nothing has ever been created"), never
    merely to perform the no-op comparison itself. A tentative, lock-free check decides whether a
    virtual no-op can return without touching the database at all; anything else (a real change, OR
    an `expected_version` that does not match the virtual `0`) falls through to
    `_get_or_create_career_profile` (which may create the row or discover one a concurrent racer
    already created) followed by a `FOR UPDATE` lock and a FRESH version check against whatever row
    actually exists now -- the pre-materialization `expected_version` comparison is NEVER trusted by
    itself once a row might need to be written to, closing a race the first draft of this fix had
    (a concurrently-created row's real version silently ignored). A virtual no-op never writes
    anything at all, so it never participates in any lock and can never corrupt a concurrent REAL
    first-mutation for the same user (`_get_or_create_career_profile`'s own SAVEPOINT/
    `IntegrityError`-retry remains the sole first-write-race guard, unchanged). Two concurrent
    requests for the same, already-existing profile serialize on the `FOR UPDATE` lock rather than
    racing a fail-fast conditional UPDATE -- a different latency characteristic, not a different
    correctness guarantee: the loser still observes the winner's new version and still conflicts
    correctly."""
    present = command.model_fields_set
    headline = command.headline if "headline" in present else None
    summary = command.summary if "summary" in present else None
    # `None` on the wire/command means "clear to an empty list" -- the column is NOT NULL.
    target_roles = (command.target_roles or []) if "target_roles" in present else None

    existing = session.scalar(select(CareerProfile).where(CareerProfile.user_id == user.id))

    if existing is None:
        tentative_change = (
            ("headline" in present and headline is not None)
            or ("summary" in present and summary is not None)
            or ("target_roles" in present and target_roles != [])
        )
        if not tentative_change and expected_version == 0:
            # Genuinely nothing to do, and the caller's own expectation already matches the
            # virtual state -- return without ever creating a row, a revision, or an audit event.
            return ProfileUpdateResult(profile=None, fields_changed=[])
        # Either a real change is being asserted, or `expected_version` itself disagrees with "no
        # profile has ever been created" -- resolve/lock the REAL row (creating it if truly nobody
        # has yet) and re-validate against ITS actual version below, never the pre-materialization
        # assumption above.
        existing = _get_or_create_career_profile(session, user=user)

    locked = session.execute(
        select(CareerProfile).where(CareerProfile.id == existing.id).with_for_update()
    ).scalar_one()
    if locked.profile_version != expected_version:
        raise ProfileVersionConflictError

    fields_changed: list[str] = []
    if "headline" in present and headline != locked.headline:
        fields_changed.append("headline")
    if "summary" in present and summary != locked.summary:
        fields_changed.append("summary")
    if "target_roles" in present and target_roles != locked.target_roles:
        fields_changed.append("target_roles")

    if not fields_changed:
        return ProfileUpdateResult(profile=locked, fields_changed=[])

    locked.profile_version += 1
    if "headline" in present:
        locked.headline = headline
    if "summary" in present:
        locked.summary = summary
    if "target_roles" in present and target_roles is not None:
        locked.target_roles = target_roles
    session.flush()
    # A new revision captures the post-mutation top-level state at the NEW profile_version -- the
    # only place besides profile creation where headline/summary/target_roles actually change
    # (docs/adr/0010-...md "historical top-level revision").
    _record_profile_revision(session, profile=locked)
    session.flush()
    return ProfileUpdateResult(profile=locked, fields_changed=fields_changed)


def find_owned_fact(session: Session, *, user: User, fact_id: uuid.UUID) -> ProfileFact | None:
    """Ownership-scoped lookup (docs/adr/0010-...md "ownership / IDOR testing"): a fact belonging
    to a different user is indistinguishable from a nonexistent one -- the router maps `None` to
    `PROFILE_FACT_NOT_FOUND` either way."""
    return session.scalar(
        select(ProfileFact).where(ProfileFact.id == fact_id, ProfileFact.user_id == user.id)
    )


def list_active_facts(
    session: Session, *, user: User, fact_type: FactType | None = None
) -> list[ProfileFact]:
    """Current truth only -- superseded/retracted facts are hidden by default
    (docs/adr/0010-...md "retraction"), never hard-deleted, still reachable via `find_owned_fact`
    for a fact's own id."""
    stmt = select(ProfileFact).where(
        ProfileFact.user_id == user.id, ProfileFact.status == FactStatus.ACTIVE
    )
    if fact_type is not None:
        stmt = stmt.where(ProfileFact.fact_type == fact_type)
    return list(session.scalars(stmt.order_by(ProfileFact.created_at.asc())))


def add_profile_fact(
    session: Session,
    *,
    user: User,
    expected_version: int,
    fact_type: FactType,
    value: FactValue,
    now: datetime | None = None,
) -> ProfileFact:
    """ALWAYS writes `source_kind=USER_DIRECT`/`user_confirmed=True` -- this is the one endpoint a
    public client can call directly, and the server NEVER trusts a client-supplied provenance/
    confirmation value (docs/adr/0010-...md "provenance / no source spoofing"). A brand-new fact
    starts its own lineage (`lineage_id = id`). `value` is a TYPED `FactValue` (round-2 independent
    review "BLOCKER 1") -- never an arbitrary `dict`; `value.kind` is validated against `fact_type`
    HERE, not only by `router.py`'s `AddProfileFactRequest`, so a future channel adapter calling
    this function directly cannot store a fact whose stated type disagrees with its own value."""
    if value.kind != fact_type.value:
        raise ProfileFactValueMismatchError
    profile = _bump_profile_version(session, user=user, expected_version=expected_version)
    current = now or datetime.now(UTC)
    fact_id = uuid.uuid4()
    fact = ProfileFact(
        id=fact_id,
        user_id=user.id,
        career_profile_id=profile.id,
        fact_type=fact_type,
        value_json=value.model_dump(mode="json", by_alias=True),
        source_kind=FactSourceKind.USER_DIRECT,
        user_confirmed=True,
        confirmed_at=current,
        status=FactStatus.ACTIVE,
        lineage_id=fact_id,
        active_from_profile_version=profile.profile_version,
    )
    session.add(fact)
    session.flush()
    return fact


def replace_profile_fact(
    session: Session,
    *,
    user: User,
    fact_id: uuid.UUID,
    expected_version: int,
    value: FactValue,
    now: datetime | None = None,
) -> ProfileFact:
    """A changed claim NEVER mutates `fact.value_json` in place (docs/adr/0010-...md "fact
    immutability", the deliberate deviation from `career-ops-hq/career-ops`'s own in-place-mutation
    behavior -- see docs/SOURCE_REUSE_LEDGER.md). It creates a NEW row in the SAME lineage,
    supersedes the old one, and the old row (and its original `value_json`) remains permanently
    readable via `find_owned_fact`.

    Takes a bare `fact_id`, not an already-resolved `ProfileFact` (round-2 independent review
    "BLOCKER 1"): the ownership-scoped lookup happens INSIDE this function, so a caller can never
    pass in an ORM object it (incorrectly) resolved for the wrong user and have this function trust
    it. `value.kind` is validated against the EXISTING fact's own type HERE too, not only by
    `router.py`."""
    fact = find_owned_fact(session, user=user, fact_id=fact_id)
    if fact is None:
        raise ProfileFactNotFoundError
    if fact.status is not FactStatus.ACTIVE:
        raise ProfileFactNotActiveError
    if value.kind != fact.fact_type.value:
        raise ProfileFactValueMismatchError
    profile = _bump_profile_version(session, user=user, expected_version=expected_version)
    current = now or datetime.now(UTC)

    fact.status = FactStatus.SUPERSEDED
    fact.inactive_from_profile_version = profile.profile_version
    session.flush()  # the old row must be SUPERSEDED before the new ACTIVE row is inserted, or the
    # partial unique index on (lineage_id) WHERE status='active' would reject the insert below.

    new_fact_id = uuid.uuid4()
    replacement = ProfileFact(
        id=new_fact_id,
        user_id=user.id,
        career_profile_id=profile.id,
        fact_type=fact.fact_type,
        value_json=value.model_dump(mode="json", by_alias=True),
        source_kind=FactSourceKind.USER_DIRECT,
        user_confirmed=True,
        confirmed_at=current,
        status=FactStatus.ACTIVE,
        lineage_id=fact.lineage_id,
        supersedes_fact_id=fact.id,
        active_from_profile_version=profile.profile_version,
    )
    session.add(replacement)
    session.flush()
    return replacement


def retract_profile_fact(
    session: Session,
    *,
    user: User,
    fact_id: uuid.UUID,
    expected_version: int,
    now: datetime | None = None,
) -> ProfileFact:
    """Logical retraction only -- the row is never hard-deleted (docs/adr/0010-...md "retraction",
    docs/ENGINEERING_STANDARDS.md §23 "soft delete only when product/legal behavior requires it").
    No replacement fact is created; the lineage simply has no `ACTIVE` member until/unless a future
    `add_profile_fact` starts a new one (a retraction does not implicitly reuse the old
    `lineage_id`, since a brand-new claim about the same topic is a new, independent assertion).

    Takes a bare `fact_id`, not an already-resolved `ProfileFact` (round-2 independent review
    "BLOCKER 1") -- same ownership-scoped-lookup-inside-the-service discipline as
    `replace_profile_fact`."""
    fact = find_owned_fact(session, user=user, fact_id=fact_id)
    if fact is None:
        raise ProfileFactNotFoundError
    if fact.status is not FactStatus.ACTIVE:
        raise ProfileFactNotActiveError
    profile = _bump_profile_version(session, user=user, expected_version=expected_version)
    fact.status = FactStatus.RETRACTED
    fact.inactive_from_profile_version = profile.profile_version
    session.flush()
    return fact


class TruthSnapshot:
    """The ONLY shape any future consumer may use to ground generated external material
    (docs/AI_AGENT.md §6, AGENTS.md §6, docs/adr/0010-...md "truth snapshot"). Plain data holder,
    not an ORM model -- `get_truth_snapshot` below is the one place this is constructed."""

    __slots__ = ("facts", "headline", "profile_version", "summary", "target_roles", "user_id")

    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        profile_version: int,
        headline: str | None,
        summary: str | None,
        target_roles: list[str],
        facts: list[ProfileFact],
    ) -> None:
        self.user_id = user_id
        self.profile_version = profile_version
        self.headline = headline
        self.summary = summary
        self.target_roles = target_roles
        self.facts = facts


def _resolve_historical_top_level_state(
    session: Session, *, profile: CareerProfile, target_version: int
) -> CareerProfileRevision:
    """The latest `CareerProfileRevision` at or before `target_version` -- NOT the live
    `CareerProfile` row, which only ever reflects the CURRENT state (docs/adr/0010-...md
    "historical top-level revision", round-1 independent review "BLOCKER 1"). Always resolves to a
    real row for any `target_version` in `[0, profile.profile_version]`: the very first mutation
    that ever created `profile` also inserted the `profile_version=0` revision in the same
    transaction (`_get_or_create_career_profile`), so there is never a gap."""
    revision = session.scalar(
        select(CareerProfileRevision)
        .where(
            CareerProfileRevision.career_profile_id == profile.id,
            CareerProfileRevision.profile_version <= target_version,
        )
        .order_by(CareerProfileRevision.profile_version.desc())
        .limit(1)
    )
    if revision is None:  # pragma: no cover -- defensive; see docstring invariant above.
        raise AssertionError(
            f"no CareerProfileRevision at or before version {target_version} for profile "
            f"{profile.id} -- the profile_version=0 revision should always exist"
        )
    return revision


def get_truth_snapshot(
    session: Session, *, user: User, profile_version: int | None = None
) -> TruthSnapshot:
    """Default filter (docs/adr/0010-...md "truth snapshot filter"): confirmed, active, not
    retracted/superseded, owned by this user -- NEVER an unconfirmed candidate, NEVER another
    user's fact. `profile_version=None` means "current" (the LIVE `CareerProfile` row, unchanged
    behavior); an explicit historical version reconstructs BOTH the fact-side truth (each fact's own
    `active_from`/`inactive_from_profile_version` window) AND the profile's own top-level truth
    (`headline`/`summary`/`target_roles`, via `_resolve_historical_top_level_state` -- round-1
    independent review's BLOCKER 1 fix: these were previously always read from the CURRENT profile
    regardless of the requested version, mixing temporal states). A version greater than the
    profile's real current version (or greater than 0 when no profile exists yet) is rejected with
    `ProfileVersionNotFoundError` -- a snapshot is never fabricated for a version that was never
    actually reached. Never a second event-sourcing framework (docs/adr/0010-...md "historical
    version")."""
    profile = get_career_profile(session, user=user)
    current_version = profile.profile_version if profile is not None else 0
    target_version = current_version if profile_version is None else profile_version

    if target_version > current_version:
        raise ProfileVersionNotFoundError

    if profile_version is None or profile is None:
        # "Current" (explicit or implied by no profile existing yet) -- the live row, unchanged
        # from before this fix.
        headline = profile.headline if profile is not None else None
        summary = profile.summary if profile is not None else None
        target_roles = list(profile.target_roles) if profile is not None else []
    else:
        historical = _resolve_historical_top_level_state(
            session, profile=profile, target_version=target_version
        )
        headline = historical.headline
        summary = historical.summary
        target_roles = list(historical.target_roles)

    stmt = select(ProfileFact).where(
        ProfileFact.user_id == user.id,
        ProfileFact.user_confirmed.is_(True),
        ProfileFact.active_from_profile_version <= target_version,
        (ProfileFact.inactive_from_profile_version.is_(None))
        | (ProfileFact.inactive_from_profile_version > target_version),
    )
    facts = list(session.scalars(stmt.order_by(ProfileFact.created_at.asc())))

    return TruthSnapshot(
        user_id=user.id,
        profile_version=target_version,
        headline=headline,
        summary=summary,
        target_roles=target_roles,
        facts=facts,
    )
