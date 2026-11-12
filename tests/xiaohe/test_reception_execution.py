from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from test_admission import visitor
from test_identity_sessions import bootstrap
from test_toolkit_execution import frame

from xiaohe.contracts import AgentOutcome
from xiaohe.graph import build_graph
from xiaohe.models import Conversation, Message, Run

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
    from deep_runtime_support import make_script_model
    from test_deep_factory import final_response

    from xiaohe.admission import accept_agent_message
    from xiaohe.contracts import MessageInput

    async with pg_factory.begin() as db:
        accepted = await accept_agent_message(
            db,
            who,
            "xiaohe-supplier",
            conversation,
            MessageInput(client_message_id="initial", text="我代表供应商，想了解服务"),
        )
    native_model = make_script_model([final_response(wait_for_input=True)])
    graph = build_graph(factory=pg_factory, model=native_model)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    assert "foreign-private-data" not in str(native_model.requests)
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "waiting_input"
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
