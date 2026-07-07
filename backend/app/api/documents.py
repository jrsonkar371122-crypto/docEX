"""Read-only document endpoints available to any authenticated user."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.document import Chunk, Document
from app.models.user import User
from app.schemas.document import DocumentDetailOut, DocumentOut, PageOut

router = APIRouter()


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Document]:
    stmt = select(Document).order_by(Document.uploaded_at.desc())
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> DocumentDetailOut:
    doc = (
        await db.execute(
            select(Document)
            .where(Document.id == document_id)
            .options(selectinload(Document.pages))
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    chunk_count = (
        await db.execute(
            select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        )
    ).scalar_one()

    return DocumentDetailOut(
        id=doc.id,
        filename=doc.filename,
        original_name=doc.original_name,
        file_size_bytes=doc.file_size_bytes,
        page_count=doc.page_count,
        doc_type=doc.doc_type,
        status=doc.status,
        uploaded_by=doc.uploaded_by,
        uploaded_at=doc.uploaded_at,
        processed_at=doc.processed_at,
        error_message=doc.error_message,
        chunk_count=chunk_count,
        pages=[PageOut.model_validate(p) for p in sorted(doc.pages, key=lambda p: p.page_number)],
    )
