from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, update

from xiaohe.database import database_now
from xiaohe.events import record_event
from xiaohe.jobs import cancel_run_job
from xiaohe.models import ArtifactVersion, Command, Conversation, Event, Run, RunAttempt, Session
from xiaohe.runs import TERMINAL


async def expire_guests(db, now: datetime, limit: int) -> list[UUID]:
    protected = exists(
        select(Conversation.id).where(
            Conversation.guest_id == Session.guest_id, Conversation.merge_pending.is_(True)
        )
    )
    notified = exists(
        select(Event.id)
        .join(Conversation)
        .where(Conversation.guest_id == Session.guest_id, Event.event_type == "viewer.expired")
    )
    active = exists(select(Run.id).where(Run.session_id == Session.id, Run.status.not_in(TERMINAL)))
    sessions = (
        await db.scalars(
            select(Session)
            .where(
                Session.guest_id.is_not(None),
                Session.revoked_at.is_(None),
                Session.expires_at <= now,
                Session.expires_at <= func.clock_timestamp(),
                ~protected,
                (~notified | active),
            )
            .order_by(Session.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    expired = []
    for session in sessions:
        conversations = (
            await db.scalars(
                select(Conversation)
                .where(
                    Conversation.guest_id == session.guest_id,
                    Conversation.system_id == session.system_id,
                    Conversation.tenant_id == session.tenant_id,
                )
                .order_by(Conversation.id)
                .with_for_update()
            )
        ).all()
        if any(c.merge_pending or c.user_id is not None for c in conversations):
            continue
        if session.revoked_at is not None or await database_now(db) < session.expires_at:
            continue
        for conversation in conversations:
            runs = (
                await db.scalars(
                    select(Run)
                    .where(
                        Run.conversation_id == conversation.id,
                        Run.status.not_in(TERMINAL),
                    )
                    .order_by(Run.id)
                    .with_for_update()
                )
            ).all()
            for run in runs:
                run.status, run.holds_slot = "expired", False
                await db.execute(
                    update(Command)
                    .where(
                        Command.run_id == run.id,
                        Command.completed_at.is_(None),
                    )
                    .values(completed_at=func.clock_timestamp())
                )
                await db.execute(
                    update(RunAttempt)
                    .where(
                        RunAttempt.run_id == run.id,
                        RunAttempt.status == "running",
                    )
                    .values(status="expired")
                )
                await db.execute(
                    update(ArtifactVersion)
                    .where(
                        ArtifactVersion.run_id == run.id,
                        ArtifactVersion.status == "pending",
                    )
                    .values(status="failed", cleanup_pending=True)
                )
                if run.job_id is not None:
                    await cancel_run_job(db, run.job_id)
                record_event(
                    db,
                    conversation,
                    "run.updated",
                    {"run": {"id": str(run.id), "status": "expired"}},
                    run.id,
                )
            if not await db.scalar(
                select(Event.id)
                .where(
                    Event.conversation_id == conversation.id,
                    Event.event_type == "viewer.expired",
                )
                .limit(1)
            ):
                record_event(db, conversation, "viewer.expired", {"code": "VISITOR_EXPIRED"})
        expired.append(session.guest_id)
    await db.flush()
    return expired
