import asyncio
import json
from shutil import copytree
from uuid import UUID

import pytest
from knowledge_support import write_knowledge_source
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr
from test_admission import visitor
from test_agent_semantics import ScriptedModel, prepared
from test_reception_flow import send_in_db
from test_reception_graph import DecisionModel

from xiaohe.agents.contracts import SemanticDecision
from xiaohe.agents.runtime import load_agent_registry, run_agent_turn
from xiaohe.agents.semantic import compose_answer
from xiaohe.knowledge.sources import (
    KnowledgeMaterial,
    load_sources,
    manifest_from_bundle,
    prepare_manifest,
)
from xiaohe.models import Conversation
from xiaohe.runs import acquire_run
from xiaohe.workflows.definitions import RESOURCE_ROOT
from xiaohe.workflows.loader import load_bundle


class GroundedModel(DecisionModel):
    decisions: list[dict] = []
    body: str = "测试答复。"

    def with_structured_output(self, schema, **kwargs):
        async def respond(messages):
            self.requests.append(messages)
            if schema.__name__ == "SemanticDecision":
                return self.decisions.pop(0)
            payload = json.loads(messages[-1].content)
            ids = [
                item["source"] for item in payload["materials"] if item["source"].startswith("kb:")
            ]
            return {
                "text": self.body,
                "basis": "confirmed_material" if ids else "needs_confirmation",
                "source_ids": ids,
            }

        return RunnableLambda(respond)


class KnowledgeFixture:
    def __init__(self, unavailable=False):
        self.calls = []
        self.unavailable = unavailable

    async def query(self, bundle, agent_id, topics):
        self.calls.append((bundle.bundle_id, agent_id, list(topics)))
        if self.unavailable:
            raise TimeoutError("private upstream detail")
        manifest = manifest_from_bundle(bundle)
        if manifest is None:
            return []
        key, entry = next(iter(manifest.entries.items()))
        return [
            KnowledgeMaterial(
                text="仅用于测试的虚构政策。",
                source=f"kb:{manifest.version}:{key}",
                display_source=entry.source,
                version=manifest.version,
                conditions=entry.conditions,
            )
        ]


def resources_with_knowledge(tmp_path):
    root = tmp_path / "resources"
    copytree(RESOURCE_ROOT, root)
    write_knowledge_source(
        root / "knowledge",
        agents=("xiaohe-reception", "xiaohe-buyer", "xiaohe-organization", "xiaohe-supplier"),
        topics=("general",),
    )
    manifest, _ = prepare_manifest(
        load_sources(
            root / "knowledge",
            {key: set(value.skills) for key, value in load_bundle(root).definitions.items()},
        ),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )
    (root / "knowledge/manifest.json").write_text(manifest.model_dump_json(), "utf-8")
    return root, manifest


@pytest.mark.asyncio
async def test_compose_receives_version_and_display_source(tmp_path):
    _, bundle, frame = prepared(tmp_path)
    material = KnowledgeMaterial(
        text="虚构资料",
        source="kb:fixture:policy:0",
        version="a" * 64,
        display_source="测试审定资料",
        conditions="仅适用测试场景",
    )
    model = ScriptedModel(
        result={},
        responses={
            "AnswerDraft": {
                "text": "测试答复",
                "basis": "confirmed_material",
                "source_ids": [material.source],
            }
        },
    )
    await compose_answer(
        model, frame, SemanticDecision(action="consultation"), [material], "依据资料回答"
    )
    payload = json.loads(model.requests[0][-1].content)
    assert payload["materials"][0]["version"] == "a" * 64
    assert payload["materials"][0]["display_source"] == "测试审定资料"


