"""Unit tests for `tapjob_api.resume_import.storage` (docs/adr/0011-resume-import.md "object
storage", round-1 independent review "Finding 1"). Pure, no real network/R2 call anywhere --
`LocalFileObjectStorage` uses a real temporary directory on disk, never a mock of a real adapter.
"""

import uuid
from pathlib import Path

import pytest

from tapjob_api.core.config import ApiSettings, Environment
from tapjob_api.resume_import.limits import (
    R2_CONNECT_TIMEOUT_SECONDS,
    R2_MAX_RETRY_ATTEMPTS,
    R2_READ_TIMEOUT_SECONDS,
)
from tapjob_api.resume_import.storage import (
    LocalFileObjectStorage,
    ObjectStorageConfigError,
    ObjectStorageError,
    R2ObjectStorage,
    create_storage_adapter,
)

_DATABASE_URL = "postgresql+psycopg://tapjob@localhost:5432/tapjob_test"
_REDIS_URL = "redis://localhost:6379/0"


def _settings(
    *,
    app_env: Environment = Environment.LOCAL,
    r2_endpoint_url: str | None = None,
    r2_bucket: str | None = None,
    r2_access_key_id: str | None = None,
    r2_secret_access_key: str | None = None,
    resume_import_local_storage_dir: str | None = None,
) -> ApiSettings:
    return ApiSettings(
        app_env=app_env,
        database_url=_DATABASE_URL,  # type: ignore[arg-type]
        redis_url=_REDIS_URL,  # type: ignore[arg-type]
        r2_endpoint_url=r2_endpoint_url,
        r2_bucket=r2_bucket,
        r2_access_key_id=r2_access_key_id,
        r2_secret_access_key=r2_secret_access_key,
        resume_import_local_storage_dir=resume_import_local_storage_dir,
    )


# -- create_storage_adapter resolution order ------------------------------------------------------


def test_full_r2_config_selects_the_real_r2_adapter_in_any_environment() -> None:
    for app_env in (
        Environment.LOCAL,
        Environment.DEVELOPMENT,
        Environment.STAGING,
        Environment.PRODUCTION,
    ):
        adapter = create_storage_adapter(
            _settings(
                app_env=app_env,
                r2_endpoint_url="https://example.r2.cloudflarestorage.com",
                r2_bucket="bucket",
                r2_access_key_id="key",
                r2_secret_access_key="secret",
            )
        )
        assert isinstance(adapter, R2ObjectStorage)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"r2_endpoint_url": "https://example.com"},
        {"r2_bucket": "bucket"},
        {"r2_access_key_id": "key", "r2_bucket": "bucket"},
        {"r2_endpoint_url": "https://example.com", "r2_bucket": "b", "r2_access_key_id": "k"},
    ],
)
def test_partial_r2_config_fails_fast_in_any_environment(kwargs: dict[str, str]) -> None:
    """Round-1 independent review "Finding 1": a PARTIAL R2 configuration is always an operator
    mistake, never interpreted charitably, regardless of environment."""
    for app_env in (Environment.LOCAL, Environment.STAGING):
        with pytest.raises(ObjectStorageConfigError):
            create_storage_adapter(_settings(app_env=app_env, **kwargs))


def test_missing_r2_config_fails_fast_in_staging_and_production() -> None:
    """Staging/production must never silently fall back to a process-local storage adapter -- the
    exact cross-process bug this finding describes."""
    for app_env in (Environment.STAGING, Environment.PRODUCTION):
        with pytest.raises(ObjectStorageConfigError):
            create_storage_adapter(_settings(app_env=app_env))


def test_missing_r2_config_with_a_shared_local_dir_uses_local_file_storage_in_development() -> None:
    adapter = create_storage_adapter(
        _settings(app_env=Environment.DEVELOPMENT, resume_import_local_storage_dir="/data/x")
    )
    assert isinstance(adapter, LocalFileObjectStorage)


def test_r2_adapter_configures_a_named_connect_read_timeout_and_bounded_retries() -> None:
    """Round-2 independent review "Hardening 9" (docs/ENGINEERING_STANDARDS.md §25): never the
    SDK's own invisible defaults."""
    adapter = create_storage_adapter(
        _settings(
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
            r2_bucket="bucket",
            r2_access_key_id="key",
            r2_secret_access_key="secret",
        )
    )
    assert isinstance(adapter, R2ObjectStorage)
    boto_config = adapter._client.meta.config
    assert boto_config.connect_timeout == R2_CONNECT_TIMEOUT_SECONDS
    assert boto_config.read_timeout == R2_READ_TIMEOUT_SECONDS
    assert boto_config.retries["mode"] == "standard"
    # botocore normalizes `max_attempts` into `total_max_attempts` (the initial try plus retries).
    assert boto_config.retries["total_max_attempts"] == R2_MAX_RETRY_ATTEMPTS + 1


