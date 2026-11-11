import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage
from sqlalchemy import select
from test_deep_factory import final_response
from test_deep_runtime import invoke
from test_deep_tools import tool_context, tools_module
from test_lead_submission import Business

from xiaohe.models import Command
from xiaohe.reception.contracts import CreateDraft, ProgressPage
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import create_draft

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_full_native_capture_and_consultation(pg_factory, job_app, tmp_path, role):
    client = Business()
    context, frame = await tool_context(
        pg_factory,
        tmp_path,
        role=role,
        text="虚构甲公司，虚构协会，100—500，本市，暂无，测试联系人，测试联系电话，好的，可以提交",
        client=client,
    )
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        fields = dict(row.fields)
        row.fields, row.state, row.summary_version = {}, "collecting", None
    candidates = {
        key: {"value": value, "evidence": value if isinstance(value, str) else "暂无"}
        for key, value in fields.items()
    }
    calls = [
        AIMessage(
            content="",
            tool_calls=[
                dict(
                    name="save_lead",
                    args={
                        "draft_id": str(frame.draft_id),
                        "expected_revision": frame.draft_revision,
                        "candidates": candidates,
                    },
                    id="capture",
                )
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                dict(
                    name="submit_lead",
                    args={
                        "draft_id": str(frame.draft_id),
                        "expected_revision": frame.draft_revision + 1,
                        "confirmation": {"confirmed": True, "evidence": "可以提交"},
                    },
                    id="submit",
                )
            ],
        ),
        final_response(
            answer={"text": "服务范围可继续核实。", "basis": "needs_confirmation"},
            wait_for_input=False,
        ),
    ]
    result = await invoke(context, model=make_script_model(calls))
    assert not result.wait_for_input and len(client.payloads) == 1
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, frame.draft_id)).state == "accepted"
        assert (
            len(
                list(
                    await db.scalars(
                        select(ReceptionSubmission).where(ReceptionSubmission.status == "accepted")
                    )
                )
            )
            == 1
        )


