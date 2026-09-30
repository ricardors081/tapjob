"""Alembic environment. The database URL comes from typed settings, never from alembic.ini."""

from alembic import context
from sqlalchemy import create_engine, pool

from tapjob_api.core.config import ApiSettings
from tapjob_api.core.logging import configure_logging
from tapjob_api.db.base import Base

MIGRATIONS_SERVICE_NAME = "migrations"

settings = ApiSettings()
configure_logging(
    level=settings.log_level, service=MIGRATIONS_SERVICE_NAME, environment=settings.app_env
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(str(settings.database_url), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
