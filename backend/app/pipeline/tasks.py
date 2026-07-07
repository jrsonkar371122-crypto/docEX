"""Celery task orchestrating the full 7-phase ingestion pipeline."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.models.document import Document, DocStatus, DocType, Page
from app.models.job import IngestionJob, JobStatus
from app.pipeline import (
    chunker,
    embedder,
    extractor,
    image_extractor,
    profiler,
    structurer,
    table_extractor,
)
from app.pipeline.worker import SyncSessionLocal, celery_app

logger = logging.getLogger("documind.pipeline.tasks")

_LOG_TAIL_LIMIT = 6000


def _update_job(
    db, job: IngestionJob, *, progress: int | None = None,
    status: JobStatus | None = None, message: str | None = None,
) -> None:
    if progress is not None:
        job.progress_pct = progress
    if status is not None:
        job.status = status
    if message is not None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        job.log_tail = (job.log_tail + "\n" + line)[-_LOG_TAIL_LIMIT:]
        logger.info("[job %s] %s", job.id, message)
    db.commit()


@celery_app.task(name="app.pipeline.tasks.ingest_document", bind=True)
def ingest_document(self, document_id: str, job_id: str) -> dict:
    """Run the full ingestion pipeline for one document."""
    doc_uuid = uuid.UUID(document_id)
    job_uuid = uuid.UUID(job_id)

    with SyncSessionLocal() as db:
        document = db.execute(
            select(Document).where(Document.id == doc_uuid)
        ).scalar_one_or_none()
        job = db.execute(
            select(IngestionJob).where(IngestionJob.id == job_uuid)
        ).scalar_one_or_none()
        if document is None or job is None:
            logger.error("Document or job not found (doc=%s job=%s)", document_id, job_id)
            return {"status": "failed", "reason": "not_found"}

        pdf_path = os.path.join(settings.upload_dir, document.filename)

        try:
            job.celery_task_id = self.request.id
            document.status = DocStatus.processing
            _update_job(db, job, progress=1, status=JobStatus.running,
                        message="Ingestion started.")

            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"Stored PDF missing: {pdf_path}")

            # Phase 0 — profiling.
            profile = profiler.profile_pdf(pdf_path)
            document.page_count = profile.page_count
            document.doc_type = DocType(profile.doc_type)
            _update_job(db, job, progress=5,
                        message=f"Profiled: {profile.doc_type}, {profile.page_count} pages.")

            # Phase 1 — text + OCR/VLM extraction.
            text_elems, raw_text_by_page, ocr_pages = extractor.extract_text(
                pdf_path, profile
            )
            _update_job(db, job, progress=25, message="Text extracted.")

            # Phase 2 — tables.
            table_elems = table_extractor.extract_tables(pdf_path)
            _update_job(db, job, progress=50,
                        message=f"Tables extracted on {len(table_elems)} page(s).")

            # Phase 3 — images (generic VLM descriptions).
            image_elems = image_extractor.extract_images(pdf_path, document_id)
            _update_job(db, job, progress=70,
                        message=f"Images described on {len(image_elems)} page(s).")

            # Persist pages now that we know per-page flags.
            page_id_by_number = _persist_pages(
                db, doc_uuid, profile.page_count, raw_text_by_page,
                table_elems, image_elems, ocr_pages,
            )

            # Phase 4 — structure tree.
            tree = structurer.build_tree(
                text_elems, table_elems, image_elems, profile.page_count
            )
            _update_job(db, job, progress=80, message="Document structure built.")

            # Phase 5 — chunking.
            built_chunks = chunker.chunk_document(tree)
            _update_job(db, job, progress=90,
                        message=f"Chunked into {len(built_chunks)} chunk(s).")

            # Phase 6 — embed + store.
            stored = embedder.embed_and_store(
                db, doc_uuid, built_chunks, page_id_by_number
            )

            document.status = DocStatus.ready
            document.processed_at = datetime.now(timezone.utc)
            job.finished_at = datetime.now(timezone.utc)
            _update_job(db, job, progress=100, status=JobStatus.done,
                        message=f"Done. {stored} chunk(s) embedded and stored.")
            return {"status": "done", "chunks": stored}

        except Exception as exc:  # noqa: BLE001
            db.rollback()
            document = db.execute(
                select(Document).where(Document.id == doc_uuid)
            ).scalar_one_or_none()
            job = db.execute(
                select(IngestionJob).where(IngestionJob.id == job_uuid)
            ).scalar_one_or_none()
            if document is not None:
                document.status = DocStatus.failed
                document.error_message = str(exc)[:2000]
            if job is not None:
                job.finished_at = datetime.now(timezone.utc)
                _update_job(db, job, status=JobStatus.failed,
                            message=f"FAILED: {exc}")
            logger.exception("Ingestion failed for %s", document_id)
            return {"status": "failed", "reason": str(exc)}


def _persist_pages(
    db,
    document_id: uuid.UUID,
    page_count: int,
    raw_text_by_page: dict[int, str],
    table_elems: dict[int, list],
    image_elems: dict[int, list],
    ocr_pages: set[int],
) -> dict[int, uuid.UUID]:
    """Create Page rows and return a page_number → page_id map."""
    page_id_by_number: dict[int, uuid.UUID] = {}
    for page_number in range(1, page_count + 1):
        page = Page(
            id=uuid.uuid4(),
            document_id=document_id,
            page_number=page_number,
            raw_text=raw_text_by_page.get(page_number, ""),
            has_table=page_number in table_elems,
            has_image=page_number in image_elems,
            is_ocr=page_number in ocr_pages,
        )
        db.add(page)
        page_id_by_number[page_number] = page.id
    db.flush()
    return page_id_by_number
