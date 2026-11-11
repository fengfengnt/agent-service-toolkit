from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from deep_runtime_support import DeepScriptModel, make_script_model
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from test_deep_factory import final_response, stub_tools
from test_deep_runtime import invoke
from test_deep_tools import tool_context, tools_module
from test_lead_submission import Business

from xiaohe.deep_runtime.bundles import load_runtime_bundle, select_binding
from xiaohe.deep_runtime.factory import build_business_agent, build_readonly_child
from xiaohe.deep_runtime.models import ChildBinding, DeepExecution, RuntimeBinding
from xiaohe.deep_runtime.runtime import publish_runtime
from xiaohe.models import Command, Conversation, Message, Run
from xiaohe.reception.models import ReceptionDraft

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "question,submits", [("确认提交这份资料吗？", 1), ("需要解释这个字段吗？", 0)]
)
async def test_colloquial_confirmation_receives_previous_question(
    pg_factory, job_app, tmp_path, question, submits
):
    client = Business()
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        current = await db.get(Message, command.message_id)
        db.add(
            Message(
                conversation_id=context.conversation_id,
                system_id=context.actor.system.system_id,
                tenant_id=context.actor.system.tenant_id,
                role="assistant",
                parts=[{"type": "text", "text": question}],
                created_at=current.created_at - timedelta(microseconds=1),
            )
        )
        command.question = {"type": "input_request", "requestId": str(uuid4()), "prompt": question}

    class ContextModel(DeepScriptModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.requests.append(list(messages))
            if (
                submits
                and any(isinstance(m, AIMessage) and m.text == question for m in messages)
                and not any(isinstance(m, ToolMessage) for m in messages)
            ):
                answer = AIMessage(
                    content="",
                    tool_calls=[
                        dict(
                            name="submit_lead",
                            args={
                                "draft_id": str(frame.draft_id),
                                "expected_revision": frame.draft_revision,
                                "confirmation": {"confirmed": True, "evidence": "好的"},
                            },
                            id="context-submit",
                        )
                    ],
                )
            else:
                answer = final_response(
                    answer={"text": "可以继续说明。", "basis": "general_advice"},
                    wait_for_input=False,
                )
            return ChatResult(generations=[ChatGeneration(message=answer)])

    model = ContextModel(responses=[], profile={"tool_calling": True})
    await invoke(context, model=model)
    assert any(isinstance(m, AIMessage) and m.text == question for m in model.requests[0])
    assert question in model.requests[0][1].text or any(
        question in m.text for m in model.requests[0]
    )
    assert len(client.payloads) == submits


@pytest.mark.parametrize("problem", ["success_claim", "wrong_source", "missing_structure"])
async def test_committed_write_survives_bad_final_candidate(
    pg_factory, job_app, tmp_path, monkeypatch, problem
):
    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州")
    await tools_module().execute_effect(
        context,
        "save_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"candidates": {"city": {"value": "苏州", "evidence": "苏州"}}},
    )
    answer = (
        {"text": "本次资料已保存。", "basis": "general_advice"}
        if problem == "success_claim"
        else {
            "text": "可以继续咨询。",
            "basis": "confirmed_material",
            "source_ids": ["nonexistent-source"],
        }
    )
    model = make_script_model([final_response(answer=answer, wait_for_input=False)])
    if problem == "missing_structure":

        class BrokenGraph:
            async def ainvoke(self, *args, **kwargs):
                return {}

            async def aget_state(self, *args):
                return SimpleNamespace(tasks=[])

        monkeypatch.setattr(
            "xiaohe.deep_runtime.runtime.build_business_agent",
            lambda *args, **kwargs: BrokenGraph(),
        )
    result = await invoke(context, model=model)
    assert any("保存" in getattr(part, "text", "") for part in result.parts)
    assert await invoke(context, model=model) == result
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.revision == frame.draft_revision + 1 and row.fields["city"] == "苏州"


async def test_disabled_runtime_drains_snapshot_without_loading_broken_new_package(
    pg_factory, job_app, tmp_path
):
    from test_admission import send, visitor

    context, _ = await tool_context(pg_factory, tmp_path)
    await publish_runtime(pg_factory, tmp_path / "broken-current-package", enabled=False)
    result = await invoke(context, model=make_script_model([final_response(wait_for_input=False)]))
    assert result
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "新请求")
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).runtime_version != "deepagent-v1"
        assert (await db.get(Command, context.command_id)).payload["deepExecution"][
            "bundle_id"
        ] == context.bundle.bundle_id


