"""Hybrid retrieval: pgvector cosine → BM25 re-rank → MMR diversity."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Chunk, Document
from app.services import llm_service

logger = logging.getLogger("documind.retrieval")

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    document_id: uuid.UUID
    content: str
    section_path: str
    page_number: int | None
    doc_name: str
    has_table: bool
    has_image: bool
    vector_score: float
    embedding: list[float]


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-10
    return float(np.dot(a, b) / denom)


async def vector_search(
    db: AsyncSession, query_embedding: list[float], top_k: int
) -> list[RetrievedChunk]:
    """Top-k chunks by pgvector cosine distance."""
    distance = Chunk.embedding.cosine_distance(query_embedding).label("distance")
    stmt = (
        select(Chunk, Document.original_name, distance)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.embedding.isnot(None))
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await db.execute(stmt)).all()

    results: list[RetrievedChunk] = []
    for chunk, doc_name, dist in rows:
        page_number = None
        if chunk.page is not None:
            page_number = chunk.page.page_number
        results.append(
            RetrievedChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                content=chunk.content,
                section_path=chunk.section_path,
                page_number=page_number,
                doc_name=doc_name,
                has_table=chunk.has_table,
                has_image=chunk.has_image,
                vector_score=1.0 - float(dist),
                embedding=list(chunk.embedding),
            )
        )
    return results


def bm25_rerank(
    query: str, candidates: list[RetrievedChunk]
) -> list[tuple[RetrievedChunk, float]]:
    """Re-rank candidates by BM25 over their content."""
    if not candidates:
        return []
    corpus = [_tokenize(c.content) for c in candidates]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(query))
    max_score = float(scores.max()) if len(scores) else 0.0
    norm = [(s / max_score) if max_score > 0 else 0.0 for s in scores]
    ranked = sorted(zip(candidates, norm), key=lambda x: x[1], reverse=True)
    return ranked


def mmr_select(
    query_embedding: list[float],
    ranked: list[tuple[RetrievedChunk, float]],
    top_k: int,
    lambda_mult: float,
) -> list[RetrievedChunk]:
    """Maximal Marginal Relevance selection for diversity."""
    if not ranked:
        return []
    q = np.asarray(query_embedding, dtype=np.float32)
    pool = list(ranked)
    embeddings = {c.id: np.asarray(c.embedding, dtype=np.float32) for c, _ in pool}
    relevance = {c.id: _cosine(q, embeddings[c.id]) for c, _ in pool}

    selected: list[RetrievedChunk] = []
    remaining = [c for c, _ in pool]

    while remaining and len(selected) < top_k:
        best_chunk = None
        best_score = -1e9
        for cand in remaining:
            if not selected:
                diversity = 0.0
            else:
                diversity = max(
                    _cosine(embeddings[cand.id], embeddings[s.id]) for s in selected
                )
            score = lambda_mult * relevance[cand.id] - (1 - lambda_mult) * diversity
            if score > best_score:
                best_score = score
                best_chunk = cand
        if best_chunk is None:
            break
        selected.append(best_chunk)
        remaining.remove(best_chunk)
    return selected


async def hybrid_search(db: AsyncSession, query: str) -> list[RetrievedChunk]:
    """Full pipeline: embed → vector top-N → BM25 → MMR → final top-k."""
    query_embedding = await llm_service.embed_text(query)
    candidates = await vector_search(
        db, query_embedding, settings.retrieval_vector_topk
    )
    if not candidates:
        return []
    ranked = bm25_rerank(query, candidates)
    final = mmr_select(
        query_embedding,
        ranked,
        settings.retrieval_final_topk,
        settings.mmr_lambda,
    )
    return final
