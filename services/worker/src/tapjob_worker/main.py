"""Celery entrypoint: ``celery -A tapjob_worker.main:app worker``.

Settings are loaded and validated here, at process start, so a misconfigured worker fails fast.
"""

from tapjob_worker.celery_app import create_celery_app
from tapjob_worker.config import WorkerSettings

app = create_celery_app(WorkerSettings())
