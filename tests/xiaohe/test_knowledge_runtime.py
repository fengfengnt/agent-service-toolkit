import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from knowledge_support import DeterministicEmbeddings, embedding_server, write_knowledge_source
from test_knowledge_index import build_child
from workflow_support import write_test_bundle

from xiaohe.knowledge.build import publish_index
from xiaohe.knowledge.sources import embedding_identity, load_sources, prepare_manifest
from xiaohe.settings import Settings
from xiaohe.workflows.loader import load_bundle
from xiaohe.workflows.models import AGENT_IDS


@pytest.fixture(scope="module")
def knowledge_release(tmp_path_factory):
    pytest.importorskip("chromadb")
    root = tmp_path_factory.mktemp("knowledge-release")
    with embedding_server() as (endpoint, requests):
        for agent in AGENT_IDS:
            write_knowledge_source(
                root / "sources",
                id=agent.removeprefix("xiaohe-"),
                agents=(agent,),
                topics=("general",),
                text=agent + "的虚构资料",
            )
        write_knowledge_source(
            root / "sources", id="buyer-rfx", topics=("buyer.rfx",), text="采购 RFX 虚构专用资料"
        )
        allowed = {agent: set() for agent in AGENT_IDS}
        allowed["xiaohe-buyer"] = {"buyer.rfx"}
        manifest, chunks = prepare_manifest(
            load_sources(root / "sources", allowed),
            embedding_model="fixture",
            embedding_identity=embedding_identity(endpoint, "fixture"),
            dimensions=3,
        )
        indexes = root / "indexes"
        indexes.mkdir()
        stage = indexes / ".stage"
        result = build_child(stage, manifest, chunks)
        assert result.returncode == 0, result.stderr
        publish_index(stage, indexes, manifest)
        yield indexes, manifest, endpoint, requests


def configured(tmp_path, release):
    indexes, manifest, endpoint, requests = release
    root = tmp_path / "indexes"
    shutil.copytree(indexes, root)
    resources = write_test_bundle(tmp_path / "resources")
    workflow = yaml.safe_load((resources / "workflows/buyer.yaml").read_text("utf-8"))
    workflow["skills"] = ["buyer.rfx"]
    (resources / "workflows/buyer.yaml").write_text(yaml.safe_dump(workflow), "utf-8")
    (resources / "skills/buyer/rfx").mkdir(parents=True)
    (resources / "skills/buyer/rfx/SKILL.md").write_text("测试兜底资料。", "utf-8")
    (resources / "skills/catalog.json").write_text(
        json.dumps(
            [
                {
                    "id": "buyer.rfx",
                    "direction": "buyer",
                    "title": "采购 RFX 说明",
                    "path": "buyer/rfx/SKILL.md",
                }
            ]
        ),
        "utf-8",
    )
    (resources / "knowledge").mkdir()
    (resources / "knowledge/manifest.json").write_text(manifest.model_dump_json(), "utf-8")
    settings = Settings(
        toolkit_base_url="http://localhost",
        auth_secret="fixture",
        knowledge_root=root,
        knowledge_embedding_base_url=endpoint,
        knowledge_embedding_model="fixture",
        knowledge_embedding_api_key="fixture-private",
        knowledge_embedding_dimensions=3,
    )
    return settings, load_bundle(resources), requests


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", AGENT_IDS)
async def test_each_agent_reads_only_explicitly_authorized_general_material(
    tmp_path, knowledge_release, agent
):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, requests = configured(tmp_path, knowledge_release)
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    try:
        hits = await knowledge.query(bundle, agent, [])
        assert [hit.text for hit in hits] == [agent + "的虚构资料"]
        assert hits[0].display_source == "测试审定资料"
        assert hits[0].version == knowledge_release[1].version
        assert hits[0].source.startswith("kb:" + hits[0].version + ":")
        assert requests[-1]["input"] == ["平台介绍与服务方向"]
    finally:
        await knowledge.aclose()


