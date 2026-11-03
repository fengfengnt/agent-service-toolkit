from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_admission import send, visitor

from xiaohe.contracts import ToolkitExecution
from xiaohe.models import Command, Run, Session
from xiaohe.runs import acquire_run, reply_id
from xiaohe.toolkit_runtime import create_execution_router

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def frame(factory, who, conversation):
    accepted = await send(factory, who, conversation, "one")
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        run = await db.get(Run, fence.run_id)
        session = await db.get(Session, who.session_id)
        return ToolkitExecution(
            run_id=run.id,
            user_id=session.toolkit_user_id,
            thread_id=run.thread_id,
            attempt=fence.attempt,
            ownership_epoch=fence.ownership_epoch,
            mode="start",
        )


async def test_internal_execution_reuses_bearer_and_registered_graph(pg_factory, runtime_env):
    who, conversation = await visitor(pg_factory)
    command = await frame(pg_factory, who, conversation)
    url = f"/internal/xiaohe/runs/{command.run_id}/stream"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime_env.app), base_url="http://runtime"
    ) as client:
        for token in (None, "wrong"):
            response = await client.post(
                url,
                json=command.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            assert response.status_code == 401
        for changes in (
            {"user_id": "other"},
            {"thread_id": "foreign"},
            {"checkpoint_ns": "foreign"},
        ):
            response = await client.post(
                url,
                json={**command.model_dump(mode="json"), **changes},
                headers={"Authorization": "Bearer fixture-auth"},
            )
            assert response.status_code in (403, 422)
    events = [event async for event in runtime_env.client.stream_execution(command)]
    assert events[-1].kind == "outcome" and events[-1].outcome.parts[0].text == "模拟答复"
    assert len(runtime_env.calls) == 1


async def test_recover_does_not_resubmit_human_message(pg_factory, runtime_env):
    who, conversation = await visitor(pg_factory)
    command = await frame(pg_factory, who, conversation)
    _ = [event async for event in runtime_env.client.stream_execution(command)]
    # Graph completed, but deliberately leave the business outcome uncommitted.
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, command.run_id, recover=True)
        run = await db.get(Run, command.run_id)
        replay = ToolkitExecution(
            run_id=run.id,
            user_id=command.user_id,
            thread_id=run.thread_id,
            attempt=fence.attempt,
            ownership_epoch=fence.ownership_epoch,
            mode="recover",
        )
    events = [event async for event in runtime_env.client.stream_execution(replay)]
    assert events[-1].kind == "outcome" and len(runtime_env.calls) == 1
    state = await runtime_env.graph.aget_state({"configurable": {"thread_id": replay.thread_id}})
    assert len([message for message in state.values["messages"] if message.type == "human"]) == 1


async def test_internal_router_rejects_unset_or_mismatched_upstream_secret(
    pg_factory, runtime_env, monkeypatch
):
    from core.settings import settings as upstream

    for value in (None, SecretStr("different")):
        monkeypatch.setattr(upstream, "AUTH_SECRET", value)
        with pytest.raises(ValueError):
            create_execution_router(pg_factory, runtime_env.settings)


async def test_resume_receipt_recovers_the_action_not_previous_question(
    pg_factory, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.admission import accept_action
    from xiaohe.contracts import AgentOutcome
    from xiaohe.graph import build_graph

    calls = []

    async def handler(state):
        calls.append(state["command_id"])
        if len(calls) == 1:
            return AgentOutcome(
                parts=[{"type": "input_request", "requestId": "Q1", "prompt": "请补充"}],
                wait_for_input=True,
                artifact_version_ids=[],
            )
        return AgentOutcome(
            parts=[{"type": "text", "text": "动作已完成"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        await accept_action(db, who, conversation, "reply", "Q1", None, "补充")
        fence = await acquire_run(db, UUID(accepted.run.id))
        run = await db.get(Run, fence.run_id)
        session = await db.get(Session, who.session_id)
        resume = ToolkitExecution(
            run_id=run.id,
            user_id=session.toolkit_user_id,
            thread_id=run.thread_id,
            attempt=fence.attempt,
            ownership_epoch=fence.ownership_epoch,
            mode="resume",
        )
    from langgraph._internal._constants import NULL_TASK_ID

    from xiaohe.toolkit_runtime import _prepare

    async with pg_factory.begin() as db:
        context = await _prepare(db, resume)
    checkpoint = await graph.checkpointer.aget_tuple(
        {"configurable": {"thread_id": resume.thread_id}}
    )
    approved = {
        "run_id": str(resume.run_id),
        "command_id": str(context.input_id),
        "message_id": str(context.input_message_id),
        "text": context.input_text,
        "output_message_id": str(reply_id(resume.run_id, context.input_id)),
    }
    await graph.checkpointer.aput_writes(
        checkpoint.config, [("__resume__", approved)], NULL_TASK_ID
    )
    checkpoint = await graph.checkpointer.aget_tuple(checkpoint.config)
    assert any(channel == "__resume__" for _, channel, _ in checkpoint.pending_writes)
    await runtime_env.worker.execute_run(resume.run_id)
    async with pg_factory() as db:
        run = await db.get(Run, resume.run_id)
        assert run.status == "completed" and run.outcome["parts"][0]["text"] == "动作已完成"
        assert (
            await db.scalar(
                select(Command.completed_at).where(
                    Command.run_id == run.id, Command.kind == "action"
                )
            )
            is not None
        )
    assert len(calls) == 2
