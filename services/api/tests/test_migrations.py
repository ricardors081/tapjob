import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, inspect

from tapjob_api.career_profile.models import (
    CareerProfile,
    FactSourceKind,
    FactStatus,
    FactType,
    ProfileFact,
)
from tapjob_api.core.config import ApiSettings
from tapjob_api.db.session import create_session_factory
from tapjob_api.identity.models import User

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def test_migration_history_has_a_single_head() -> None:
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()

    assert len(heads) == 1


@pytest.mark.integration
def test_migrations_upgrade_and_downgrade_against_postgres() -> None:
    """Requires APP_ENV and DATABASE_URL pointing at a disposable PostgreSQL database."""
    config = _alembic_config()
    engine = create_engine(str(ApiSettings().database_url))
    try:
        command.upgrade(config, "head")
        assert "alembic_version" in inspect(engine).get_table_names()

        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        engine.dispose()


@pytest.mark.integration
def test_downgrading_0004_with_a_resume_import_sourced_profile_fact_fails_fast() -> None:
    """Round-2 independent review "Finding 7": migration 0004's downgrade must never silently
    EITHER falsify a real `profile_facts.source_kind = 'resume_import'` row's provenance to
    `'user_direct'`, OR silently delete real Truth Store data -- it must fail fast and loud
    instead, requiring an explicit, separately-approved operational decision. An empty-database
    round trip (`test_migrations_upgrade_and_downgrade_against_postgres` above) is completely
    unaffected -- this is specifically the DATA-BEARING case that test's own empty schema can never
    exercise."""
    config = _alembic_config()
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    command.upgrade(config, "head")
    session_factory = create_session_factory(engine)

    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    try:
        with session_factory() as session:
            session.add(User(id=user_id, locale="en-US"))
            session.flush()
            session.add(CareerProfile(id=profile_id, user_id=user_id))
            session.flush()
            session.add(
                ProfileFact(
                    id=fact_id,
                    user_id=user_id,
                    career_profile_id=profile_id,
                    fact_type=FactType.SKILL,
                    value_json={"kind": "skill", "name": "Python"},
                    source_kind=FactSourceKind.RESUME_IMPORT,
                    source_ref="test-migration-0004-downgrade",
                    evidence_ref="test-migration-0004-downgrade",
                    confidence=1.0,
                    user_confirmed=True,
                    confirmed_at=datetime.now(UTC),
                    status=FactStatus.ACTIVE,
                    lineage_id=fact_id,
                    active_from_profile_version=1,
                )
            )
            session.commit()

        # alembic may or may not wrap the raised RuntimeError in its own exception type -- broad
        # on purpose, the only thing this asserts is "the downgrade did not silently succeed".
        with pytest.raises(Exception):  # noqa: B017
            command.downgrade(config, "0003")
    finally:
        # Always restore to head and clean up, regardless of the outcome above -- this test must
        # never leave the shared integration database downgraded or with stray fixture rows.
        command.upgrade(config, "head")
        with session_factory() as session:
            session.execute(delete(ProfileFact).where(ProfileFact.id == fact_id))
            session.execute(delete(CareerProfile).where(CareerProfile.id == profile_id))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
        engine.dispose()


@pytest.mark.integration
def test_migration_0005_upgrade_downgrade_cycle() -> None:
    """Explicit upgrade -> downgrade -> upgrade cycle for migration 0005 (jobs foundation)."""
    config = _alembic_config()
    settings = ApiSettings()
    engine = create_engine(str(settings.database_url))
    try:
        command.upgrade(config, "0005")
        tables = inspect(engine).get_table_names()
        for t in ("companies", "jobs", "job_origins", "idempotency_keys"):
            assert t in tables

        command.downgrade(config, "0004")
        tables_after_downgrade = inspect(engine).get_table_names()
        for t in ("companies", "jobs", "job_origins", "idempotency_keys"):
            assert t not in tables_after_downgrade

        command.upgrade(config, "head")
        tables_after_reupgrade = inspect(engine).get_table_names()
        for t in ("companies", "jobs", "job_origins", "idempotency_keys"):
            assert t in tables_after_reupgrade
    finally:
        command.upgrade(config, "head")
        engine.dispose()
