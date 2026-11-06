from shutil import copytree
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from test_admission import visitor
from test_agent_handoff import AgentSequenceModel, install_agents
from test_reception_flow import send_in_db, talk

from xiaohe.agents.runtime import load_agent_registry, run_agent_turn
from xiaohe.contracts import ServiceError
from xiaohe.models import Command, Run, Session
from xiaohe.runs import acquire_run
from xiaohe.workflows.definitions import RESOURCE_ROOT
from xiaohe.workflows.store import bundle_for_run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_worker_cancelling_guard_query_closes_with_rollback(
    pg_factory, runtime_env, monkeypatch
):
    import asyncio

    from sqlalchemy import text

    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "测试工作进程收尾")

    async def blocked_watch(fence, guard):
        await guard.execute(text("SELECT pg_sleep(30)"))

    monkeypatch.setattr(runtime_env.worker, "_watch", blocked_watch)
    await asyncio.wait_for(runtime_env.worker.execute_run(UUID(accepted.run.id)), 10)


async def kill_at_committed_phase(tmp_path, frame, phase):
    import asyncio
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    ready = tmp_path / "ready"
    config = tmp_path / "child.json"
    config.write_text(
        json.dumps({"frame": frame.model_dump(mode="json"), "phase": phase, "ready": str(ready)}),
        "utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "tests/xiaohe")])
    )
    with (tmp_path / "child.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).parent / "fixtures/agent_crash_worker.py"),
                str(config),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                if process.poll() is not None:
                    pytest.fail(
                        "isolated crash child exited before committed phase; inspect private child.log"
                    )
                await asyncio.sleep(0.1)
            assert ready.exists(), "child did not reach crash window"
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 10)
        assert process.returncode != 0


async def test_hard_kill_after_source_binding_recovers_target_once(pg_factory, job_app, tmp_path):
    from xiaohe.agents.nodes import build_agent_frame

    model = AgentSequenceModel(result={}, decisions=[{"action": "consultation"}])
    registry = await load_agent_registry(pg_factory, RESOURCE_ROOT, model, None)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "我想了解采购服务")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    frame = await build_agent_frame(pg_factory, fence, "xiaohe-reception", registry.bundle)
    await kill_at_committed_phase(tmp_path, frame, "source")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, fence.run_id, recover=True)
    result = await run_agent_turn(pg_factory, fence, bundle=registry.bundle, model=model)
    assert result.wait_for_input and len(model.agent_calls) == 1
    assert "采购 Agent" in model.agent_calls[0]


async def test_hard_kill_after_backend_success_reconciles_without_new_submission(
    pg_factory, job_app, tmp_path
):
    from sqlalchemy import text
    from test_agent_operations import cache_decision, prepare

    from xiaohe.agents.contracts import SemanticDecision
    from xiaohe.agents.nodes import make_node_executors
    from xiaohe.reception.contracts import SubmissionResult, SubmitReceipt
    from xiaohe.workflows.models import OperationNode

    bundle, frame = await prepare(pg_factory, tmp_path)
    decision = SemanticDecision(action="confirm_current_summary")
    await cache_decision(pg_factory, frame, decision)
    await kill_at_committed_phase(tmp_path, frame, "submission")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, frame.fence.run_id, recover=True)
    frame = frame.model_copy(update={"fence": fence})

    class Backend:
        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted",
                receipt=SubmitReceipt(
                    submission_key=key,
                    kind="service",
                    application_id="durable-effect",
                    revision=1,
                    status="accepted",
                ),
            )

        async def submit(self, actor, payload):
            raise AssertionError("must reconcile the committed effect")

    nodes = make_node_executors(pg_factory, model=None, business_client=Backend(), bundle=bundle)
    node = OperationNode(
        id="submit", name="提交", type="operation", operation="confirm_submission", next="done"
    )
    result = await nodes["operation"](
        node, {"frame": frame.model_dump(), "decision": decision.model_dump()}, {}
    )
    assert "需求已记录" in result["operation_result"]["text"]
    async with pg_factory() as db:
        assert (
            await db.scalar(
                text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:run"),
                {"run": fence.run_id},
            )
            == 1
        )


