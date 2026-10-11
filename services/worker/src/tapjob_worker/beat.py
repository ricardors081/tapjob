"""Celery Beat entrypoint: ``celery -A tapjob_worker.beat:app beat``.

Minimal scheduler runtime: broker + periodic schedules. Does not require database
engine, object storage, or messaging provider credentials.
"""

from tapjob_worker.celery_app import create_beat_app
from tapjob_worker.config import BeatSettings

app = create_beat_app(BeatSettings())
