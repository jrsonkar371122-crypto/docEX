"""Feedback submission (any authenticated user) and admin listing."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.schemas.admin import FeedbackAdminOut
from app.services import feedback_service

router = APIRouter()


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    payload: FeedbackIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    # Confirm the message exists and belongs to a session owned by the user.
    row = (
        await db.execute(
            select(ChatMessage, ChatSession)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(ChatMessage.id == payload.message_id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found."
        )
    _msg, session = row
    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only rate your own conversations.",
        )

    await feedback_service.submit_feedback(
        db,
        message_id=payload.message_id,
        user_id=current_user.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    return {"detail": "Feedback recorded."}


@router.get("", response_model=list[FeedbackAdminOut])
async def admin_feedback(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[FeedbackAdminOut]:
    return await feedback_service.list_feedback(db)