def test_missing_r2_config_with_no_local_dir_fails_fast_in_local_and_development() -> None:
    """Round-2 independent review "Finding 5": `create_storage_adapter` must never implicitly
    return `InMemoryObjectStorage` any more, not even in `local`/`development` -- a REAL process
    started without either R2 or a shared local directory configured has no genuinely durable,
    genuinely cross-process storage option, the exact cross-process bug "Finding 1" already fixed
    for staging/production. `InMemoryObjectStorage` is now ONLY ever constructed directly by a test
    fixture (`tapjob_api.main.create_app`'s `resume_storage_override` parameter), never obtained
    from this function."""
    for app_env in (Environment.LOCAL, Environment.DEVELOPMENT):
        with pytest.raises(ObjectStorageConfigError):
            create_storage_adapter(_settings(app_env=app_env))


# -- LocalFileObjectStorage: real filesystem round trip -------------------------------------------


def test_local_file_storage_put_get_delete_round_trip(tmp_path: Path) -> None:
    storage = LocalFileObjectStorage(base_dir=str(tmp_path))
    key = f"resume-imports/{uuid.uuid4()}/{uuid.uuid4().hex}.pdf"

    stored = storage.put(storage_key=key, content=b"hello world", content_type="application/pdf")
    assert stored.byte_size == len(b"hello world")

    assert storage.get(storage_key=key) == b"hello world"

    storage.delete(storage_key=key)
    with pytest.raises(ObjectStorageError):
        storage.get(storage_key=key)


def test_local_file_storage_delete_normalizes_an_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-2 independent review "Finding 6": `delete` must normalize a raw `OSError` to
    `ObjectStorageError` exactly like `put`/`get` already do -- a caller performing a best-effort
    compensating delete needs a typed exception it can `contextlib.suppress`."""
    storage = LocalFileObjectStorage(base_dir=str(tmp_path))
    key = f"resume-imports/{uuid.uuid4()}/{uuid.uuid4().hex}.pdf"
    storage.put(storage_key=key, content=b"x", content_type="application/pdf")

    def _raise(*_: object, **__: object) -> None:
        raise OSError("simulated permission error")

    monkeypatch.setattr(Path, "unlink", _raise)
    with pytest.raises(ObjectStorageError):
        storage.delete(storage_key=key)


def test_local_file_storage_rejects_a_storage_key_escaping_the_base_dir(tmp_path: Path) -> None:
    storage = LocalFileObjectStorage(base_dir=str(tmp_path))
    with pytest.raises(ObjectStorageError):
        storage.put(storage_key="../../etc/passwd", content=b"x", content_type="text/plain")


def test_two_independent_local_file_storage_instances_share_the_same_directory(
    tmp_path: Path,
) -> None:
    """THE process-boundary proof (round-1 independent review "Finding 1" test requirement): two
    SEPARATE `LocalFileObjectStorage` instances, constructed independently (simulating the API
    process and the worker process, which never share Python state), pointed at the SAME directory
    (simulating the Docker named volume `infra/compose.yaml` mounts on both containers) -- an
    object one instance writes is genuinely readable by the OTHER instance. This is the structural
    guarantee `InMemoryObjectStorage` can never provide across a real process boundary."""
    base_dir = str(tmp_path)
    api_process_storage = LocalFileObjectStorage(base_dir=base_dir)
    worker_process_storage = LocalFileObjectStorage(base_dir=base_dir)

    key = f"resume-imports/{uuid.uuid4()}/{uuid.uuid4().hex}.docx"
    api_process_storage.put(storage_key=key, content=b"shared content", content_type="x")

    # The WORKER's own instance -- never told about `api_process_storage` directly -- reads the
    # SAME bytes back, because durability lives in the shared directory, not in either Python
    # object's own memory.
    assert worker_process_storage.get(storage_key=key) == b"shared content"

    worker_process_storage.delete(storage_key=key)
    with pytest.raises(ObjectStorageError):
        api_process_storage.get(storage_key=key)
