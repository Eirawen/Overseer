from __future__ import annotations

import os

from celery import Celery

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def build_celery_app() -> Celery:
    redis_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    app = Celery("overseer")
    app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )
    app.autodiscover_tasks(["overseer.execution"])
    return app


celery_app = build_celery_app()
