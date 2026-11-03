from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import Actor, ChatMessage, RunRef, ServiceError, WriteFence
from xiaohe.conversations import require_conversation
from xiaohe.database import database_now
from xiaohe.events import record_event
from xiaohe.models import Command, Conversation, Message, Run, RunAttempt, Session, System, User

TERMINAL = {"completed", "failed", "cancelled", "expired"}


def reply_id(run_id: UUID, command_id: UUID) -> UUID:
    return uuid5(run_id, str(command_id))


async def _locked(db: AsyncSession, run_id: UUID):
    row = await db.scalar(
        select(Run).where(Run.id == run_id).execution_options(populate_existing=True)
    )
    if row is None:
        raise ServiceError("FORBIDDEN", 403)
    binding = (
        row.session_id,
        row.conversation_id,
        row.system_id,
        row.tenant_id,
        row.user_id,
        row.guest_id,
    )
    session = None
    if row.guest_id is not None:
        session = await db.scalar(
            select(Session)
            .where(Session.id == row.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == row.conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = await db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if binding != (
        row.session_id,
        row.conversation_id,
        row.system_id,
        row.tenant_id,
        row.user_id,
        row.guest_id,
    ):
        raise ServiceError("WRITE_FENCE_STALE", 409)
    return row, conversation, session


async def _authorized(
    db: AsyncSession, row: Run, conversation: Conversation, session: Session | None
):
    if (row.system_id, row.tenant_id, row.user_id, row.guest_id) != (
        conversation.system_id,
        conversation.tenant_id,
        conversation.user_id,
        conversation.guest_id,
    ) or row.ownership_epoch != conversation.ownership_epoch:
        raise ServiceError("WRITE_FENCE_STALE", 409)
    enabled = await db.scalar(
        select(System.enabled).where(
            System.system_id == row.system_id, System.tenant_id == row.tenant_id
        )
    )
    if enabled is not True:
        raise ServiceError("FORBIDDEN", 403)
    stamp = await database_now(db)
    if stamp >= row.execution_expires_at:
        raise ServiceError("EXECUTION_EXPIRED", 410)
    if row.guest_id is not None:
        if (
            session is None
            or session.guest_id != row.guest_id
            or session.revoked_at is not None
            or stamp >= session.expires_at
        ):
            raise ServiceError("EXECUTION_EXPIRED", 410)
    elif (
        await db.scalar(
            select(User.enabled).where(
                User.user_id == row.user_id,
                User.system_id == row.system_id,
                User.tenant_id == row.tenant_id,
            )
        )
        is not True
    ):
        raise ServiceError("FORBIDDEN", 403)


def _status_event(db, conversation, row):
    record_event(
        db, conversation, "run.updated", {"run": {"id": str(row.id), "status": row.status}}, row.id
    )


async def acquire_run(
    db: AsyncSession, run_id: UUID, *, recover: bool = False
) -> WriteFence | None:
    row, conversation, session = await _locked(db, run_id)
    if (
        row.status in TERMINAL
        or row.status == "waiting_input"
        or (row.status == "running" and not recover)
    ):
        return None
    try:
        await _authorized(db, row, conversation, session)
    except ServiceError as error:
        row.status = "expired" if error.code == "EXECUTION_EXPIRED" else "failed"
        row.holds_slot = False
        _status_event(db, conversation, row)
        return None
    command = await db.scalar(
        select(Command)
        .where(Command.run_id == row.id, Command.completed_at.is_(None))
        .with_for_update()
    )
    if command is None or command.attempts >= 3:
        row.status, row.holds_slot = "failed", False
        _status_event(db, conversation, row)
        if command is not None:
            command.completed_at = await database_now(db)
        return None
    if row.attempt:
        await db.execute(
            update(RunAttempt)
            .where(RunAttempt.run_id == row.id, RunAttempt.attempt == row.attempt)
            .values(status="abandoned")
        )
    row.attempt += 1
    command.attempts += 1
    row.status, row.holds_slot, row.outcome = "running", True, None
    if row.thread_id is None:
        row.thread_id = str(uuid4())
    conversation.thread_id = row.thread_id
    db.add(
        RunAttempt(
            run_id=row.id,
            attempt=row.attempt,
            conversation_id=row.conversation_id,
            system_id=row.system_id,
            tenant_id=row.tenant_id,
            thread_id=row.thread_id,
        )
    )
    existing = await db.get(Message, reply_id(row.id, command.id))
    if existing is not None:
        existing.parts = []
        record_event(
            db,
            conversation,
            "message.upsert",
            {
                "message": ChatMessage(
                    id=str(existing.id), role="assistant", created_at=existing.created_at, parts=[]
                ).model_dump(mode="json", by_alias=True)
            },
            row.id,
        )
    _status_event(db, conversation, row)
    await db.flush()
    return WriteFence(run_id=row.id, attempt=row.attempt, ownership_epoch=row.ownership_epoch)


async def require_write(db: AsyncSession, fence: WriteFence) -> Run:
    row, conversation, session = await _locked(db, fence.run_id)
    if (
        row.attempt != fence.attempt
        or row.ownership_epoch != fence.ownership_epoch
        or row.status != "running"
    ):
        raise ServiceError("WRITE_FENCE_STALE", 409)
    await _authorized(db, row, conversation, session)
    updated = await db.scalar(
        update(Run)
        .where(
            Run.id == row.id,
            Run.attempt == fence.attempt,
            Run.ownership_epoch == fence.ownership_epoch,
            Run.status == "running",
            Run.execution_expires_at > func.clock_timestamp(),
        )
        .values(updated_at=func.clock_timestamp())
        .returning(Run.id)
        .execution_options(synchronize_session=False)
    )
    if updated is None:
        raise ServiceError("EXECUTION_EXPIRED", 410)
    return row


async def cancel_run(db: AsyncSession, actor: Actor, run_id: UUID) -> RunRef:
    row = await db.get(Run, run_id)
    if row is None:
        raise ServiceError("FORBIDDEN", 403)
    conversation = await require_conversation(db, actor, row.conversation_id)
    row = await db.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row.status not in TERMINAL:
        row.status, row.holds_slot = "cancelled", False
        row.ownership_epoch += 1
        await db.execute(
            update(Command)
            .where(Command.run_id == row.id, Command.completed_at.is_(None))
            .values(completed_at=func.clock_timestamp())
        )
        _status_event(db, conversation, row)
    return RunRef(id=str(row.id), status=row.status)
