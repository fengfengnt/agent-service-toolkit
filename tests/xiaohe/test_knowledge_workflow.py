import asyncio

import pytest
from deep_runtime_support import make_script_model
from knowledge_support import write_knowledge_source, write_test_bundle
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from test_deep_factory import final_response
from test_deep_tools import tool_context

from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime.bundles import load_runtime_bundle, persist_runtime_bundle
from xiaohe.deep_runtime.models import DeepExecution
from xiaohe.deep_runtime.runtime import publish_runtime, run_deep_turn
from xiaohe.knowledge.sources import (
    KnowledgeMaterial,
    load_sources,
    manifest_from_bundle,
    prepare_manifest,
)
from xiaohe.models import Command, Conversation, Message, Run
from xiaohe.skill_schema import AGENT_IDS

pytestmark = pytest.mark.asyncio


class KnowledgeFixture:
    def __init__(self, unavailable=False):
        self.calls = []
        self.unavailable = unavailable

    async def query(self, bundle, agent_id, topics):
        self.calls.append((bundle.bundle_id, agent_id, list(topics)))
        if self.unavailable:
            raise TimeoutError("private upstream detail")
        manifest = manifest_from_bundle(bundle)
        key, entry = next(iter(manifest.entries.items()))
        return [
            KnowledgeMaterial(
                text="仅用于测试的虚构政策。",
                source=f"kb:{manifest.version}:{key}",
                version=manifest.version,
                display_source=entry.source,
                conditions=entry.conditions,
            )
        ]


async def bound_context(factory, tmp_path, agent="xiaohe-buyer"):
    role = agent.removeprefix("xiaohe-")
    context, _ = await tool_context(
        factory, tmp_path, role="buyer" if role == "reception" else role
    )
    root = write_test_bundle(tmp_path / "resources")
    write_knowledge_source(root / "knowledge", agents=AGENT_IDS, topics=("general",))
    manifest, _ = prepare_manifest(
        load_sources(root / "knowledge", {}),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )
    (root / "knowledge/manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    bundle = load_runtime_bundle(root)
    async with factory.begin() as db:
        await persist_runtime_bundle(db, bundle)
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {
            "deepExecution": DeepExecution(bundle_id=bundle.bundle_id, active=agent).model_dump(
                mode="json"
            )
        }
        (await db.get(Run, context.fence.run_id)).deep_runtime_bundle_id = bundle.bundle_id
        if role == "reception":
            (await db.get(Conversation, context.conversation_id)).direction = "unknown"
    return context, bundle, root


def source_id(bundle):
    manifest = manifest_from_bundle(bundle)
    return f"kb:{manifest.version}:{next(iter(manifest.entries))}"


async def invoke(context, model, knowledge, saver=None):
    async with context.factory() as db:
        command = await db.get(Command, context.command_id)
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
    return await run_deep_turn(
        context.factory,
        context.fence,
        execution=execution,
        model=model,
        business_client=context.business_client,
        checkpointer=saver or InMemorySaver(),
        knowledge=knowledge,
    )


@pytest.mark.pg
@pytest.mark.parametrize("agent", AGENT_IDS)
async def test_four_agents_include_only_used_verified_citations(
    pg_factory, job_app, tmp_path, agent
):
    context, bundle, _ = await bound_context(pg_factory, tmp_path, agent)
    knowledge = KnowledgeFixture()
    model = make_script_model(
        [
            final_response(
                answer={
                    "text": "答" * 4000,
                    "basis": "confirmed_material",
                    "source_ids": [source_id(bundle)],
                }
            )
        ]
    )
    first = await invoke(context, model, knowledge)
    assert await invoke(context, model, knowledge) == first
    texts = [p.text for p in first.parts if p.type == "text"]
    assert len(texts) == 2 and texts[0] == "答" * 4000
    assert texts[1].count("测试审定资料") == 1 and "仅适用测试场景" in texts[1]
    assert manifest_from_bundle(bundle).version not in texts[1]
    prompt = model.requests[0][0].text
    assert source_id(bundle) in prompt and "display_source" in prompt and "conditions" in prompt
    assert len(knowledge.calls) == 1 and knowledge.calls[0][1] == agent


@pytest.mark.pg
async def test_unavailable_knowledge_keeps_skill_answer(pg_factory, job_app, tmp_path):
    context, _, _ = await bound_context(pg_factory, tmp_path)
    model = make_script_model(
        [final_response(answer={"text": "请说明需求。", "basis": "general_advice"})]
    )
    outcome = await invoke(context, model, KnowledgeFixture(unavailable=True))
    assert [p.text for p in outcome.parts if p.type == "text"] == ["请说明需求。"]
    assert "private upstream" not in str(outcome)


@pytest.mark.pg
@pytest.mark.parametrize("forged", [False, True])
async def test_unreferenced_material_is_not_cited_and_forged_source_is_rejected(
    pg_factory, job_app, tmp_path, forged
):
    context, _, _ = await bound_context(pg_factory, tmp_path)
    model = make_script_model(
        [
            final_response(
                answer={
                    "text": "还需确认范围。",
                    "basis": "needs_confirmation",
                    "source_ids": ["kb:forged:policy:0"] if forged else [],
                }
            )
        ]
    )
    if forged:
        with pytest.raises(ServiceError):
            await invoke(context, model, KnowledgeFixture())
    else:
        outcome = await invoke(context, model, KnowledgeFixture())
        assert [p.text for p in outcome.parts if p.type == "text"] == ["还需确认范围。"]


@pytest.mark.pg
async def test_pending_command_keeps_published_knowledge_snapshot(pg_factory, job_app, tmp_path):
    context, old, root = await bound_context(pg_factory, tmp_path)
    write_knowledge_source(
        root / "knowledge", agents=AGENT_IDS, topics=("general",), text="新发布资料"
    )
    newer, _ = prepare_manifest(
        load_sources(root / "knowledge", {}),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )
    (root / "knowledge/manifest.json").write_text(newer.model_dump_json(), encoding="utf-8")
    await publish_runtime(pg_factory, root)
    current = load_runtime_bundle(root)
    assert current.bundle_id != old.bundle_id
    knowledge = KnowledgeFixture()
    model = make_script_model(
        [
            final_response(
                answer={
                    "text": "请确认适用范围。",
                    "basis": "confirmed_material",
                    "source_ids": [source_id(old)],
                }
            )
        ]
    )
    await invoke(context, model, knowledge)
    assert knowledge.calls[0][0] == old.bundle_id
    assert newer.version not in model.requests[0][0].text


@pytest.mark.pg
async def test_handoff_retrieves_with_target_binding(pg_factory, job_app, tmp_path):
    context, bundle, _ = await bound_context(pg_factory, tmp_path, "xiaohe-reception")
    knowledge = KnowledgeFixture()
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
            final_response(
                answer={
                    "text": "请介绍采购需求。",
                    "basis": "confirmed_material",
                    "source_ids": [source_id(bundle)],
                }
            ),
        ]
    )
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        (await db.get(Message, command.message_id)).parts = [{"type": "text", "text": "采购方"}]
    await invoke(context, model, knowledge)
    assert [c[1] for c in knowledge.calls] == ["xiaohe-reception", "xiaohe-buyer"]
    assert set(knowledge.calls[-1][2]) == set(bundle.bindings["xiaohe-buyer"].skills)


