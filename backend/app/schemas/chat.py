"""Chat-related request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat import MessageRole


class MessageRequest(BaseModel):
    session_id: uuid.UUID | None = None
    content: str = Field(min_length=1, max_length=8000)


class SourceChunk(BaseModel):
    id: str
    section_path: str
    page: int | None = None
    preview: str
    doc_name: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    source_chunks: list | None = None
    latency_ms: int | None = None
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut] = []


class SessionTitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