async def begin(factory, registry, *, direction="unknown", text="介绍平台的服务范围"):
    who, conversation = await visitor(factory)
    async with factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = direction
        accepted = await send_in_db(db, who, conversation, text)
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    return fence


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent,topic",
    [
        ("xiaohe-reception", None),
        ("xiaohe-buyer", "buyer.rfx"),
        ("xiaohe-organization", "organization.cooperation"),
        ("xiaohe-supplier", "supplier.security"),
    ],
)
async def test_four_agents_include_verified_citations_once(
    pg_factory, job_app, tmp_path, agent, topic
):
    root, manifest = resources_with_knowledge(tmp_path)
    knowledge = KnowledgeFixture()
    model = GroundedModel(
        result={},
        body="答" * 4000,
        decisions=[
            {"action": "consultation" if topic else "unclear", "topics": [topic] if topic else []}
        ],
    )
    registry = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    fence = await begin(
        pg_factory, registry, direction=agent.removeprefix("xiaohe-") if topic else "unknown"
    )
    first = await run_agent_turn(
        pg_factory, fence, bundle=registry.bundle, entry_agent=agent, model=model, registry=registry
    )
    second = await run_agent_turn(
        pg_factory, fence, bundle=registry.bundle, entry_agent=agent, model=model, registry=registry
    )
    texts = [item.text for item in first.parts if item.type == "text"]
    assert len(texts) == 2
    assert texts[0].startswith("答" * 4000)
    assert texts[1].count("测试审定资料") == 1
    assert manifest.version in texts[1] and "仅适用测试场景" in texts[1]
    assert first == second
    assert knowledge.calls == [(registry.bundle.bundle_id, agent, [topic] if topic else [])]


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize("off_topic", [False, True])
async def test_unavailable_or_off_topic_keeps_existing_answer(
    pg_factory, job_app, tmp_path, off_topic
):
    root, _ = resources_with_knowledge(tmp_path)
    knowledge = KnowledgeFixture(unavailable=True)
    model = GroundedModel(
        result={}, decisions=[{"action": "off_topic" if off_topic else "unclear"}]
    )
    registry = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    fence = await begin(pg_factory, registry)
    result = await run_agent_turn(
        pg_factory, fence, bundle=registry.bundle, model=model, registry=registry
    )
    texts = [item.text for item in result.parts if item.type == "text"]
    assert texts == ["测试答复。"]
    assert "private upstream" not in str(result)
    assert len(knowledge.calls) == (0 if off_topic else 1)


@pytest.mark.pg
@pytest.mark.asyncio
async def test_old_bundle_registry_rebuild_keeps_bound_knowledge_version(
    pg_factory, job_app, tmp_path
):
    root, old_manifest = resources_with_knowledge(tmp_path)
    knowledge = KnowledgeFixture()
    model = GroundedModel(result={}, decisions=[{"action": "unclear"}])
    old = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    fence = await begin(pg_factory, old)
    write_knowledge_source(root / "knowledge", topics=("general",), text="新版本虚构资料")
    newer, _ = prepare_manifest(
        load_sources(root / "knowledge", {"xiaohe-buyer": set()}),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )
    (root / "knowledge/manifest.json").write_text(newer.model_dump_json(), "utf-8")
    current = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    result = await run_agent_turn(
        pg_factory, fence, bundle=old.bundle, model=model, registry=current
    )
    visible = str(result.parts)
    assert old_manifest.version in visible
    assert newer.version not in visible
    assert knowledge.calls[0][0] == old.bundle.bundle_id


@pytest.mark.pg
@pytest.mark.asyncio
async def test_handoff_uses_target_agent_scope(pg_factory, job_app, tmp_path):
    root, _ = resources_with_knowledge(tmp_path)
    knowledge = KnowledgeFixture()
    model = GroundedModel(
        result={},
        decisions=[
            {"action": "consultation", "target_direction": "buyer"},
            {"action": "consultation", "topics": ["buyer.rfx"]},
        ],
    )
    registry = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    fence = await begin(pg_factory, registry, text="采购怎么收费")
    result = await run_agent_turn(
        pg_factory, fence, bundle=registry.bundle, model=model, registry=registry
    )
    assert any("资料依据" in item.text for item in result.parts if item.type == "text")
    assert knowledge.calls == [(registry.bundle.bundle_id, "xiaohe-buyer", ["buyer.rfx"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_startup_failure_restores_registry_even_when_knowledge_close_is_cancelled(
    monkeypatch, cancel_close
):
    from agents.agents import agents
    from core.settings import DatabaseType
    from core.settings import settings as upstream
    from xiaohe import main
    from xiaohe.agents import runtime
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
    monkeypatch.setattr(runtime, "load_agent_registry", fail)
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
            pytest.fail("startup must fail before serving requests")
    assert main._active_app is None
    assert agents.get("xiaohe") is previous


@pytest.mark.pg
@pytest.mark.asyncio
async def test_new_run_for_old_draft_keeps_draft_knowledge_bundle(pg_factory, job_app, tmp_path):
    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft

    root, old_manifest = resources_with_knowledge(tmp_path)
    knowledge = KnowledgeFixture()
    model = GroundedModel(
        result={}, decisions=[{"action": "consultation", "topics": ["buyer.rfx"]}]
    )
    old = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="old-knowledge-draft", direction="buyer", kind="service"),
        )
    write_knowledge_source(root / "knowledge", topics=("general",), text="新发布的虚构政策")
    newer, _ = prepare_manifest(
        load_sources(root / "knowledge", {"xiaohe-buyer": set()}),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )
    (root / "knowledge/manifest.json").write_text(newer.model_dump_json(), "utf-8")
    current = await load_agent_registry(pg_factory, root, model, None, knowledge=knowledge)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "继续咨询采购收费")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    result = await run_agent_turn(
        pg_factory, fence, bundle=current.bundle, model=model, registry=current
    )
    assert old_manifest.version in str(result.parts)
    assert newer.version not in str(result.parts)
    assert knowledge.calls[0][0] == old.bundle.bundle_id
