import pytest
from deep_runtime_support import DeepScriptModel, make_script_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_deep_factory import final_response
from test_deep_runtime import invoke, runtime
from test_deep_tools import tool_context

from xiaohe.models import Command

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_completed_native_turn_reuses_outcome_without_input(pg_factory, job_app, tmp_path):
    context, _ = await tool_context(pg_factory, tmp_path)
    model = make_script_model([final_response(wait_for_input=False)])
    saver = InMemorySaver()
    first = await invoke(context, model=model, saver=saver)
    assert await invoke(context, model=model, saver=saver) == first
    assert len(model.requests) == 1
    async with pg_factory() as db:
        threads = runtime().deep_thread_ids(await db.get(Command, context.command_id))
    snapshot = await saver.aget_tuple(
        {"configurable": {"thread_id": next(iter(threads)), "checkpoint_ns": ""}}
    )
    assert snapshot is not None


async def test_native_requires_existing_saver(pg_factory, job_app, tmp_path):
    from xiaohe.contracts import ServiceError
    from xiaohe.deep_runtime.models import DeepExecution

    context, _ = await tool_context(pg_factory, tmp_path)
    with pytest.raises(ServiceError, match="CHECKPOINT_NOT_READY"):
        await runtime().run_deep_turn(
            pg_factory,
            context.fence,
            execution=DeepExecution(bundle_id=context.bundle.bundle_id),
            model=None,
            business_client=None,
            checkpointer=None,
        )


async def test_checkpoint_recovery_does_not_duplicate_user_input(pg_factory, job_app, tmp_path):
    class FailsOnce(DeepScriptModel):
        failed: bool = False

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("isolated model failure")
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    context, _ = await tool_context(pg_factory, tmp_path)
    model = FailsOnce(
        responses=[final_response(wait_for_input=False)], profile={"tool_calling": True}
    )
    saver = InMemorySaver()
    with pytest.raises(RuntimeError, match="isolated model failure"):
        await invoke(context, model=model, saver=saver)
    await invoke(context, model=model, saver=saver)
    assert (
        sum(isinstance(m, HumanMessage) and m.content == context.text for m in model.requests[0])
        == 1
    )


async def test_save_survives_model_failure_with_server_notice(pg_factory, job_app, tmp_path):
    class FailsAfterSave(DeepScriptModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.requests:
                raise RuntimeError("isolated failure after tool commit")
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州")
    model = FailsAfterSave(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="save_lead",
                        args={
                            "draft_id": str(frame.draft_id),
                            "expected_revision": frame.draft_revision,
                            "candidates": {"city": {"value": "苏州", "evidence": "苏州"}},
                        },
                        id="save-before-crash",
                    )
                ],
            )
        ],
        profile={"tool_calling": True},
    )
    result = await invoke(context, model=model)
    assert any("保存" in getattr(part, "text", "") for part in result.parts)


async def test_login_control_cannot_be_submit_confirmation(pg_factory, job_app, tmp_path):
    from test_deep_tools import tools_module

    from xiaohe.contracts import ServiceError

    context, frame = await tool_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {"receptionControl": "start_collection"}
    with pytest.raises(ServiceError):
        await tools_module().execute_effect(
            context,
            "submit_lead",
            draft_id=frame.draft_id,
            expected_revision=frame.draft_revision,
            payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
        )


async def test_native_threads_deleted_with_guest(pg_factory, runtime_env):
    from uuid import UUID, uuid4

    from langgraph.checkpoint.base import empty_checkpoint
    from sqlalchemy import select
    from test_admission import send, visitor

    from xiaohe.expiry import _delete_guest_bodies

    who, conversation_id = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation_id, "test guest")
    native_thread = str(uuid4())
    saver = runtime_env.graph.checkpointer
    configs = [
        {"configurable": {"thread_id": native_thread, "checkpoint_ns": ns}}
        for ns in ("", "child:1")
    ]
    for config in configs:
        await saver.aput(
            config, empty_checkpoint(), {"source": "input", "step": 0, "parents": {}}, {}
        )
    async with pg_factory.begin() as db:
        command = await db.scalar(select(Command).where(Command.run_id == UUID(accepted.run.id)))
        command.payload = command.payload | {
            "deepExecution": {"threads": {"test": {"id": native_thread}}}
        }
    async with pg_factory.begin() as db:
        await _delete_guest_bodies(db, [conversation_id], [UUID(accepted.run.id)])
    assert all([await saver.aget_tuple(config) is None for config in configs])
