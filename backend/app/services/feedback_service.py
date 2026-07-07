"""Feedback persistence and admin retrieval helpers."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat import ChatMessage, Feedback
from app.models.user import User
from app.schemas.admin import FeedbackAdminOut


async def submit_feedback(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: int,
    comment: str | None,
) -> Feedback:
    """Insert or update the caller's feedback for a message."""
    existing = (
        await db.execute(
            select(Feedback).where(
                Feedback.message_id == message_id, Feedback.user_id == user_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.rating = rating
        existing.comment = comment
        await db.flush()
        return existing

    fb = Feedback(
        message_id=message_id,
        user_id=user_id,
        rating=rating,
        comment=comment,
    )
    db.add(fb)
    await db.flush()
    return fb


async def list_feedback(db: AsyncSession) -> list[FeedbackAdminOut]:
    """All feedback joined with message preview and username, newest first."""
    stmt = (
        select(Feedback)
        .options(selectinload(Feedback.message), selectinload(Feedback.message))
        .order_by(Feedback.created_at.desc())
    )
    feedbacks = (await db.execute(stmt)).scalars().all()

    out: list[FeedbackAdminOut] = []
    for fb in feedbacks:
        msg: ChatMessage | None = fb.message
        preview = (msg.content[:80] if msg and msg.content else "").strip()
        user = (
            await db.execute(select(User).where(User.id == fb.user_id))
        ).scalar_one_or_none()
        username = user.full_name if user else "(unknown)"
        out.append(
            FeedbackAdminOut(
                id=fb.id,
                message_id=fb.message_id,
                message_preview=preview,
                rating=fb.rating,
                comment=fb.comment,
                username=username,
                created_at=fb.created_at,
            )
        )
    return out
