import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_admission import send, visitor

from xiaohe.admission import accept_action
from xiaohe.contracts import AgentOutcome
from xiaohe.graph import build_graph
from xiaohe.models import Command, Message, Run


async def test_exhausted_stalled_job_releases_business_slot(pg_factory, job_app, runtime_env):
    from sqlalchemy import text

    from xiaohe.jobs import recover_stalled_jobs
    from xiaohe.runs import acquire_run

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    worker_id = await job_app.job_manager.register_worker()
    for previous in range(3):
        job = await job_app.job_manager.fetch_job(queues=["xiaohe"], worker_id=worker_id)
        async with pg_factory.begin() as db:
            await acquire_run(db, UUID(accepted.run.id), recover=True)
        await recover_stalled_jobs(
            job_app, seconds_since_heartbeat=0, on_exhausted=runtime_env.worker.finalize_lost_run
        )
        if previous < 2:
            async with pg_factory.begin() as db:
                await db.execute(
                    text(
                        "UPDATE procrastinate_jobs SET scheduled_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": job.id},
                )
    async with pg_factory() as db:
        run = await db.get(Run, UUID(accepted.run.id))
        assert run.status == "failed" and not run.holds_slot
    assert runtime_env.calls == []


async def test_stale_epoch_cannot_change_current_run_status(pg_factory, job_app, runtime_env):
    from sqlalchemy import update

    from xiaohe.contracts import ServiceError
    from xiaohe.models import Conversation
    from xiaohe.runs import acquire_run

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        await db.execute(
            update(Conversation).where(Conversation.id == conversation).values(ownership_epoch=1)
        )
        await db.execute(update(Run).where(Run.id == fence.run_id).values(ownership_epoch=1))
    assert (
        await runtime_env.worker._failed(fence, ServiceError("UPSTREAM_UNAVAILABLE", 503, True))
        is False
    )
    async with pg_factory() as db:
        assert (await db.get(Run, fence.run_id)).status == "running"


pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_worker_duplicate_delivery_single_writer(pg_factory, runtime_env):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    await asyncio.gather(*(runtime_env.worker.execute_run(UUID(accepted.run.id)) for _ in range(3)))
    assert len(runtime_env.calls) == 1
    async with pg_factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Message).where(Message.role == "assistant")
            )
            == 1
        )
        assert (await db.get(Run, UUID(accepted.run.id))).status == "completed"


async def test_waiting_input_is_durable_and_action_is_not_replayed(
    pg_factory, runtime_env, monkeypatch
):
    from agents.agents import agents

    calls = []

    async def respond(state):
        calls.append(state["command_id"])
        if len(calls) == 1:
            return AgentOutcome(
                parts=[{"type": "input_request", "requestId": "Q1", "prompt": "请补充"}],
                wait_for_input=True,
                artifact_version_ids=[],
            )
        return AgentOutcome(
            parts=[{"type": "text", "text": "已收到补充"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=respond)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        run = await db.get(Run, UUID(accepted.run.id))
        assert run.status == "waiting_input" and run.pending_request["requestId"] == "Q1"
        thread = run.thread_id
    before = await graph.aget_state({"configurable": {"thread_id": thread}})
    assert before.next
    async with pg_factory.begin() as db:
        reply = await accept_action(db, who, conversation, "answer", "Q1", None, "补充内容")
    await runtime_env.worker.execute_run(UUID(reply.run.id))
    await runtime_env.worker.execute_run(UUID(reply.run.id))
    after = await graph.aget_state({"configurable": {"thread_id": thread}})
    humans = [message for message in after.values["messages"] if message.type == "human"]
    assert len(humans) == 2 and len({message.id for message in humans}) == 2
    assert len(calls) == 2
    async with pg_factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Command).where(Command.completed_at.is_not(None))
            )
            == 2
        )


async def test_cancel_live_execution_never_publishes_late_output(
    pg_factory, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.runs import cancel_run

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(state):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    worker = asyncio.create_task(runtime_env.worker.execute_run(UUID(accepted.run.id)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with pg_factory.begin() as db:
            await cancel_run(db, who, UUID(accepted.run.id))
        await asyncio.wait_for(worker, 5)
        await asyncio.wait_for(stopped.wait(), 2)
        async with pg_factory() as db:
            assert (await db.get(Run, UUID(accepted.run.id))).status == "cancelled"
            assert (
                await db.scalar(
                    select(func.count()).select_from(Message).where(Message.role == "assistant")
                )
                == 0
            )
    finally:
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def test_user_execution_survives_local_logout_but_not_deadline(pg_factory, runtime_env):
    from fastapi import Response
    from sqlalchemy import update
    from test_identity_sessions import actor, bootstrap, login, request

    from xiaohe.contracts import ServiceError
    from xiaohe.identity import logout_session
    from xiaohe.runs import acquire_run, require_write

    _, token = await login(pg_factory)
    snapshot, _, _ = await bootstrap(pg_factory, token)
    who = await actor(pg_factory, token)
    accepted = await send(pg_factory, who, UUID(snapshot.conversation_id), "initial")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        await logout_session(request(token, csrf=snapshot.csrf.token), Response(), db)
    async with pg_factory.begin() as db:
        assert (await require_write(db, fence)).user_id == who.user_id
        await db.execute(
            update(Run)
            .where(Run.id == fence.run_id)
            .values(execution_expires_at=func.clock_timestamp())
        )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await require_write(db, fence)
        assert exc.value.code == "EXECUTION_EXPIRED"
