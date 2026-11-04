from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select, text, update

from xiaohe.database import database_now
from xiaohe.events import record_event
from xiaohe.jobs import cancel_run_job
from xiaohe.models import (
    Artifact,
    ArtifactVersion,
    Command,
    Conversation,
    Event,
    EventTombstone,
    GuestClaim,
    GuestObjectTombstone,
    Message,
    Run,
    RunAttempt,
    Session,
)
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
    claimed = exists(
        select(GuestClaim.id).where(
            GuestClaim.guest_id == Session.guest_id, GuestClaim.status.in_(("pending", "merged"))
        )
    )
    sessions = (
        await db.scalars(
            select(Session)
            .where(
                Session.guest_id.is_not(None),
                Session.purged_at.is_(None),
                Session.merge_operation_id.is_(None),
                Session.expires_at <= now,
                Session.expires_at <= func.clock_timestamp(),
                ~protected,
                ~claimed,
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
        if session.merge_operation_id is not None or await database_now(db) < session.expires_at:
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


async def _delete_guest_bodies(db, conversation_ids, run_ids):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    threads = set(
        await db.scalars(
            select(Conversation.thread_id).where(Conversation.id.in_(conversation_ids))
        )
    )
    threads.update(await db.scalars(select(Run.thread_id).where(Run.id.in_(run_ids))))
    threads.update(
        await db.scalars(select(RunAttempt.thread_id).where(RunAttempt.run_id.in_(run_ids)))
    )
    if await db.scalar(text("SELECT to_regclass('public.checkpoints')")):
        raw = await (await db.connection()).get_raw_connection()
        saver = AsyncPostgresSaver(raw.driver_connection)
        for thread_id in sorted(threads - {None}):
            await saver.adelete_thread(thread_id)
    private_events = (
        Event.conversation_id.in_(conversation_ids),
        Event.event_type != "viewer.expired",
    )
    for event in await db.scalars(select(Event).where(*private_events)):
        db.add(
            EventTombstone(
                id=event.id,
                conversation_id=event.conversation_id,
                system_id=event.system_id,
                tenant_id=event.tenant_id,
                sequence=event.sequence,
            )
        )
    await db.flush()
    await db.execute(delete(Event).where(*private_events))
    for model in (Command, RunAttempt):
        await db.execute(delete(model).where(model.conversation_id.in_(conversation_ids)))
    artifact_ids = select(Artifact.id).where(Artifact.conversation_id.in_(conversation_ids))
    await db.execute(delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_ids)))
    await db.execute(delete(Artifact).where(Artifact.conversation_id.in_(conversation_ids)))
    await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))


async def purge_expired_guest(factory, guest_id: UUID, artifacts) -> bool:
    async with factory.begin() as db:
        session = await db.scalar(
            select(Session).where(Session.guest_id == guest_id).with_for_update(skip_locked=True)
        )
        if session is None or await database_now(db) < session.expires_at:
            return False
        if session.purged_at is not None:
            return True
        session.cleanup_checked_at = await database_now(db)
        protected = await db.scalar(
            select(GuestClaim.id)
            .where(GuestClaim.guest_id == guest_id, GuestClaim.status.in_(("pending", "merged")))
            .limit(1)
        )
        if protected or session.merge_operation_id is not None:
            return False
        conversations = (
            await db.scalars(
                select(Conversation)
                .where(
                    Conversation.guest_id == guest_id,
                    Conversation.system_id == session.system_id,
                    Conversation.tenant_id == session.tenant_id,
                )
                .order_by(Conversation.id)
                .with_for_update()
            )
        ).all()
        if any(c.user_id is not None or c.merge_pending for c in conversations):
            return False
        conversation_ids = [c.id for c in conversations]
        runs = (
            await db.scalars(
                select(Run)
                .where(Run.conversation_id.in_(conversation_ids))
                .order_by(Run.id)
                .with_for_update()
            )
        ).all()
        if any(r.status not in TERMINAL or r.user_id is not None for r in runs):
            return False
        run_ids = [r.id for r in runs]
        threads = {c.thread_id for c in conversations} | {r.thread_id for r in runs}
        threads.update(
            await db.scalars(select(RunAttempt.thread_id).where(RunAttempt.run_id.in_(run_ids)))
        )
        keys = ["xiaohe.run:" + str(r.id) for r in runs]
        keys += ["xiaohe.thread:" + t for t in sorted(threads - {None})]
        versions = (
            await db.scalars(
                select(ArtifactVersion)
                .join(Artifact)
                .where(Artifact.conversation_id.in_(conversation_ids))
                .order_by(ArtifactVersion.id)
                .with_for_update(of=ArtifactVersion)
            )
        ).all()
        keys += ["xiaohe.upload:" + str(v.id) for v in versions]
        for key in keys:
            if not await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
            ):
                return False
        session.cleanup_stopped_at = session.cleanup_stopped_at or await database_now(db)
        for version in versions:
            # The session/conversation locks prevent claim ownership changes during deletion.
            if artifacts is None:
                return False
            db.add(
                GuestObjectTombstone(
                    object_key=version.object_key,
                    guest_id=guest_id,
                    system_id=session.system_id,
                    tenant_id=session.tenant_id,
                )
            )
            await artifacts.delete_expired_object(version.object_key)
        await _delete_guest_bodies(db, conversation_ids, run_ids)
        session.purged_at = await database_now(db)
        return True


