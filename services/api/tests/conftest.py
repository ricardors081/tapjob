from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.main import create_app

SettingsFactory = Callable[..., ApiSettings]


@pytest.fixture
def database_url() -> str:
    # Synthetic DSN: unit tests never open a database connection.
    return "postgresql+psycopg://tapjob@localhost:5432/tapjob_test"


@pytest.fixture
def make_settings(database_url: str) -> SettingsFactory:
    def factory(environment: Environment = Environment.LOCAL) -> ApiSettings:
        return ApiSettings(app_env=environment, database_url=database_url)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client
