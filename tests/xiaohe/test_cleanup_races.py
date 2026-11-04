import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from test_admission import send, visitor
from test_visitor_expiry import due

from xiaohe.contracts import ServiceError
from xiaohe.models import Command, Conversation, Event, EventTombstone, Message, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def cleanup(factory, guest_id, artifacts=None):
    from xiaohe.expiry import expire_guests, purge_expired_guest

    async with factory.begin() as db:
        await expire_guests(db, datetime.now(UTC), 100)
    return await purge_expired_guest(factory, guest_id, artifacts)


async def test_cleanup_removes_bodies_and_keeps_expired_session_and_cursor_tombstones(
    pg_factory, runtime_env
):
    from xiaohe.identity import require_session

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "anonymous private text")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        thread_id = (await db.get(Run, UUID(accepted.run.id))).thread_id
        cursor_ids = set(await db.scalars(select(Event.id)))
        assert (
            await runtime_env.graph.aget_state({"configurable": {"thread_id": thread_id}})
        ).values
    await due(pg_factory, who)
    assert await cleanup(pg_factory, who.guest_id)
    async with pg_factory() as db:
        retained = await db.get(Session, who.session_id)
        assert retained.purged_at >= retained.cleanup_stopped_at >= retained.expires_at
        assert retained.message_count == 1
        assert await db.get(Conversation, conversation) is not None
        for model in (Message, Run, Command):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        assert [event.payload for event in await db.scalars(select(Event))] == [
            {"code": "VISITOR_EXPIRED"}
        ]
        assert cursor_ids <= set(await db.scalars(select(EventTombstone.id)))
        with pytest.raises(ServiceError) as error:
            await require_session(db, who)
        assert error.value.http_status == 410
    assert not (
        await runtime_env.graph.aget_state({"configurable": {"thread_id": thread_id}})
    ).values
    assert await cleanup(pg_factory, who.guest_id)


async def test_cleanup_refuses_live_guests(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    await send(pg_factory, who, conversation, "still live")
    async with pg_factory() as db:
        before = await db.scalar(select(Message.parts))
    assert not await cleanup(pg_factory, who.guest_id)
    async with pg_factory() as db:
        assert await db.scalar(select(Message.parts)) == before


@pytest.mark.parametrize("guard_kind", ["run", "thread"])
async def test_cleanup_waits_for_writer_guards(pg_factory, runtime_env, guard_kind):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "private")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        run = await db.get(Run, UUID(accepted.run.id))
        key = "xiaohe." + guard_kind + ":" + (str(run.id) if guard_kind == "run" else run.thread_id)
    await due(pg_factory, who)
    async with pg_factory.kw["bind"].connect() as guard, guard.begin():
        await guard.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
        )
        assert not await asyncio.wait_for(cleanup(pg_factory, who.guest_id), 2)
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Message)) == 2
    assert await cleanup(pg_factory, who.guest_id)


async def test_database_delete_failure_rolls_back_checkpoint_and_cleanup_marker(
    pg_factory, runtime_env, monkeypatch
):
    from xiaohe import expiry

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "private")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        thread_id = (await db.get(Run, UUID(accepted.run.id))).thread_id
    await due(pg_factory, who)
    original = expiry._delete_guest_bodies

    async def failure(*args):
        await original(*args)
        raise RuntimeError("controlled DB delete rollback")

    monkeypatch.setattr(expiry, "_delete_guest_bodies", failure)
    with pytest.raises(RuntimeError):
        await cleanup(pg_factory, who.guest_id)
    async with pg_factory() as db:
        assert (await db.get(Session, who.session_id)).purged_at is None
        assert await db.scalar(select(func.count()).select_from(Message)) == 2
    assert (await runtime_env.graph.aget_state({"configurable": {"thread_id": thread_id}})).values
    monkeypatch.setattr(expiry, "_delete_guest_bodies", original)
    assert await cleanup(pg_factory, who.guest_id)


async def test_registered_maintenance_purges_revoked_expired_guests_only(pg_factory, runtime_env):
    from xiaohe.artifacts import ArtifactService
    from xiaohe.worker import RunWorker

    expired, conversation = await visitor(pg_factory)
    live, live_conversation = await visitor(pg_factory)
    await send(pg_factory, expired, conversation, "expired")
    await send(pg_factory, live, live_conversation, "live")
    await due(pg_factory, expired)
    async with pg_factory.begin() as db:
        (await db.get(Session, expired.session_id)).revoked_at = func.clock_timestamp()
    service = ArtifactService(pg_factory, runtime_env.settings)
    worker = RunWorker(pg_factory, runtime_env.client, artifacts=service)
    await worker.maintain()
    async with pg_factory() as db:
        assert (await db.get(Session, expired.session_id)).purged_at is not None
        assert (await db.get(Session, live.session_id)).purged_at is None
        assert set(await db.scalars(select(Message.conversation_id))) == {live_conversation}


