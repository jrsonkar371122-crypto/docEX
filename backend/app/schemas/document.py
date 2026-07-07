"""Document-related response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocStatus, DocType


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_number: int
    has_table: bool
    has_image: bool
    is_ocr: bool


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    content: str
    section_path: str
    has_table: bool
    has_image: bool
    token_count: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    original_name: str
    file_size_bytes: int
    page_count: int
    doc_type: DocType
    status: DocStatus
    uploaded_by: uuid.UUID | None = None
    uploaded_at: datetime
    processed_at: datetime | None = None
    error_message: str | None = None


class DocumentDetailOut(DocumentOut):
    chunk_count: int = 0
    pages: list[PageOut] = []
