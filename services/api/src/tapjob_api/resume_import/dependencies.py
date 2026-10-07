"""FastAPI dependencies for WP1.3 (docs/adr/0011-resume-import.md). Reuses WP1.1's verified-token
trust boundary and generic rate limiter wholesale, plus WP1.3's own storage-adapter selection --
no second current-user mechanism, no second rate-limit framework, no second storage selector
(docs/ENGINEERING_STANDARDS.md §1).
"""

from celery import Celery
from fastapi import Request

from tapjob_api.core.rate_limit import RateLimitPolicy
from tapjob_api.http.errors import ApiError, ErrorCode
from tapjob_api.resume_import.storage import ObjectStoragePort

# Upload/finalize are the abuse-sensitive writes (docs/adr/0011-...md "rate limit") -- reads
# (polling import status, fetching the master resume) are deliberately unthrottled, same
# discipline as `career_profile.dependencies`.
RESUME_IMPORT_UPLOAD_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:resume-imports:upload", limit=10, window_seconds=300
)
RESUME_IMPORT_FINALIZE_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:resume-imports:finalize", limit=20, window_seconds=300
)


def get_resume_storage(request: Request) -> ObjectStoragePort:
    storage: ObjectStoragePort | None = getattr(request.app.state, "resume_storage", None)
    if storage is None:
        raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, status_code=503)
    return storage


def get_task_producer(request: Request) -> Celery:
    """A lightweight Celery PRODUCER instance (docs/adr/0011-...md "async processing") -- it only
    ever calls `.send_task(name, args=...)` by task name string; it never imports or executes
    `tapjob_api.resume_import.tasks.process_resume_import_task` itself (that happens only in the
    worker process, `tapjob_worker.celery_app`). Keeps the dependency direction worker -> api
    intact (docs/adr/0006-worker-correlation-deployment-cd-contract.md) -- the API process never
    needs to import anything from `tapjob_worker`."""
    producer: Celery | None = getattr(request.app.state, "celery_producer", None)
    if producer is None:
        raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, status_code=503)
    return producer