async def test_wait_restart_uses_original_bundle_and_target(
    pg_factory, runtime_env, monkeypatch, tmp_path
):
    root = tmp_path / "resources"
    copytree(RESOURCE_ROOT, root)
    model, registry = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "consultation", "target_direction": "buyer"},
            {"action": "consultation", "topics": ["buyer.rfx"]},
            {"action": "consultation", "topics": ["buyer.rfx"]},
        ],
        root,
    )
    who, conversation = await visitor(pg_factory)
    await talk(pg_factory, runtime_env, who, conversation, "采购如何收费")
    (root / "prompts/buyer-intent.md").write_text("新版本提示词哨兵", "utf-8")
    newer = await load_agent_registry(pg_factory, root, model, None)
    assert newer.bundle.bundle_id != registry.bundle.bundle_id
    from test_reception_flow import install

    await install(pg_factory, runtime_env, monkeypatch, model=model)
    await talk(pg_factory, runtime_env, who, conversation, "计费方式呢")
    assert "新版本提示词哨兵" not in str(model.requests)
    async with pg_factory() as db:
        run = await db.scalar(select(Run).where(Run.conversation_id == conversation))
        assert run.workflow_bundle_id == registry.bundle.bundle_id
        commands = (await db.scalars(select(Command).where(Command.run_id == run.id))).all()
        assert all(
            item.payload["agentExecution"]["bundleId"] == registry.bundle.bundle_id
            for item in commands
        )


async def test_recovery_after_source_saved_does_not_repeat_reception(
    pg_factory, job_app, tmp_path, monkeypatch
):
    model = AgentSequenceModel(
        result={},
        decisions=[
            {"action": "consultation", "target_direction": "buyer"},
            {"action": "consultation"},
        ],
    )
    registry = await load_agent_registry(pg_factory, RESOURCE_ROOT, model, None)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "我想了解采购服务")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        bundle = await bundle_for_run(db, fence.run_id)

    async def process_stopped(*args, **kwargs):
        raise RuntimeError("simulated stop before target graph")

    monkeypatch.setattr(registry["xiaohe-buyer"], "ainvoke", process_stopped)
    with pytest.raises(RuntimeError, match="simulated stop"):
        await run_agent_turn(pg_factory, fence, bundle=bundle, model=model, registry=registry)
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, fence.run_id, recover=True)
    result = await run_agent_turn(pg_factory, fence, bundle=bundle, model=model)
    assert result.wait_for_input
    assert sum("小鹤接待" in value for value in model.agent_calls) == 1
    assert sum("采购 Agent" in value for value in model.agent_calls) == 1


async def test_revocation_and_expiry_override_frozen_workflow(pg_factory, job_app):
    model = AgentSequenceModel(result={}, decisions=[])
    registry = await load_agent_registry(pg_factory, RESOURCE_ROOT, model, None)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "采购")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp())
        )
    with pytest.raises(ServiceError):
        await run_agent_turn(pg_factory, fence, bundle=registry.bundle, model=model)
    assert model.requests == []


async def test_late_model_result_cannot_write_pending_details(pg_factory, job_app, tmp_path):
    from langchain_core.runnables import RunnableLambda
    from test_agent_operations import prepare

    from xiaohe.agents.contracts import SemanticDecision
    from xiaohe.agents.nodes import make_node_executors
    from xiaohe.reception.models import ReceptionDraft
    from xiaohe.runs import cancel_run
    from xiaohe.workflows.models import UnderstandNode

    bundle, frame = await prepare(pg_factory, tmp_path, text="我司在苏州")

    class CancelDuringCall(AgentSequenceModel):
        def with_structured_output(self, schema, **kwargs):
            async def delayed(messages):
                async with pg_factory.begin() as db:
                    await cancel_run(db, frame.actor, frame.fence.run_id)
                return SemanticDecision.model_validate(
                    {
                        "action": "provide_details",
                        "candidates": {"city": {"value": "苏州", "evidence": "我司在苏州"}},
                    }
                )

            return RunnableLambda(delayed)

    executors = make_node_executors(
        pg_factory, model=CancelDuringCall(result={}), business_client=None, bundle=bundle
    )
    node = UnderstandNode(
        id="understand", name="理解", type="understand", prompt="intent", next="decide"
    )
    with pytest.raises(ServiceError, match="WRITE_FENCE_STALE"):
        await executors["understand"](node, {"frame": frame.model_dump()}, {})
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.revision == frame.draft_revision and "city" not in row.fields
