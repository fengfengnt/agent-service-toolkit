from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from test_admission import send, visitor
from test_identity_sessions import bootstrap
from test_reception_graph import model
from test_toolkit_execution import frame

from xiaohe.contracts import AgentOutcome
from xiaohe.graph import build_graph, build_reception_context
from xiaohe.models import Conversation, Message, Run
from xiaohe.runs import acquire_run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_authorized_context_excludes_foreign_system_and_untrusted_state(
    pg_factory, runtime_env, monkeypatch
):
    from agents.agents import agents

    who, conversation = await visitor(pg_factory)
    other, _, _ = await bootstrap(pg_factory, system="other", origin="https://other.example")
    async with pg_factory.begin() as db:
        db.add(
            Message(
                conversation_id=UUID(other.conversation_id),
                system_id="other",
                tenant_id=2,
                role="user",
                parts=[{"type": "text", "text": "foreign-private-data"}],
            )
        )
    accepted = await send(pg_factory, who, conversation, "initial", "我代表供应商，想了解服务")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        context = await build_reception_context(db, fence)
    assert "foreign-private-data" not in context.model_dump_json()
    graph = build_graph(factory=pg_factory, model=model("buyer"))
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    await runtime_env.worker.execute_run(fence.run_id)
    async with pg_factory() as db:
        assert (await db.get(Run, fence.run_id)).status == "completed"
        assert (await db.get(Conversation, conversation)).direction == "supplier"


async def test_internal_model_chunks_never_become_public_text(pg_factory, runtime_env, monkeypatch):
    from agents.agents import agents

    fake = GenericFakeChatModel(
        messages=iter([AIMessage(content="internal routing JSON must stay private")])
    )

    async def handler(state):
        await fake.ainvoke("internal-only")
        return AgentOutcome(
            parts=[{"type": "text", "text": "对客答复"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    command = await frame(pg_factory, who, conversation)
    events = [event async for event in runtime_env.client.stream_execution(command)]
    assert [event.kind for event in events] == ["outcome"]
    assert "internal routing" not in "".join(event.model_dump_json() for event in events)
