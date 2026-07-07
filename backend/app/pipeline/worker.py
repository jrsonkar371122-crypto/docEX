"""Celery application definition and synchronous DB session factory."""
from __future__ import annotations

from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

celery_app = Celery(
    "documind",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_max_tasks_per_child=20,
    broker_connection_retry_on_startup=True,
)

# Ensure the task module is imported so tasks register with the worker.
celery_app.autodiscover_tasks(["app.pipeline"])

# Synchronous engine used exclusively by Celery workers.
sync_engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)


# Import tasks at the bottom so the app is defined first.
import app.pipeline.tasks  # noqa: E402,F401