async def purge_expired_guests(factory, artifacts, limit=100):
    async with factory() as db:
        guests = (
            await db.scalars(
                select(Session.guest_id)
                .where(
                    Session.guest_id.is_not(None),
                    Session.purged_at.is_(None),
                    Session.merge_operation_id.is_(None),
                    Session.expires_at <= func.clock_timestamp(),
                    ~exists(
                        select(GuestClaim.id).where(
                            GuestClaim.guest_id == Session.guest_id,
                            GuestClaim.status.in_(("pending", "merged")),
                        )
                    ),
                )
                .order_by(Session.cleanup_checked_at.asc().nulls_first(), Session.id)
                .limit(limit)
            )
        ).all()
    for guest_id in guests:
        try:
            await purge_expired_guest(factory, guest_id, artifacts)
        except Exception:
            # A failed object/DB operation keeps its durable rows for the next scan.
            async with factory.begin() as db:
                await db.execute(
                    update(Session)
                    .where(Session.guest_id == guest_id)
                    .values(cleanup_checked_at=func.clock_timestamp())
                )
    if artifacts is not None:
        await _reap_late_objects(factory, artifacts, limit)


async def _reap_late_objects(factory, artifacts, limit):
    async with factory() as db:
        keys = (
            await db.scalars(
                select(GuestObjectTombstone.object_key)
                .order_by(
                    GuestObjectTombstone.checked_at.asc().nulls_first(),
                    GuestObjectTombstone.object_key,
                )
                .limit(limit)
            )
        ).all()
    for key in keys:
        try:
            async with factory.begin() as db:
                locator = await db.get(GuestObjectTombstone, key)
                session = await db.scalar(
                    select(Session)
                    .where(Session.guest_id == locator.guest_id)
                    .with_for_update(skip_locked=True)
                )
                if session is None or session.purged_at is None:
                    continue
                protected = await db.scalar(
                    select(GuestClaim.id)
                    .where(
                        GuestClaim.guest_id == session.guest_id,
                        GuestClaim.status.in_(("pending", "merged")),
                    )
                    .limit(1)
                )
                if protected or session.merge_operation_id is not None:
                    continue
                receipt = await db.scalar(
                    select(GuestObjectTombstone)
                    .where(GuestObjectTombstone.object_key == key)
                    .with_for_update(skip_locked=True)
                )
                if receipt is None:
                    continue
                await artifacts.delete_expired_object(key)
                receipt.checked_at = await database_now(db)
        except Exception:
            async with factory.begin() as db:
                await db.execute(
                    update(GuestObjectTombstone)
                    .where(GuestObjectTombstone.object_key == key)
                    .values(checked_at=func.clock_timestamp())
                )