@pytest.mark.parametrize("claim_wins", [False, True])
async def test_claim_and_physical_cleanup_have_one_owner(pg_factory, runtime_env, claim_wins):
    from test_admission import finish
    from test_guest_claim import prepare
    from test_http_api import client_for, session
    from test_identity_sessions import actor, login

    from xiaohe.conversations import default_conversation

    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        target = await default_conversation(db, member)
    active = await send(pg_factory, member, target.id, "target")
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        source = await send(pg_factory, who, UUID(guest["conversationId"]), "source")
        await finish(pg_factory, source.run.id)
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        if claim_wins:
            assert (await client.post(path, json={})).status_code == 202
        await due(pg_factory, who)
        assert await cleanup(pg_factory, who.guest_id) is (not claim_wins)
        async with pg_factory() as db:
            assert (await db.get(Message, UUID(source.message.id)) is not None) is claim_wins
        await finish(pg_factory, active.run.id)
        await runtime_env.worker.maintain()
        result = await client.post(path, json={})
        assert result.status_code == 200
        assert result.json()["merge"]["status"] == ("MERGED" if claim_wins else "SKIPPED_EXPIRED")
        assert (source.message.id in {m["id"] for m in result.json()["messages"]}) is claim_wins
        assert len(calls) == 1


async def test_cleanup_keeps_one_safe_expiry_marker_for_an_open_stream(pg_factory, runtime_env):
    from xiaohe.artifacts import ArtifactService
    from xiaohe.events import stream_events
    from xiaohe.worker import RunWorker

    who, conversation = await visitor(pg_factory)
    await send(pg_factory, who, conversation, "private")
    stream = await stream_events(pg_factory, who, conversation)
    await due(pg_factory, who)
    worker = RunWorker(
        pg_factory, runtime_env.client, artifacts=ArtifactService(pg_factory, runtime_env.settings)
    )
    await worker.maintain()
    async with pg_factory() as db:
        marker = await db.scalar(select(Event).where(Event.event_type == "viewer.expired"))
        assert marker is not None and marker.payload == {"code": "VISITOR_EXPIRED"}
        marker_id = marker.id
        purged_at = (await db.get(Session, who.session_id)).purged_at
        assert marker.created_at <= purged_at
    await worker.maintain()
    assert b"viewer.expired" in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    async with pg_factory() as db:
        assert list(await db.scalars(select(Event.id))) == [marker_id]


async def test_stalled_job_for_a_purged_run_does_not_break_global_recovery(
    pg_factory, job_app, runtime_env
):
    from xiaohe.jobs import recover_stalled_jobs

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "private")
    worker_id = await job_app.job_manager.register_worker()
    job = await job_app.job_manager.fetch_job(queues=["xiaohe"], worker_id=worker_id)
    async with pg_factory.begin() as db:
        await db.execute(
            text("UPDATE procrastinate_jobs SET attempts=2 WHERE id=:id"), {"id": job.id}
        )
    await due(pg_factory, who)
    assert await cleanup(pg_factory, who.guest_id)
    assert (
        await recover_stalled_jobs(
            job_app, seconds_since_heartbeat=0, on_exhausted=runtime_env.worker.finalize_lost_run
        )
        == 0
    )
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        assert (
            await db.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": job.id}
            )
            == "failed"
        )
        assert await db.get(Run, UUID(accepted.run.id)) is None


async def test_failed_cleanup_does_not_starve_other_guests(pg_factory, runtime_env, monkeypatch):
    from xiaohe import expiry

    guests = [await visitor(pg_factory) for _ in range(2)]
    for who, conversation in guests:
        await send(pg_factory, who, conversation, "private")
        await due(pg_factory, who)
    async with pg_factory.begin() as db:
        await expiry.expire_guests(db, datetime.now(UTC), 100)
        first = await db.scalar(select(Session).order_by(Session.id))
        first_conversation = await db.scalar(
            select(Conversation.id).where(Conversation.guest_id == first.guest_id)
        )
    original = expiry._delete_guest_bodies

    async def fail_first(db, conversations, runs):
        if first_conversation in conversations:
            raise RuntimeError("controlled cleanup failure")
        await original(db, conversations, runs)

    monkeypatch.setattr(expiry, "_delete_guest_bodies", fail_first)
    await expiry.purge_expired_guests(pg_factory, None, limit=1)
    await expiry.purge_expired_guests(pg_factory, None, limit=1)
    async with pg_factory() as db:
        assert (await db.get(Session, first.id)).purged_at is None
        assert (
            await db.scalar(
                select(func.count()).select_from(Session).where(Session.purged_at.is_not(None))
            )
            == 1
        )
    monkeypatch.setattr(expiry, "_delete_guest_bodies", original)
    await expiry.purge_expired_guests(pg_factory, None, limit=1)
    async with pg_factory() as db:
        assert (await db.get(Session, first.id)).purged_at is not None
