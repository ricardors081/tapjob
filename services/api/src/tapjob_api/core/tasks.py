"""A lightweight Celery PRODUCER for the HTTP API process (WP1.3, docs/adr/0011-resume-import.md
"async processing"). The API process only ever enqueues tasks by name (`.send_task`); it never
imports or runs a worker task itself -- `tapjob_worker.celery_app` owns the actual consumer/worker
app and imports the real task modules. Broker-connection settings mirror `tapjob_worker.celery_app`
exactly (same JSON-only serialization) so a message produced here is always consumable there.
"""

from celery import Celery

TASK_SERIALIZER = "json"


def create_task_producer(redis_url: str) -> Celery:
    app = Celery("api-producer", broker=redis_url)
    app.conf.update(
        task_serializer=TASK_SERIALIZER,
        accept_content=[TASK_SERIALIZER],
        result_serializer=TASK_SERIALIZER,
        task_ignore_result=True,
    )
    return app
