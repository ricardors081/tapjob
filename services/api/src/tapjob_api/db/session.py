"""Request-scoped SQLAlchemy sessions (docs/ENGINEERING_STANDARDS.md §23). One `sessionmaker` is
created once per process from the shared `Engine` (`tapjob_api.core.database`), and `get_db_session`
is the FastAPI dependency every route/service function uses to get a `Session` -- never calling
`sessionmaker()`/`Session()` directly at a call site.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: a route/service often returns an ORM object's already-read attributes
    # in the same response after committing; the default expire-on-commit would force a second,
    # unnecessary round-trip to re-fetch them.
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db_session(request: Request) -> Iterator[Session]:
    session_factory: sessionmaker[Session] = request.app.state.db_session_factory
    with session_factory() as session:
        yield session
