from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from identity_fakes import FakeIdentityVerifier
from sqlalchemy import create_engine, text

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.identity.dependencies import get_identity_verifier
from tapjob_api.main import create_app

SettingsFactory = Callable[..., ApiSettings]

# WP1.1 identity + WP1.2 career-profile tables, in FK-safe TRUNCATE order (docs/adr/0009-
# identity-auth-user-account.md, docs/adr/0010-career-profile-truth-store.md). TRUNCATE ... CASCADE
# does not actually require this order (CASCADE follows FKs regardless of list order), but it's
# kept readable/explicit anyway.
_IDENTITY_TABLES = (
    "account_audit_events",
    "account_deletion_requests",
    "consent_records",
    "user_devices",
    "external_identities",
    "profile_facts",
    "career_profile_revisions",
    "career_profiles",
    "users",
)
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def database_url() -> str:
    # Synthetic DSN: unit tests never open a database connection.
    return "postgresql+psycopg://tapjob@localhost:5432/tapjob_test"


@pytest.fixture
def redis_url() -> str:
    # Synthetic DSN: unit tests never open a Redis connection.
    return "redis://localhost:6379/0"


@pytest.fixture
def make_settings(database_url: str, redis_url: str) -> SettingsFactory:
    def factory(environment: Environment = Environment.LOCAL) -> ApiSettings:
        return ApiSettings(
            app_env=environment,
            database_url=database_url,  # type: ignore[arg-type]
            redis_url=redis_url,  # type: ignore[arg-type]
        )

    return factory


@pytest.fixture
def client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client


# -- WP1.1 identity integration fixtures (docs/ENGINEERING_STANDARDS.md §27) ---------------------
# Real PostgreSQL/Redis, as provisioned by the `backend-integration` CI job. Every test using
# `identity_client` gets a verified-token trust boundary backed by `FakeIdentityVerifier` -- no
# test in this suite ever contacts a real Firebase project (docs/adr/0009-...md "operational
# prerequisites").


@pytest.fixture(scope="session")
def _identity_schema_ready() -> None:
    command.upgrade(Config(str(_ALEMBIC_INI)), "head")


@pytest.fixture
def fake_identity_verifier() -> FakeIdentityVerifier:
    return FakeIdentityVerifier()


def _flush_rate_limit_keys(redis_dsn: str) -> None:
    # Every `RateLimitPolicy.key_prefix` in `tapjob_api.identity.dependencies` starts with `rl:`
    # (docs/ENGINEERING_STANDARDS.md §2). Many test files intentionally reuse the same literal
    # Firebase subject (e.g. "uid-1") as fixture data for readability; without this flush, their
    # rate-limit counters accumulate in the SAME Redis instance across the whole pytest session and
    # eventually trip a real 429 for a later, unrelated test -- a test-isolation bug, not a product
    # bug (caught via CI's backend-integration job, not locally: Docker was unavailable).
    client = redis.Redis.from_url(redis_dsn)
    try:
        keys = list(client.scan_iter(match="rl:*"))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


@pytest.fixture
def identity_client(
    _identity_schema_ready: None, fake_identity_verifier: FakeIdentityVerifier
) -> Iterator[TestClient]:
    settings = ApiSettings()
    _flush_rate_limit_keys(str(settings.redis_url))
    app = create_app(settings)
    app.dependency_overrides[get_identity_verifier] = lambda: fake_identity_verifier
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        engine = create_engine(str(settings.database_url))
        try:
            with engine.begin() as connection:
                connection.execute(text(f"TRUNCATE TABLE {', '.join(_IDENTITY_TABLES)} CASCADE"))
        finally:
            engine.dispose()
        _flush_rate_limit_keys(str(settings.redis_url))
