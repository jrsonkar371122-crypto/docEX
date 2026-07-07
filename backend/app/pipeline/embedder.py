"""Phase 6 — embed chunks with nomic-embed-text and upsert into pgvector."""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import Chunk
from app.pipeline import ollama_sync
from app.pipeline.chunker import BuiltChunk

logger = logging.getLogger("documind.pipeline.embedder")

_BATCH_SIZE = 32
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.5


def _embed_with_retry(text: str) -> list[float]:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return ollama_sync.embed(text)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = _BASE_BACKOFF ** attempt
            logger.warning(
                "Embedding failed (%d/%d): %s — retry in %.1fs",
                attempt, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Embedding failed after {_MAX_RETRIES} attempts: {last_exc}")


def embed_and_store(
    db: Session,
    document_id: uuid.UUID,
    built_chunks: list[BuiltChunk],
    page_id_by_number: dict[int, uuid.UUID],
) -> int:
    """Embed each chunk and persist it. Returns the number of chunks stored."""
    stored = 0
    for batch_start in range(0, len(built_chunks), _BATCH_SIZE):
        batch = built_chunks[batch_start : batch_start + _BATCH_SIZE]
        for local_ix, bc in enumerate(batch):
            embedding = _embed_with_retry(bc.content)
            if len(embedding) != settings.embedding_dim:
                logger.warning(
                    "Embedding dim mismatch: got %d expected %d",
                    len(embedding), settings.embedding_dim,
                )
            primary_page = bc.page_numbers[0] if bc.page_numbers else None
            page_id = page_id_by_number.get(primary_page) if primary_page else None
            chunk = Chunk(
                id=uuid.uuid4(),
                document_id=document_id,
                page_id=page_id,
                chunk_index=batch_start + local_ix,
                content=bc.content,
                section_path=bc.section_path,
                has_table=bc.has_table,
                has_image=bc.has_image,
                embedding=embedding,
                token_count=bc.token_count,
            )
            db.add(chunk)
            stored += 1
        db.flush()
    return stored
