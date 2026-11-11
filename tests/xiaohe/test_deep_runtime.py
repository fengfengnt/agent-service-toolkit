import importlib

import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_deep_factory import final_response
from test_deep_tools import tool_context
from test_lead_submission import Business

from xiaohe.deep_runtime.models import DeepExecution
from xiaohe.models import Command, Conversation

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def runtime():
    assert importlib.util.find_spec("xiaohe.deep_runtime.runtime"), "native runner missing"
    return importlib.import_module("xiaohe.deep_runtime.runtime")


async def invoke(context, *, model, saver=None):
    async with context.factory() as db:
        command = await db.get(Command, context.command_id)
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
    return await runtime().run_deep_turn(
        context.factory,
        context.fence,
        execution=execution,
        model=model,
        business_client=context.business_client,
        checkpointer=saver or InMemorySaver(),
    )


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_three_business_nodes_and_native_answer(pg_factory, job_app, tmp_path, role):
    context, _ = await tool_context(pg_factory, tmp_path, role=role, text="有什么建议")
    model = make_script_model(
        [
            final_response(
                answer={"text": "建议先明确需求。", "basis": "general_advice"}, wait_for_input=True
            )
        ]
    )
    outcome = await invoke(context, model=model)
    assert {"prepare_context", "business_agent", "finalize"} == set(
        runtime().business_graph_nodes()
    )
    assert outcome.wait_for_input and "一般建议" in outcome.parts[0].text
    assert (
        sum(isinstance(m, HumanMessage) and m.content == context.text for m in model.requests[0])
        == 1
    )
    async with pg_factory() as db:
        command = await db.get(Command, context.command_id)
        assert str(context.fence.run_id) not in runtime().deep_thread_ids(command)
        assert len(runtime().deep_thread_ids(command)) == 1


async def test_button_submits_without_model(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path, client=Business())
    async with pg_factory.begin() as db:
        cmd = await db.get(Command, context.command_id)
        cmd.payload = cmd.payload | {
            "reception": {
                "action": "confirm",
                "draftId": str(frame.draft_id),
                "expectedRevision": frame.draft_revision,
                "summaryVersion": frame.draft_revision,
            }
        }
    model = make_script_model([AIMessage(content="must not run")])
    outcome = await invoke(context, model=model)
    assert not model.requests and not outcome.wait_for_input
    assert "受理" in outcome.parts[0].text and len(context.business_client.payloads) == 1


async def test_reception_handoff_only_once(pg_factory, job_app, tmp_path):
    context, _ = await tool_context(pg_factory, tmp_path, text="我是采购方")
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {
            "deepExecution": DeepExecution(bundle_id=context.bundle.bundle_id).model_dump(
                mode="json"
            )
        }
        (await db.get(Conversation, context.conversation_id)).direction = "unknown"
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="request_handoff",
                        args={"direction": "buyer", "evidence": "采购方"},
                        id="route",
                    )
                ],
            ),
            final_response(wait_for_input=False),
            final_response(answer={"text": "请介绍采购需求。", "basis": "general_advice"}),
        ]
    )
    await invoke(context, model=model)
    async with pg_factory() as db:
        cmd = await db.get(Command, context.command_id)
        assert cmd.payload["deepExecution"]["handoffs"] == 1
        assert (await db.get(Conversation, context.conversation_id)).direction == "buyer"


async def test_outer_dispatch_reads_command_before_legacy_snapshot(
    pg_factory, job_app, tmp_path, monkeypatch
):
    from xiaohe.graph import build_graph

    context, _ = await tool_context(pg_factory, tmp_path)

    async def forbidden(*args):
        raise AssertionError("native entered legacy snapshot loader")

    monkeypatch.setattr("xiaohe.workflows.store.bundle_for_run", forbidden)
    graph = build_graph(
        factory=pg_factory, model=make_script_model([final_response(wait_for_input=False)])
    )
    graph.checkpointer = InMemorySaver()
    result = await graph.ainvoke(
        {"run_id": str(context.fence.run_id), "output_message_id": str(context.command_id)},
        config={
            "configurable": {"thread_id": "outer"},
            "metadata": {
                "business_run_id": str(context.fence.run_id),
                "attempt": context.fence.attempt,
                "ownership_epoch": context.fence.ownership_epoch,
            },
        },
    )
    assert result["outcome"]