@pytest.mark.asyncio
async def test_unconfigured_knowledge_leaves_no_files_and_uses_no_executor(tmp_path):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings = Settings(
        toolkit_base_url="http://localhost",
        auth_secret="fixture",
        knowledge_root=tmp_path / "absent",
    )
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    assert (
        await knowledge.query(
            load_bundle(write_test_bundle(tmp_path / "resources")), "xiaohe-buyer", []
        )
        == []
    )
    assert not settings.knowledge_root.exists()
    await knowledge.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["missing", "marker", "model", "dimensions", "endpoint", "unknown_topic"]
)
async def test_invalid_version_or_scope_never_falls_forward(tmp_path, knowledge_release, mutation):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, requests = configured(tmp_path, knowledge_release)
    version = settings.knowledge_root / knowledge_release[1].version
    if mutation == "missing":
        (version / "manifest.json").unlink()
    elif mutation == "marker":
        (version / "complete.json").write_text("{}", "utf-8")
    elif mutation == "model":
        settings.knowledge_embedding_model = "different"
    elif mutation == "dimensions":
        settings.knowledge_embedding_dimensions = 4
    elif mutation == "endpoint":
        settings.knowledge_embedding_base_url += "/other"
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    before = len(requests)
    try:
        assert (
            await knowledge.query(
                bundle, "xiaohe-buyer", ["supplier.security"] if mutation == "unknown_topic" else []
            )
            == []
        )
        assert len(requests) == before
    finally:
        await knowledge.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["body", "scope"])
async def test_database_payload_is_checked_against_trusted_manifest(
    tmp_path, knowledge_release, tamper
):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, _ = configured(tmp_path, knowledge_release)
    directory = settings.knowledge_root / knowledge_release[1].version
    code = """
import chromadb,sys
c=chromadb.PersistentClient(path=sys.argv[1]).get_collection('xiaohe_knowledge',embedding_function=None)
if sys.argv[2]=='body':
    c.update(ids=['buyer:0'],documents=['篡改正文'],embeddings=[[1.,0.,0.]])
else:
    c.update(ids=['supplier:0'],metadatas=[{'version':sys.argv[3],'agent.xiaohe-buyer':True,'topic.general':True}])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(directory), tamper, knowledge_release[1].version],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    try:
        hits = await knowledge.query(bundle, "xiaohe-buyer", [])
        assert [hit.text for hit in hits] == (
            [] if tamper == "body" else ["xiaohe-buyer的虚构资料"]
        )
    finally:
        await knowledge.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timed_out_or_cancelled_thread_keeps_admission_until_finished(
    tmp_path, knowledge_release, monkeypatch, cancel
):
    from xiaohe.knowledge import runtime

    settings, bundle, _ = configured(tmp_path, knowledge_release)
    knowledge = runtime.EmbeddedKnowledge(settings)
    monkeypatch.setattr(runtime, "QUERY_TIMEOUT", 0.02)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow(*args):
        calls.append(1)
        entered.set()
        release.wait(5)
        return []

    monkeypatch.setattr(knowledge, "_retrieve", slow)
    await knowledge.start()
    try:
        first = asyncio.create_task(knowledge.query(bundle, "xiaohe-buyer", []))
        assert await asyncio.to_thread(entered.wait, 3)
        if cancel:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            assert await first == []
        assert await knowledge.query(bundle, "xiaohe-buyer", []) == []
        assert len(calls) == 1
    finally:
        release.set()
        await knowledge.aclose()


@pytest.mark.asyncio
async def test_second_process_cannot_open_active_index_root(tmp_path, knowledge_release):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, _ = configured(tmp_path, knowledge_release)
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    payload = settings.model_dump(
        mode="json",
        include={
            "toolkit_base_url",
            "knowledge_root",
            "knowledge_embedding_base_url",
            "knowledge_embedding_model",
            "knowledge_embedding_dimensions",
        },
    )
    payload.update(auth_secret="fixture", knowledge_embedding_api_key="fixture-private")
    code = """
import asyncio,json,sys
from xiaohe.knowledge.runtime import EmbeddedKnowledge
from xiaohe.settings import Settings
async def main():
    k=EmbeddedKnowledge(Settings(**json.loads(sys.argv[1])))
    await k.start()
    assert not k.enabled
    await k.aclose()
