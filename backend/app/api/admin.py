"""Admin endpoints: upload, jobs, documents, users, feedback."""
from __future__ import annotations

import os
import uuid

import aiofiles  # type: ignore
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import settings
from app.database import get_db
from app.models.document import Chunk, Document, DocStatus
from app.models.job import IngestionJob, JobStatus
from app.models.user import User
from app.schemas.admin import (
    DocumentAdminOut,
    FeedbackAdminOut,
    JobDetailOut,
    JobOut,
    UploadResponse,
    UserAdminOut,
    UserUpdate,
)
from app.services import feedback_service

router = APIRouter()

_PDF_MAGIC = b"%PDF"


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
    file: UploadFile = File(...),
) -> UploadResponse:
    # Validate declared content type.
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF uploads are accepted.",
        )

    os.makedirs(settings.upload_dir, exist_ok=True)
    doc_id = uuid.uuid4()
    stored_name = f"{doc_id}.pdf"
    stored_path = os.path.join(settings.upload_dir, stored_name)

    # Stream to disk while enforcing the size cap and checking magic bytes.
    size = 0
    first_chunk = True
    async with aiofiles.open(stored_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            if first_chunk:
                if not chunk.startswith(_PDF_MAGIC):
                    await out.close()
                    os.remove(stored_path)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="File is not a valid PDF (magic bytes mismatch).",
                    )
                first_chunk = False
            size += len(chunk)
            if size > settings.upload_limit_bytes:
                await out.close()
                os.remove(stored_path)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
                )
            await out.write(chunk)

    if size == 0:
        os.remove(stored_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file."
        )

    document = Document(
        id=doc_id,
        filename=stored_name,
        original_name=file.filename or stored_name,
        file_size_bytes=size,
        status=DocStatus.queued,
        uploaded_by=admin.id,
    )
    db.add(document)

    job = IngestionJob(document_id=doc_id, status=JobStatus.queued)
    db.add(job)
    await db.flush()

    # Enqueue the Celery ingestion task (import here to avoid a hard dep at import time).
    from app.pipeline.tasks import ingest_document

    async_result = ingest_document.delay(str(doc_id), str(job.id))
    job.celery_task_id = async_result.id
    await db.flush()

    return UploadResponse(
        document_id=doc_id,
        job_id=job.id,
        filename=document.original_name,
        status="queued",
    )


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[JobOut]:
    stmt = (
        select(IngestionJob, Document.original_name)
        .join(Document, Document.id == IngestionJob.document_id)
        .order_by(IngestionJob.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        JobOut(
            id=job.id,
            document_id=job.document_id,
            celery_task_id=job.celery_task_id,
            status=job.status,
            progress_pct=job.progress_pct,
            created_at=job.created_at,
            finished_at=job.finished_at,
            doc_name=doc_name,
        )
        for job, doc_name in rows
    ]


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> JobDetailOut:
    row = (
        await db.execute(
            select(IngestionJob, Document.original_name)
            .join(Document, Document.id == IngestionJob.document_id)
            .where(IngestionJob.id == job_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    job, doc_name = row
    return JobDetailOut(
        id=job.id,
        document_id=job.document_id,
        celery_task_id=job.celery_task_id,
        status=job.status,
        progress_pct=job.progress_pct,
        created_at=job.created_at,
        finished_at=job.finished_at,
        doc_name=doc_name,
        log_tail=job.log_tail,
    )


@router.get("/documents", response_model=list[DocumentAdminOut])
async def list_documents_admin(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[DocumentAdminOut]:
    chunk_counts = dict(
        (
            await db.execute(
                select(Chunk.document_id, func.count(Chunk.id)).group_by(
                    Chunk.document_id
                )
            )
        ).all()
    )
    uploader = User.__table__.alias("uploader")
    stmt = (
        select(Document, uploader.c.full_name)
        .outerjoin(uploader, uploader.c.id == Document.uploaded_by)
        .order_by(Document.uploaded_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        DocumentAdminOut(
            id=doc.id,
            original_name=doc.original_name,
            page_count=doc.page_count,
            chunk_count=chunk_counts.get(doc.id, 0),
            doc_type=doc.doc_type.value,
            status=doc.status.value,
            uploaded_by_name=uploader_name,
            uploaded_at=doc.uploaded_at,
        )
        for doc, uploader_name in rows
    ]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> None:
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )

    # Remove the stored PDF from disk (chunks/pages cascade in the DB).
    stored_path = os.path.join(settings.upload_dir, doc.filename)
    if os.path.exists(stored_path):
        try:
            os.remove(stored_path)
        except OSError:
            pass

    await db.execute(delete(Document).where(Document.id == document_id))


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[User]:
    stmt = select(User).order_by(User.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


@router.patch("/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if user.id == admin.id and payload.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.role is not None:
        user.role = payload.role
    await db.flush()
    await db.refresh(user)
    return user


@router.get("/feedback", response_model=list[FeedbackAdminOut])
async def list_feedback_admin(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[FeedbackAdminOut]:
    return await feedback_service.list_feedback(db)
