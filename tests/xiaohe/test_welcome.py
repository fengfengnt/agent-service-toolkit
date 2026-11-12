import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from test_http_api import client_for, session

from xiaohe.models import Conversation, Event, Message, Run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_initial_welcome_is_persisted_once_without_a_run_or_quota(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        first = await session(client)
        assert len(first["messages"]) == 1
        welcome = first["messages"][0]
        assert welcome["role"] == "assistant"
        choices = next(p for p in welcome["parts"] if p["type"] == "suggestions")
        assert [(item["id"], item["text"]) for item in choices["items"]] == [
            ("buyer", "采购商"),
            ("organization", "社群组织"),
            ("supplier", "供应商"),
        ]
        assert first["viewer"]["messageCount"] == 0 and first["activeRun"] is None
        reopened = await asyncio.gather(
            *[client.post("/ai-api/v1/session", json={}) for _ in range(4)]
        )
        assert all(
            r.status_code == 200 and r.json()["messages"] == first["messages"] for r in reopened
        )
        assert all(
            r.json()["viewer"]["expiresAt"] == first["viewer"]["expiresAt"] for r in reopened
        )
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Message)) == 1
            assert await db.scalar(select(func.count()).select_from(Event)) == 1
            assert await db.scalar(select(func.count()).select_from(Run)) == 0
            assert await db.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 0


@pytest.mark.parametrize(
    "direction,label", [("buyer", "采购方"), ("organization", "社群组织"), ("supplier", "供应商")]
)
async def test_welcome_direction_shortcut_is_a_real_first_user_turn(
    pg_factory, runtime_env, monkeypatch, direction, label
):
    from agents.agents import agents
    from deep_runtime_support import make_script_model
    from langchain_core.messages import AIMessage
    from test_deep_factory import final_response
    from xiaohe.graph import build_graph

    native=make_script_model([
        AIMessage(content="",tool_calls=[dict(name="request_handoff",id="welcome-direction",args={"direction":direction,"evidence":label})]),
        final_response(wait_for_input=False),
        final_response(answer={"text":direction,"basis":"needs_confirmation","source_ids":[]},wait_for_input=False),
    ])
    graph=build_graph(factory=pg_factory,model=native)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    async with client_for(pg_factory) as (client, app):
        first = await session(client)
        assert first["messages"] and first["viewer"]["messageCount"] == 0
        path = f"/ai-api/v1/conversations/{first['conversationId']}"
        accepted = await client.post(
            path + "/messages", json={"clientMessageId": "direction", "text": label}
        )
        assert accepted.status_code == 202 and accepted.json()["viewer"]["messageCount"] == 1
        await runtime_env.worker.execute_run(UUID(accepted.json()["run"]["id"]))
        async with pg_factory() as db:
            assert (
                await db.get(Conversation, UUID(first["conversationId"]))
            ).direction == direction
        final = (await client.get(path)).json()
        assert (
            len(final["messages"]) == 3 and final["messages"][0]["id"] == first["messages"][0]["id"]
        )
        assert final["messages"][-1]["parts"] == [{"type": "text", "text": direction}]
