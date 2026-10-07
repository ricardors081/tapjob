"""Object storage boundary for resume source files (docs/adr/0011-resume-import.md "object
storage"). A local, internal `Protocol` -- not an addition to the canonical seven WP0.2-B ports
(`docs/adr/0005-internal-ports-capability-model.md`), since storage is not one of the externally
pluggable provider capabilities that blueprint enumerates and this WP has no real architectural
need to make it one yet (docs/ENGINEERING_STANDARDS.md §14 "avoid over-abstraction for internal
code that has no realistic alternative" -- there is exactly one production adapter, R2).

Canonical architecture: Cloudflare R2, private, S3-compatible (docs/ARCHITECTURE.md §3 "Storage").
No bucket is ever public; every stored object is reached only by this process's own server-side
credentials, never a client-facing URL baked into a response.
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from tapjob_api.core.config import Environment, RuntimeSettings
from tapjob_api.resume_import.limits import (
    R2_CONNECT_TIMEOUT_SECONDS,
    R2_MAX_RETRY_ATTEMPTS,
    R2_READ_TIMEOUT_SECONDS,
)

_CHECKSUM_ALGORITHM = "sha256"


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    checksum_sha256: str
    byte_size: int


class ObjectStorageError(Exception):
    """Raised for any storage-adapter failure -- never a raw `botocore`/network exception
    crossing this boundary (docs/ENGINEERING_STANDARDS.md §13-14, same discipline as
    `ports._common.PortError` for the canonical ports)."""


class ObjectStorageConfigError(Exception):
    """Raised by `create_storage_adapter` itself, at process-startup configuration time, never at
    first use (round-1 independent review "Finding 1"): a PARTIAL R2 configuration (some but not
    all four settings present) is never valid in any environment, and a MISSING R2 configuration
    in `staging`/`production` is never silently tolerated as a process-local fallback. Both fail
    the process fast and loud at startup, never a confusing later `ObjectStorageError` the first
    time a real upload happens to reach storage."""


class ObjectStoragePort(Protocol):
    """`put`/`get`/`delete` all operate on an already-computed, random `storage_key` -- this port
    never derives a key from a user-supplied filename (docs/adr/0011-...md "never make the original
    filename the storage key")."""

    def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject: ...

    def get(self, *, storage_key: str) -> bytes: ...

    def delete(self, *, storage_key: str) -> None: ...


def new_storage_key(*, user_id: uuid.UUID, extension: str) -> str:
    """A fresh, random, non-enumerable key, namespaced by owner for operational clarity only --
    ownership is ALWAYS re-checked against the database row, never inferred from this path
    (docs/adr/0011-...md "file security")."""
    return f"resume-imports/{user_id}/{uuid.uuid4().hex}{extension}"


def compute_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class R2ObjectStorage:
    """Cloudflare R2 production adapter -- S3-compatible, private bucket, server-only credentials
    (docs/ENGINEERING_STANDARDS.md §3.3: never `EXPO_PUBLIC_*`, never logged). No signed-URL
    issuance lives here; WP1.3's API surface has no download endpoint (docs/adr/0011-...md
    "private file access" -- not required by this WP's own accepted scope)."""

    def __init__(
        self, *, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket: str
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            # R2 does not use AWS regions; "auto" is Cloudflare's own documented convention, and
            # `signature_version="s3v4"` is required for R2 compatibility.
            region_name="auto",
            config=BotoConfig(
                signature_version="s3v4",
                # Round-2 independent review "Hardening 9" (docs/ENGINEERING_STANDARDS.md §25):
                # never the SDK's own invisible defaults -- a named connect/read timeout and a
                # bounded retry policy, so a hung/slow R2 endpoint cannot stall a worker/request
                # indefinitely, and a brief network blip is absorbed by boto3's own bounded retry
                # before ever surfacing as `ObjectStorageError`.
                connect_timeout=R2_CONNECT_TIMEOUT_SECONDS,
                read_timeout=R2_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": R2_MAX_RETRY_ATTEMPTS, "mode": "standard"},
            ),
        )

    def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=storage_key, Body=content, ContentType=content_type
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("put failed") from error
        return StoredObject(
            storage_key=storage_key,
            checksum_sha256=compute_checksum(content),
            byte_size=len(content),
        )

    def get(self, *, storage_key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            content: bytes = response["Body"].read()
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("get failed") from error
        return content

    def delete(self, *, storage_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=storage_key)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("delete failed") from error


def create_storage_adapter(settings: RuntimeSettings) -> "ObjectStoragePort":
    """Shared adapter-selection logic for both process lifespans (`tapjob_api.main`,
    `tapjob_worker.celery_app`) -- never duplicated per process (docs/ENGINEERING_STANDARDS.md §1).

    Resolution order (round-1 independent review "Finding 1"):
    1. All four R2 settings present -> real `R2ObjectStorage`, in any environment.
    2. SOME but not all four R2 settings present -> `ObjectStorageConfigError`, in any
       environment. A partial configuration is always an operator mistake, never a value this
       function tries to interpret charitably.
    3. Zero R2 settings present, `app_env` is `staging`/`production` -> `ObjectStorageConfigError`.
       Staging/production must NEVER silently fall back to a process-local storage adapter -- the
       exact bug this finding describes: the API process and the worker process are always
       separate OS processes in these environments, so an object one process `put()`s would be
       genuinely unreadable by the other's own in-memory fake, previously failing invisibly deep
       inside a Celery task instead of loudly at process startup.
    4. Zero R2 settings present, `app_env` is `local`/`development`,
       `resume_import_local_storage_dir` IS set -> `LocalFileObjectStorage`, a shared FILESYSTEM
       directory (a Docker named volume in `infra/compose.yaml`, mounted at the SAME path by both
       the `api` and `worker` services) -- genuinely durable and genuinely shared across the two
       real local processes, never a fake pretending to be shared.
    5. Zero R2 settings present, `app_env` is `local`/`development`,
       `resume_import_local_storage_dir` is UNSET -> `ObjectStorageConfigError` (round-2
       independent review "Finding 5"): this function NEVER implicitly returns
       `InMemoryObjectStorage` any more. A runtime process started without EITHER real R2 OR a
       shared local directory configured has no genuinely durable, cross-process storage -- the
       exact cross-process bug "Finding 1" already fixed for staging/production applies just as
       much to a real `local`/`development` deployment missing its own compose-provided shared
       volume env var. `InMemoryObjectStorage` is now ONLY ever constructed directly, injected
       explicitly by a test fixture/dependency override (`tapjob_api.main.create_app`'s own
       `resume_storage_override` parameter) -- never obtained as an implicit runtime fallback a
       caller might not even realize it was relying on."""
    configured_r2_values = (
        settings.r2_endpoint_url,
        settings.r2_bucket,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
    )
    r2_present_count = sum(1 for value in configured_r2_values if value)

    if (
        r2_present_count == len(configured_r2_values)
        and settings.r2_endpoint_url
        and settings.r2_bucket
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
    ):
        return R2ObjectStorage(
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
        )
    if r2_present_count > 0:
        raise ObjectStorageConfigError(
            "partial R2 configuration: r2_endpoint_url, r2_bucket, r2_access_key_id and "
            "r2_secret_access_key must all be set together, or none at all"
        )
    if settings.app_env in (Environment.STAGING, Environment.PRODUCTION):
        raise ObjectStorageConfigError(
            f"R2 object storage configuration is required in {settings.app_env.value} -- set "
            "r2_endpoint_url, r2_bucket, r2_access_key_id and r2_secret_access_key"
        )
    if settings.resume_import_local_storage_dir:
        return LocalFileObjectStorage(base_dir=settings.resume_import_local_storage_dir)
    raise ObjectStorageConfigError(
        f"no object storage is configured for {settings.app_env.value} -- set either R2 "
        "(r2_endpoint_url, r2_bucket, r2_access_key_id, r2_secret_access_key) or "
        "resume_import_local_storage_dir (a shared directory both the api and worker processes "
        "mount, e.g. infra/compose.yaml's resume-storage-data volume). This function never "
        "implicitly falls back to an in-memory, process-local fake (round-2 independent review "
        "'Finding 5') -- tests that need one inject InMemoryObjectStorage explicitly instead "
        "(tapjob_api.main.create_app's resume_storage_override parameter)."
    )


class LocalFileObjectStorage:
    """A shared FILESYSTEM directory -- the local/development durable adapter (round-1 independent
    review "Finding 1"): genuinely readable by a separate worker process, as long as both processes
    mount the SAME directory (a Docker named volume in `infra/compose.yaml`). Never used in
    staging/production (`create_storage_adapter` requires real R2 there). `storage_key` always
    comes from `new_storage_key` (random, server-generated) -- this class still defensively
    confirms the resolved path stays inside `base_dir` before any read/write, the same
    defense-in-depth discipline `file_validation.py` applies to uploaded content, never trusting a
    single call site to always pass a safe value."""

    def __init__(self, *, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    def _resolved_path(self, storage_key: str) -> Path:
        path = (self._base_dir / storage_key).resolve()
        if self._base_dir.resolve() not in path.parents and path != self._base_dir.resolve():
            raise ObjectStorageError("storage_key resolves outside the storage directory")
        return path

    def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        del content_type  # unused by this adapter; the real R2 adapter sets it on the object
        path = self._resolved_path(storage_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError as error:
            raise ObjectStorageError("put failed") from error
        return StoredObject(
            storage_key=storage_key,
            checksum_sha256=compute_checksum(content),
            byte_size=len(content),
        )

    def get(self, *, storage_key: str) -> bytes:
        path = self._resolved_path(storage_key)
        try:
            return path.read_bytes()
        except OSError as error:
            raise ObjectStorageError("object not found") from error

    def delete(self, *, storage_key: str) -> None:
        path = self._resolved_path(storage_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            # Round-2 independent review "Finding 6": the SAME normalized-error boundary
            # `put`/`get` already apply -- a raw `OSError` (e.g. a permissions problem) must never
            # cross this port, including for a caller that specifically wants to ATTEMPT a
            # best-effort compensating delete and needs a typed exception it can
            # `contextlib.suppress`.
            raise ObjectStorageError("delete failed") from error


class InMemoryObjectStorage:
    """Test-only fake -- process-local, never a real network call, never genuinely shared across a
    real process boundary (docs/adr/0011-...md "object storage: test adapter"). NEVER returned by
    `create_storage_adapter` (round-2 independent review "Finding 5") -- constructed and injected
    explicitly by test fixtures only (`tapjob_api.main.create_app`'s `resume_storage_override`
    parameter), never obtained as an implicit runtime fallback."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, *, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        del content_type  # unused by the fake; the real adapter sets it on the provider object
        self._objects[storage_key] = content
        return StoredObject(
            storage_key=storage_key,
            checksum_sha256=compute_checksum(content),
            byte_size=len(content),
        )

    def get(self, *, storage_key: str) -> bytes:
        try:
            return self._objects[storage_key]
        except KeyError as error:
            raise ObjectStorageError("object not found") from error

    def delete(self, *, storage_key: str) -> None:
        self._objects.pop(storage_key, None)