asyncio.run(main())
"""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code, json.dumps(payload)],
            env=env,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert len(await knowledge.query(bundle, "xiaohe-buyer", [])) == 1
    finally:
        await knowledge.aclose()


@pytest.mark.asyncio
async def test_irrelevant_embedding_is_not_returned(tmp_path):
    pytest.importorskip("chromadb")
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    with embedding_server() as (endpoint, requests):
        source = tmp_path / "sources"
        write_knowledge_source(source, topics=("general",), text="无关的虚构资料")
        manifest, chunks = prepare_manifest(
            load_sources(source, {"xiaohe-buyer": set()}),
            embedding_model="fixture",
            embedding_identity=embedding_identity(endpoint, "fixture"),
            dimensions=3,
        )
        indexes = tmp_path / "release"
        indexes.mkdir()
        stage = indexes / ".stage"
        assert build_child(stage, manifest, chunks).returncode == 0
        publish_index(stage, indexes, manifest)
        settings, bundle, _ = configured(
            tmp_path / "query", (indexes, manifest, endpoint, requests)
        )
        knowledge = EmbeddedKnowledge(settings)
        await knowledge.start()
        try:
            assert await knowledge.query(bundle, "xiaohe-buyer", []) == []
        finally:
            await knowledge.aclose()


def test_process_exit_releases_owner_lock(tmp_path, knowledge_release):
    settings, _, _ = configured(tmp_path, knowledge_release)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    payload = {
        "toolkit_base_url": "http://localhost",
        "auth_secret": "fixture",
        "knowledge_root": str(settings.knowledge_root),
        "knowledge_embedding_base_url": settings.knowledge_embedding_base_url,
        "knowledge_embedding_model": "fixture",
        "knowledge_embedding_dimensions": 3,
        "knowledge_embedding_api_key": "fixture-private",
    }
    code = """
import asyncio,json,sys
from xiaohe.knowledge.runtime import EmbeddedKnowledge
from xiaohe.settings import Settings
async def main():
    k=EmbeddedKnowledge(Settings(**json.loads(sys.argv[1])))
    await k.start()
    print('enabled' if k.enabled else 'blocked',flush=True)
    if sys.argv[2]=='hold': sys.stdin.readline()
    await k.aclose()
asyncio.run(main())
"""
    args = [sys.executable, "-c", code, json.dumps(payload)]
    owner = subprocess.Popen(
        args + ["hold"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout.readline().strip() == "enabled"
        denied = subprocess.run(
            args + ["probe"], env=env, capture_output=True, text=True, timeout=30
        )
        assert denied.returncode == 0
        assert denied.stdout.strip() == "blocked"
    finally:
        owner.communicate("\n", timeout=30)
    allowed = subprocess.run(args + ["probe"], env=env, capture_output=True, text=True, timeout=30)
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == "enabled"


@pytest.mark.asyncio
async def test_ready_host_has_prepared_embedding_client_for_first_query(
    tmp_path, knowledge_release, monkeypatch
):
    from xiaohe.knowledge import build
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, _ = configured(tmp_path, knowledge_release)
    ready = False

    def initialize(settings):
        if ready:
            raise AssertionError("client initialization must finish before readiness")
        return DeterministicEmbeddings()

    monkeypatch.setattr(build, "create_embeddings", initialize)
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    ready = True
    try:
        assert len(await knowledge.query(bundle, "xiaohe-buyer", [])) == 1
    finally:
        await knowledge.aclose()


@pytest.mark.asyncio
async def test_professional_query_uses_approved_title_and_topic_filter(tmp_path, knowledge_release):
    from xiaohe.knowledge.runtime import EmbeddedKnowledge

    settings, bundle, requests = configured(tmp_path, knowledge_release)
    knowledge = EmbeddedKnowledge(settings)
    await knowledge.start()
    try:
        hits = await knowledge.query(bundle, "xiaohe-buyer", ["buyer.rfx"])
        assert {hit.text for hit in hits} == {"xiaohe-buyer的虚构资料", "采购 RFX 虚构专用资料"}
        assert requests[-1]["input"] == ["采购 RFX 说明"]
    finally:
        await knowledge.aclose()
