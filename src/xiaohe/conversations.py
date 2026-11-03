from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import (
    Actor,
    ChatMessage,
    ChatSnapshot,
    GuestViewer,
    RunRef,
    ServiceError,
    UserViewer,
    Viewer,
)
from xiaohe.identity import CSRF_HEADER, require_session
from xiaohe.models import Conversation, Event, Message, Project, Run, User


async def default_conversation(db: AsyncSession, actor: Actor) -> Conversation:
    await require_session(db, actor)
    if actor.user_id is not None:
        await db.scalar(select(User).where(User.user_id == actor.user_id).with_for_update())
        owner = Conversation.user_id == actor.user_id
    else:
        owner = Conversation.guest_id == actor.guest_id
    existing = await db.scalar(
        select(Conversation).where(
            Conversation.system_id == actor.system.system_id,
            Conversation.tenant_id == actor.system.tenant_id,
            Conversation.purpose == "portal",
            owner,
        )
    )
    if existing is not None:
        return existing
    project = None
    if actor.user_id is not None:
        project = await db.scalar(
            select(Project).where(
                Project.system_id == actor.system.system_id,
                Project.user_id == actor.user_id,
                Project.is_default.is_(True),
            )
        )
        if project is None:
            project = Project(
                system_id=actor.system.system_id,
                tenant_id=actor.system.tenant_id,
                user_id=actor.user_id,
                is_default=True,
            )
            db.add(project)
            await db.flush()
    record = Conversation(
        system_id=actor.system.system_id,
        tenant_id=actor.system.tenant_id,
        user_id=actor.user_id,
        guest_id=actor.guest_id,
        project_id=project.id if project else None,
    )
    db.add(record)
    await db.flush()
    return record


async def require_conversation(
    db: AsyncSession, actor: Actor, conversation_id: UUID
) -> Conversation:
    await require_session(db, actor)
    owner = (
        Conversation.user_id == actor.user_id
        if actor.user_id is not None
        else Conversation.guest_id == actor.guest_id
    )
    record = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.system_id == actor.system.system_id,
            Conversation.tenant_id == actor.system.tenant_id,
            owner,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise ServiceError("FORBIDDEN", 403)
    await require_session(db, actor)
    return record


async def load_snapshot(db: AsyncSession, actor: Actor, conversation_id: UUID) -> ChatSnapshot:
    conversation = await require_conversation(db, actor, conversation_id)
    messages = (
        await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.system_id == actor.system.system_id,
                Message.tenant_id == actor.system.tenant_id,
            )
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    session = await require_session(db, actor)
    viewer = await load_viewer(db, actor)
    active = await db.scalar(
        select(Run)
        .where(
            Run.conversation_id == conversation.id,
            Run.status.in_(("queued", "running", "waiting_input")),
        )
        .order_by(Run.holds_slot.desc(), Run.updated_at.desc(), Run.id)
        .limit(1)
    )
    cursor = await db.scalar(
        select(Event.id)
        .where(Event.conversation_id == conversation.id)
        .order_by(Event.sequence.desc())
        .limit(1)
    )
    return ChatSnapshot(
        viewer=viewer,
        csrf={"headerName": CSRF_HEADER, "token": session.csrf_token},
        conversation_id=str(conversation.id),
        messages=[
            ChatMessage(
                id=str(row.id),
                role=row.role,
                created_at=row.created_at,
                parts=row.parts,
            )
            for row in messages
        ],
        active_run=RunRef(id=str(active.id), status=active.status) if active else None,
        cursor=str(cursor) if cursor else None,
        merge=None,
    )


async def load_viewer(db: AsyncSession, actor: Actor) -> Viewer:
    session = await require_session(db, actor)
    if actor.user_id is None:
        viewer = GuestViewer(
            kind="guest",
            guest_id=str(actor.guest_id),
            system_id=actor.system.system_id,
            tenant_id=actor.system.tenant_id,
            message_count=session.message_count,
            message_limit=session.message_limit,
            retention_seconds=session.retention_seconds,
            expires_at=session.expires_at,
        )
    else:
        user = await db.scalar(select(User).where(User.user_id == actor.user_id))
        viewer = UserViewer(
            kind="user",
            user_id=user.user_id,
            system_id=actor.system.system_id,
            tenant_id=actor.system.tenant_id,
            display_name=user.display_name,
        )
    return viewer
