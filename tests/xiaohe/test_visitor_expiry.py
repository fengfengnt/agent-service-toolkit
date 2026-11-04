import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from test_admission import send, visitor

from xiaohe.contracts import AgentOutcome, ServiceError
from xiaohe.models import Command, Conversation, Event, Message, Run, RunAttempt, Session
from xiaohe.runs import acquire_run, require_write

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def due(factory, who):
    async with factory.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )


@pytest.mark.parametrize("status", ["queued", "running", "waiting_input"])
async def test_expiry_stops_queued_running_waiting(pg_factory, job_app, status):
    from xiaohe.expiry import expire_guests

    who, conversation_id = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation_id, "first")
    run_id = UUID(accepted.run.id)
    async with pg_factory.begin() as db:
        if status != "queued":
            fence = await acquire_run(db, run_id)
            if status == "waiting_input":
                (await db.get(Run, run_id)).status = "waiting_input"
    await due(pg_factory, who)
    async with pg_factory.begin() as db:
        expired = await expire_guests(db, datetime.now(UTC), 100)
    assert expired == [who.guest_id]
    async with pg_factory.begin() as db:
        row = await db.get(Run, run_id)
        assert row.status == "expired" and not row.holds_slot
        assert (await db.scalar(select(Command))).completed_at is not None
        assert (
            await db.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": row.job_id}
            )
            == "cancelled"
        )
        if status != "queued":
            with pytest.raises(ServiceError):
                await require_write(db, fence)
    async with pg_factory.begin() as db:
        assert await expire_guests(db, datetime.now(UTC), 100) == []
        assert (
            await db.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "viewer.expired")
            )
            == 1
        )


async def test_scans_do_not_expire_live_or_protected_visitors(pg_factory, job_app):
    from xiaohe.expiry import expire_guests

    live, live_conversation = await visitor(pg_factory)
    protected, protected_conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, protected, protected_conversation, "protected")
    await due(pg_factory, protected)
    async with pg_factory.begin() as db:
        (await db.get(Conversation, protected_conversation)).merge_pending = True
    async with pg_factory.begin() as db:
        assert await expire_guests(db, datetime.now(UTC) + timedelta(days=1), 100) == []
        assert (await db.get(Run, UUID(accepted.run.id))).status == "queued"
        assert (await db.get(Session, live.session_id)).expires_at > datetime.now(UTC)


async def test_expiry_scans_are_bounded_and_rollback_with_job_cancellation(
    pg_factory, job_app, monkeypatch
):
    from xiaohe import expiry

    guests = [await visitor(pg_factory) for _ in range(3)]
    for who, conversation in guests:
        await send(pg_factory, who, conversation, "first")
        await due(pg_factory, who)
    async with pg_factory.begin() as db:
        assert len(await expiry.expire_guests(db, datetime.now(UTC), 1)) == 1

    original = expiry.cancel_run_job

    async def failure(*args):
        raise RuntimeError("controlled cancellation failure")

    monkeypatch.setattr(expiry, "cancel_run_job", failure)
    with pytest.raises(RuntimeError):
        async with pg_factory.begin() as db:
            await expiry.expire_guests(db, datetime.now(UTC), 100)
    async with pg_factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(Run).where(Run.status == "expired"))
            == 1
        )
    monkeypatch.setattr(expiry, "cancel_run_job", original)
    async with pg_factory.begin() as db:
        assert len(await expiry.expire_guests(db, datetime.now(UTC), 100)) == 2


async def test_expiry_interrupts_active_graph_without_publishing_late_result(
    pg_factory, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.expiry import expire_guests
    from xiaohe.graph import build_graph

    started, stopped = asyncio.Event(), asyncio.Event()

    async def handle(state):
        started.set()
        try:
            await asyncio.sleep(30)
            return AgentOutcome(
                parts=[{"type": "text", "text": "late"}],
                wait_for_input=False,
                artifact_version_ids=[],
            )
        finally:
            stopped.set()

    graph = build_graph(handler=handle)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "first")
    task = asyncio.create_task(runtime_env.worker.execute_run(UUID(accepted.run.id)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await due(pg_factory, who)
        async with pg_factory.begin() as db:
            assert await expire_guests(db, datetime.now(UTC), 100) == [who.guest_id]
        await asyncio.wait_for(task, 5)
        await asyncio.wait_for(stopped.wait(), 2)
        async with pg_factory() as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(Message).where(Message.role == "assistant")
                )
                == 0
            )
            assert (await db.scalar(select(RunAttempt))).graph_stopped_at is not None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_registered_maintenance_stops_expired_jobs(pg_factory, pg_url, job_app, runtime_env):
    from xiaohe.jobs import MAINTENANCE_QUEUE, RECOVERY_TASK, build_job_app

    who, conversation = await visitor(pg_factory)
    await send(pg_factory, who, conversation, "first")
    await due(pg_factory, who)
    app = build_job_app(
        pg_url, runtime_env.worker.execute_run, on_maintenance=runtime_env.worker.maintain
    )
    async with app.open_async():
        await app.tasks[RECOVERY_TASK].defer_async(timestamp=0)
        await app.run_worker_async(
            queues=[MAINTENANCE_QUEUE],
            wait=False,
            listen_notify=False,
            install_signal_handlers=False,
        )
    async with pg_factory() as db:
        assert (await db.scalar(select(Run))).status == "expired"


async def test_expiry_marks_pending_upload_failed_and_keeps_cleanup_intent(pg_factory, job_app):
    from test_artifact_versions import staged

    from xiaohe.expiry import expire_guests
    from xiaohe.models import ArtifactVersion

    service, who, fence, version = await staged(pg_factory, job_app)
    await due(pg_factory, who)
    async with pg_factory.begin() as db:
        await expire_guests(db, datetime.now(UTC), 100)
    async with pg_factory() as db:
        current = await db.get(ArtifactVersion, version.id)
        assert current.status == "failed" and current.cleanup_pending
    with pytest.raises(ServiceError):
        await service.write_artifact_object(version.id, b"test-only")