@pytest.mark.pg
async def test_parent_checkpoint_retry_restores_materials_without_retrieval(
    pg_factory, job_app, tmp_path, monkeypatch
):
    from xiaohe.deep_runtime import runtime
    from xiaohe.graph import build_graph

    context, bundle, _ = await bound_context(pg_factory, tmp_path)
    knowledge = KnowledgeFixture()
    model = make_script_model(
        [
            final_response(
                answer={
                    "text": "请确认范围。",
                    "basis": "confirmed_material",
                    "source_ids": [source_id(bundle)],
                },
                wait_for_input=False,
            )
        ]
    )
    checks = iter([True, False])

    def fail_once(text):
        if next(checks):
            raise RuntimeError("isolated finalize failure")
        return False

    monkeypatch.setattr(runtime, "_claims_business_success", fail_once)
    graph = build_graph(factory=pg_factory, model=model, knowledge=knowledge)
    graph.checkpointer = InMemorySaver()
    config = {
        "configurable": {"thread_id": "knowledge-retry"},
        "metadata": {
            "business_run_id": str(context.fence.run_id),
            "attempt": context.fence.attempt,
            "ownership_epoch": context.fence.ownership_epoch,
        },
    }
    with pytest.raises(RuntimeError, match="isolated finalize failure"):
        await graph.ainvoke(
            {"run_id": str(context.fence.run_id), "output_message_id": str(context.command_id)},
            config=config,
        )
    result = await graph.ainvoke(None, config=config)
    assert "测试审定资料" in str(result["outcome"])
    assert len(knowledge.calls) == 1


@pytest.mark.pg
async def test_static_welcome_does_not_request_embeddings(pg_factory, job_app, tmp_path):
    context, _, _ = await bound_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {"receptionControl": "switch_direction"}
    model = make_script_model([])
    knowledge = KnowledgeFixture()
    await invoke(context, model, knowledge)
    assert knowledge.calls == [] and model.requests == []


@pytest.mark.parametrize("cancel_close", [False, True])
async def test_startup_failure_restores_registry_and_closes_knowledge(monkeypatch, cancel_close):
    from agents.agents import agents
    from core.settings import DatabaseType
    from core.settings import settings as upstream
    from xiaohe import main
    from xiaohe.deep_runtime import runtime
    from xiaohe.settings import Settings

    class Owner:
        async def start(self):
            pass

        async def aclose(self):
            if cancel_close:
                raise asyncio.CancelledError()

    async def fail(*args, **kwargs):
        raise RuntimeError("fixture startup failure")

    monkeypatch.setattr(upstream, "DATABASE_TYPE", DatabaseType.POSTGRES)
    monkeypatch.setattr(upstream, "AUTH_SECRET", SecretStr("fixture"))
    monkeypatch.setattr(main, "EmbeddedKnowledge", lambda settings: Owner())
    monkeypatch.setattr(runtime, "publish_runtime", fail)
    previous = agents.get("xiaohe")
    app = main.create_app(
        Settings(
            database_url="postgresql+psycopg://fixture@127.0.0.1:1/test_xiaohe_unused",
            toolkit_base_url="http://localhost",
            auth_secret="fixture",
        )
    )
    with pytest.raises(asyncio.CancelledError if cancel_close else RuntimeError):
        async with app.router.lifespan_context(app):
            pytest.fail("startup must fail")
    assert main._active_app is None and agents.get("xiaohe") is previous