async def test_resume_tool_honors_actual_selected_draft(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        await create_draft(
            db,
            context.actor,
            context.conversation_id,
            CreateDraft(client_draft_id="second", kind="service", direction="buyer"),
        )
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {
            "reception": {
                "action": "resume",
                "draftId": str(frame.draft_id),
                "expectedRevision": frame.draft_revision,
            }
        }
    tool = next(t for t in tools_module().make_business_tools(context) if t.name == "prepare_lead")
    result = await tool.ainvoke({"mode": "resume", "draft_id": str(frame.draft_id)})
    assert result["status"] == "ok" and result["data"]["draft_id"] == str(frame.draft_id)


async def test_progress_result_records_trusted_receipt_for_finalization(
    pg_factory, job_app, tmp_path
):
    class Client:
        async def progress(self, actor, query):
            return ProgressPage(groups=[], next_cursor=None)

    context, _ = await tool_context(pg_factory, tmp_path, client=Client())
    await tools_module().execute_effect(
        context, "query_progress", draft_id=None, expected_revision=None, payload={}
    )
    async with pg_factory() as db:
        command = await db.get(Command, context.command_id)
        assert any(
            e["operation"] == "query_progress"
            for e in command.payload["deepExecution"]["effects"].values()
        )


@pytest.mark.parametrize("action", ["resume", "show_summary", "skip_optional", "save_round"])
async def test_legacy_buttons_do_not_require_model_reinterpretation(
    pg_factory, job_app, tmp_path, action
):
    context, frame = await tool_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {
            "reception": {
                "action": action,
                "draftId": str(frame.draft_id),
                "expectedRevision": frame.draft_revision,
            }
        }
        row = await db.get(ReceptionDraft, frame.draft_id)
        row.step, row.state, row.summary_version = "round2", "collecting", None
    model = make_script_model([final_response(wait_for_input=True)])
    await invoke(context, model=model)
    assert not model.requests
    if action in ("show_summary", "skip_optional"):
        async with pg_factory() as db:
            row = await db.get(ReceptionDraft, frame.draft_id)
            assert row.state == "review" and row.summary_version == row.revision


async def test_native_internal_text_never_reaches_worker_messages(
    pg_factory, runtime_env, monkeypatch
):
    from pathlib import Path
    from uuid import UUID

    from test_admission import send, visitor

    from agents.agents import Agent, agents
    from xiaohe.deep_runtime.runtime import publish_runtime
    from xiaohe.graph import build_graph
    from xiaohe.models import Conversation, Message, Run

    root = Path(__file__).resolve().parents[2] / "resources/xiaohe"
    await publish_runtime(pg_factory, root, enabled=True)
    name = "buyer-security"
    model = make_script_model(
        [
            AIMessage(
                content="PRIVATE_INTERNAL_TRACE",
                tool_calls=[
                    dict(
                        name="read_file",
                        args={"file_path": f"/skills/{name}/SKILL.md"},
                        id="private-read",
                    )
                ],
            ),
            final_response(
                answer={"text": "公开答复。", "basis": "general_advice"}, wait_for_input=False
            ),
        ]
    )
    graph = build_graph(factory=pg_factory, model=model)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setitem(
        agents, "xiaohe", Agent(description="native isolated fixture", graph_like=graph)
    )
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = "buyer"
    accepted = await send(pg_factory, who, conversation, "请介绍服务")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "completed"
        messages = list(
            await db.scalars(
                select(Message).where(
                    Message.conversation_id == conversation, Message.role == "assistant"
                )
            )
        )
        assert "公开答复" in str([m.parts for m in messages])
        assert "PRIVATE_INTERNAL_TRACE" not in str([m.parts for m in messages])


@pytest.mark.parametrize("operation", ["competing_driver", "cancel"])
async def test_native_late_or_competing_driver_cannot_publish(
    pg_factory, job_app, tmp_path, operation
):
    import asyncio

    from deep_runtime_support import DeepScriptModel
    from langgraph.checkpoint.memory import InMemorySaver

    from xiaohe.contracts import ServiceError
    from xiaohe.models import Run

    ready, release = asyncio.Event(), asyncio.Event()

    class BlockingModel(DeepScriptModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            ready.set()
            await release.wait()
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    context, _ = await tool_context(pg_factory, tmp_path)
    model = BlockingModel(
        responses=[final_response(wait_for_input=False)], profile={"tool_calling": True}
    )
    saver = InMemorySaver()
    first = asyncio.create_task(invoke(context, model=model, saver=saver))
    await asyncio.wait_for(ready.wait(), 10)
    try:
        if operation == "competing_driver":
            with pytest.raises(ServiceError, match="EXECUTION_ALREADY_STARTED"):
                await invoke(context, model=model, saver=saver)
        else:
            async with pg_factory.begin() as db:
                row = await db.get(Run, context.fence.run_id)
                row.status, row.holds_slot = "cancelled", False
    finally:
        release.set()
    if operation == "cancel":
        with pytest.raises(ServiceError):
            await first
        async with pg_factory() as db:
            assert (await db.get(Command, context.command_id)).payload["deepExecution"][
                "result"
            ] is None
    else:
        await first


async def test_published_update_and_disable_keep_inflight_snapshot(pg_factory, job_app, tmp_path):
    from xiaohe.deep_runtime.bundles import persist_runtime_bundle

    context, _ = await tool_context(pg_factory, tmp_path)
    spec = context.bundle.skills[context.binding.flow_skill]
    resources = context.bundle.resources | {spec.path + "/SKILL.md": "LIVE_NEW_GUIDE"}
    latest = context.bundle.model_copy(update={"resources": resources})
    async with pg_factory.begin() as db:
        await persist_runtime_bundle(db, latest)
    pg_factory.configure(
        info={"deepagents_enabled": False, "deep_runtime_bundle_id": latest.bundle_id}
    )
    model = make_script_model([final_response(wait_for_input=False)])
    await invoke(context, model=model)
    assert "LIVE_NEW_GUIDE" not in model.requests[0][0].text
    async with pg_factory() as db:
        assert (await db.get(Command, context.command_id)).payload["deepExecution"][
            "bundle_id"
        ] == context.bundle.bundle_id


@pytest.mark.parametrize("state", ["review", "accepted"])
@pytest.mark.parametrize("control", ["accept_invitation", "start_collection"])
async def test_collection_controls_do_not_use_model_or_create_extra_request(
    pg_factory, job_app, tmp_path, state, control
):
    context, frame = await tool_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {"receptionControl": control}
        (await db.get(ReceptionDraft, frame.draft_id)).state = state
    model = make_script_model([final_response(wait_for_input=True)])
    await invoke(context, model=model)
    assert not model.requests
    async with pg_factory() as db:
        assert len(list(await db.scalars(select(ReceptionDraft)))) == 1
