"""Conversations and the streaming agent turn."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import schemas
from app.agents.runtime import run_turn
from app.agents.skills import SkillContext
from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.db.base import SessionLocal
from app.db.models import ChatMessage, ChatSession, DocumentTemplate, FileAsset, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

HISTORY_LIMIT = 24


@router.get("/sessions", response_model=list[schemas.SessionOut])
async def list_sessions(user: CurrentUser, db: DbSession) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.owner_id == user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    return list(result.scalars().all())


@router.get("/sessions/{session_id}", response_model=schemas.SessionDetail)
async def get_session(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ChatSession:
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None or session.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_session(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
    if session is None or session.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await db.delete(session)
    await db.commit()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/stream")
async def stream(
    payload: schemas.ChatRequest, user: CurrentUser, db: DbSession
) -> StreamingResponse:
    """Everything that can fail with a status code happens before the stream opens."""
    if not settings.azure_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Azure OpenAI is not configured. Set AZURE_OPENAI_API_KEY and "
            "AZURE_OPENAI_ENDPOINT, or run the mock stack.",
        )

    session = await _resolve_session(db, payload, user)
    ctx = await _build_context(db, payload, user)
    history = await _load_history(db, session.id)

    db.add(
        ChatMessage(
            session_id=session.id,
            role="user",
            content=payload.message,
            mode=payload.mode,
            attachments=[
                {"id": str(i), "filename": ctx.filenames.get(str(i), "")}
                for i in ctx.attachment_ids
            ],
        )
    )
    await db.commit()

    return StreamingResponse(
        _turn(
            session_id=session.id,
            session_title=session.title,
            ctx=ctx,
            history=history,
            question=payload.message,
            mode=payload.mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _turn(
    *,
    session_id: uuid.UUID,
    session_title: str,
    ctx: SkillContext,
    history: list[AnyMessage],
    question: str,
    mode: str,
) -> AsyncIterator[str]:
    """Runs on its own session: the request-scoped one closes when the response starts."""
    yield _sse({"type": "session", "session_id": str(session_id), "title": session_title})

    answer: list[str] = []
    activity: list[dict] = []
    try:
        async for event in run_turn(ctx=ctx, history=history, question=question):
            if event["type"] == "token":
                answer.append(event["value"])
            else:
                activity.append(event)
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as a feed row
        logger.error("Agent turn failed for session %s: %s", session_id, exc, exc_info=True)
        event = {"type": "error", "message": "An error occurred while processing your request."}
        activity.append(event)
        yield _sse(event)

    content = "".join(answer).strip()
    async with SessionLocal() as db:
        message = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=content,
            mode=mode,
            # Persisted with its trace, so reopening a conversation replays the
            # reasoning feed and not just the answer.
            activity=[e for e in activity if e["type"] != "status"],
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        message_id = str(message.id)

    yield _sse(
        {
            "type": "final",
            "message_id": message_id,
            "session_id": str(session_id),
            "content": content,
        }
    )


async def _resolve_session(
    db: AsyncSession, payload: schemas.ChatRequest, user: User
) -> ChatSession:
    if payload.session_id:
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == payload.session_id))
        ).scalar_one_or_none()
        if session is None or session.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        session.mode = payload.mode
        await db.commit()
        return session

    # The first user message becomes the conversation title.
    title = payload.message.strip().splitlines()[0][:120] if payload.message.strip() else ""
    session = ChatSession(
        owner_id=user.id,
        org_id=user.org_id,
        title=title or "New conversation",
        mode=payload.mode,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def _load_history(db: AsyncSession, session_id: uuid.UUID) -> list[AnyMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    messages = list(reversed(result.scalars().all()))
    return [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in messages
        if m.content
    ]


async def _build_context(
    db: AsyncSession, payload: schemas.ChatRequest, user: User
) -> SkillContext:
    attachment_ids: list[uuid.UUID] = []
    filenames: dict[str, str] = {}
    if payload.attachment_ids:
        result = await db.execute(
            select(FileAsset).where(
                FileAsset.owner_id == user.id, FileAsset.id.in_(payload.attachment_ids)
            )
        )
        assets = list(result.scalars().all())
        filenames = {str(a.id): a.filename for a in assets}
        attachment_ids = [a.id for a in assets]

    templates = (
        (
            await db.execute(
                select(DocumentTemplate).where(
                    or_(
                        DocumentTemplate.is_builtin.is_(True),
                        DocumentTemplate.owner_id == user.id,
                    )
                )
            )
        )
        .scalars()
        .all()
    )

    return SkillContext(
        owner_id=user.id,
        mode=payload.mode,
        attachment_ids=attachment_ids,
        target_attachment_id=payload.target_attachment_id,
        template_key=payload.template_key,
        templates=[
            {
                "key": t.key,
                "name": t.name,
                "standard": t.standard,
                "description": t.description,
                "sections": t.sections,
            }
            for t in templates
        ],
        filenames=filenames,
    )