async def test_child_cannot_bind_reconciling_progress_tool(pg_factory, job_app, tmp_path):
    context, _ = await tool_context(pg_factory, tmp_path)
    with pytest.raises(ValueError, match="read.only"):
        ChildBinding(
            name="progress-reader", description="只读", skills=[], tools=["query_progress"]
        )
    with pytest.raises(ValueError, match="read.only"):
        build_readonly_child(
            context.bundle,
            RuntimeBinding(agent_id="xiaohe-buyer", skills=[], tools=["query_progress"]),
            model=make_script_model([final_response()]),
            tools=[
                t for t in tools_module().make_business_tools(context) if t.name == "query_progress"
            ],
        )


@pytest.mark.parametrize("question", ["你是谁", "收费多少，保证多久见效", "帮我做无关任务"])
async def test_reception_policy_is_snapshotted_and_in_model_input(
    pg_factory, job_app, tmp_path, question
):
    context, _ = await tool_context(pg_factory, tmp_path, text=question)
    bundle = load_runtime_bundle(Path(__file__).resolve().parents[2] / "resources/xiaohe")
    assert "deep-runtime/reception-policy.md" in bundle.resources
    binding = select_binding(bundle, context.actor, "xiaohe-reception")
    model = make_script_model(
        [
            final_response(
                answer={"text": "请说明需要的服务方向。", "basis": "needs_confirmation"},
                wait_for_input=False,
            )
        ]
    )
    graph = build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    from langchain_core.messages import HumanMessage

    await graph.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config={"configurable": {"thread_id": str(uuid4())}},
    )
    prompt = model.requests[0][0].text
    for expected in (
        "云鹤智元",
        "小鹤",
        "采购",
        "社群组织",
        "供应商",
        "无关",
        "收费",
        "效果",
        "尚待确认",
    ):
        assert expected in prompt
    assert "只返回指定结构化结果，不调用工具" not in prompt


async def test_reception_keeps_controlled_login_card(pg_factory, job_app, tmp_path):
    context, _ = await tool_context(pg_factory, tmp_path, text="我要登录")
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
        command.payload = command.payload | {
            "deepExecution": execution.model_copy(update={"active": "xiaohe-reception"}).model_dump(
                mode="json"
            )
        }
        (await db.get(Conversation, context.conversation_id)).direction = "unknown"
    model = make_script_model([final_response(wait_for_input=True)])
    result = await invoke(context, model=model)
    assert not model.requests and any(part.type == "auth_request" for part in result.parts)


async def test_write_receipt_does_not_hide_unexpected_inner_interrupt(
    pg_factory, job_app, tmp_path, monkeypatch
):
    from xiaohe.contracts import ServiceError

    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州")
    await tools_module().execute_effect(
        context,
        "save_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"candidates": {"city": {"value": "苏州", "evidence": "苏州"}}},
    )

    class InterruptedGraph:
        async def ainvoke(self, *args, **kwargs):
            return {}

        async def aget_state(self, *args):
            return SimpleNamespace(tasks=[SimpleNamespace(interrupts=["unexpected"])])

    monkeypatch.setattr(
        "xiaohe.deep_runtime.runtime.build_business_agent",
        lambda *args, **kwargs: InterruptedGraph(),
    )
    with pytest.raises(ServiceError, match="INVALID_WAIT_STATE"):
        await invoke(context, model=make_script_model([final_response()]))
    async with pg_factory() as db:
        assert (await db.get(Command, context.command_id)).payload["deepExecution"][
            "result"
        ] is None


async def test_disabled_startup_still_validates_waiting_native_run_snapshot(
    pg_factory, job_app, tmp_path
):
    from datetime import UTC, datetime

    from sqlalchemy import select
    from test_admission import send, visitor

    from xiaohe.models import DeepRuntimeBundleRecord
    from xiaohe.workflows.models import WorkflowValidationError

    await publish_runtime(
        pg_factory, Path(__file__).resolve().parents[2] / "resources/xiaohe", enabled=True
    )
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "等待接续")
    async with pg_factory.begin() as db:
        row = await db.get(Run, UUID(accepted.run.id))
        row.status = "waiting_input"
        command = await db.scalar(select(Command).where(Command.run_id == row.id))
        command.completed_at = datetime.now(UTC)
        snapshot = await db.get(DeepRuntimeBundleRecord, row.deep_runtime_bundle_id)
        snapshot.payload = snapshot.payload | {"resources": {}}
    with pytest.raises(WorkflowValidationError, match="snapshot corrupt"):
        await publish_runtime(pg_factory, tmp_path / "broken-current", enabled=False)
