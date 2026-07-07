"""Admin-related request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.job import JobStatus
from app.models.user import Role


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    filename: str
    status: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    celery_task_id: str | None = None
    status: JobStatus
    progress_pct: int
    created_at: datetime
    finished_at: datetime | None = None
    doc_name: str | None = None


class JobDetailOut(JobOut):
    log_tail: str = ""


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None


class UserUpdate(BaseModel):
    is_active: bool | None = None
    role: Role | None = None


class DocumentAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    page_count: int
    chunk_count: int = 0
    doc_type: str
    status: str
    uploaded_by_name: str | None = None
    uploaded_at: datetime


class FeedbackAdminOut(BaseModel):
    id: uuid.UUID
    message_id: uuid.UUID
    message_preview: str
    rating: int
    comment: str | None = None
    username: str
    created_at: datetime
