"""Chat endpoints: session CRUD and SSE streaming message generation."""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.user import User
from app.schemas.chat import (
    MessageRequest,
    SessionDetailOut,
    SessionOut,
    SessionTitleUpdate,
)
from app.services import context_builder, llm_service
from app.services.retrieval import RetrievedChunk, hybrid_search

router = APIRouter()


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatSession]:
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatSession:
    session = await _owned_session(db, session_id, current_user.id, with_messages=True)
    return session


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: uuid.UUID,
    payload: SessionTitleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatSession:
    session = await _owned_session(db, session_id, current_user.id)
    session.title = payload.title.strip()
    await db.flush()
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    session = await _owned_session(db, session_id, current_user.id)
    await db.execute(delete(ChatSession).where(ChatSession.id == session.id))


@router.post("/message")
async def send_message(
    payload: MessageRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream an assistant answer over SSE and persist it on completion."""
    return StreamingResponse(
        _generate(payload, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _owned_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    with_messages: bool = False,
) -> ChatSession:
    stmt = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == user_id
    )
    if with_messages:
        stmt = stmt.options(selectinload(ChatSession.messages))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )
    return session


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _sources_payload(chunks: list[RetrievedChunk]) -> list[dict]:
    payload = []
    for c in chunks:
        payload.append(
            {
                "id": str(c.id),
                "section_path": c.section_path,
                "page": c.page_number,
                "preview": c.content.strip()[:120],
                "doc_name": c.doc_name,
            }
        )
    return payload


async def _generate(
    payload: MessageRequest, user_id: uuid.UUID
) -> AsyncGenerator[str, None]:
    """SSE generator. Uses its own DB session (independent of request scope)."""
    start = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            # Resolve or create the session.
            if payload.session_id is not None:
                session = (
                    await db.execute(
                        select(ChatSession).where(
                            ChatSession.id == payload.session_id,
                            ChatSession.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if session is None:
                    yield _sse({"type": "error", "message": "Session not found."})
                    return
            else:
                session = ChatSession(user_id=user_id, title="New Chat")
                db.add(session)
                await db.flush()

            # Load recent history (before adding the new turn).
            history = list(
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session.id)
                        .order_by(ChatMessage.created_at)
                    )
                ).scalars().all()
            )
            history = context_builder.trim_history(history)

            # Persist the user's message.
            user_msg = ChatMessage(
                session_id=session.id,
                role=MessageRole.user,
                content=payload.content,
            )
            db.add(user_msg)
            is_first = len(history) == 0
            await db.flush()

            # Retrieval + context assembly.
            search_query = context_builder.resolve_query(payload.content, history)
            chunks = await hybrid_search(db, search_query)
            prompt = context_builder.build_rag_prompt(payload.content, chunks, history)

            # Emit sources first so the UI can render the panel immediately.
            sources = _sources_payload(chunks)
            yield _sse({"type": "sources", "chunks": sources})

            # Stream tokens from the LLM.
            answer_parts: list[str] = []
            async for token in llm_service.chat_stream(
                prompt,
                model=settings.llm_model,
                temperature=settings.rag_temperature,
            ):
                answer_parts.append(token)
                yield _sse({"type": "token", "content": token})

            answer = "".join(answer_parts).strip()
            latency_ms = int((time.monotonic() - start) * 1000)

            assistant_msg = ChatMessage(
                session_id=session.id,
                role=MessageRole.assistant,
                content=answer,
                source_chunks=sources,
                latency_ms=latency_ms,
            )
            db.add(assistant_msg)

            # Auto-title from the first user message.
            if is_first:
                session.title = await _make_title(payload.content)

            await db.commit()

            yield _sse(
                {
                    "type": "done",
                    "message_id": str(assistant_msg.id),
                    "session_id": str(session.id),
                    "latency_ms": latency_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            yield _sse({"type": "error", "message": "Generation failed."})
            # Surface details in server logs only.
            import logging

            logging.getLogger("documind.chat").exception("SSE generation error: %s", exc)


async def _make_title(first_message: str) -> str:
    """Auto-generate a short session title (fallback: first 6 words)."""
    try:
        prompt = context_builder.build_title_prompt(first_message)
        title = await llm_service.chat(
            prompt,
            model=settings.llm_fallback_model,
            temperature=settings.title_temperature,
            max_tokens=32,
        )
        title = title.strip().strip('"').splitlines()[0] if title else ""
        if title:
            return title[:120]
    except Exception:  # noqa: BLE001
        pass
    words = first_message.split()
    return " ".join(words[:6]) or "New Chat"
