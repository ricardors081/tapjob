"""SQLAlchemy engine lifecycle. The engine is created once per process and reused; callers must
not call ``create_engine`` on every request (docs/ENGINEERING_STANDARDS.md §23).
"""

from sqlalchemy import Engine, create_engine


def create_database_engine(database_url: str, *, connect_timeout_seconds: float) -> Engine:
    # libpq's connect_timeout is a whole number of seconds, and 0 means "no timeout"
    # (the opposite of what a readiness probe wants), so round up to at least 1.
    connect_timeout = max(1, round(connect_timeout_seconds))
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout},
    )
