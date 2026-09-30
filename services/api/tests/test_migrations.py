from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from tapjob_api.core.config import ApiSettings

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
