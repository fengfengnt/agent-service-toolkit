import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from knowledge_support import embedding_server, write_knowledge_source
from workflow_support import write_test_bundle

from xiaohe.knowledge.sources import load_sources, prepare_manifest


def inputs(tmp_path, **changes):
    sources = tmp_path / "sources"
    write_knowledge_source(sources, topics=("general",), **changes)
    return prepare_manifest(
        load_sources(sources, {"xiaohe-buyer": set()}),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
    )


def build_child(staging, manifest, chunks, vector=None):
    payload = staging.parent / (staging.name + "-input.json")
    payload.write_text(
        json.dumps(
            {
                "manifest": manifest.model_dump(mode="json"),
                "chunks": [item.model_dump() for item in chunks],
            }
        ),
        "utf-8",
    )
    code = """
import json, sys
from pathlib import Path
from knowledge_support import DeterministicEmbeddings
from xiaohe.knowledge.build import build_index
from xiaohe.knowledge.sources import KnowledgeChunk, read_manifest
data = json.loads(Path(sys.argv[1]).read_text('utf-8'))
class TestEmbeddings(DeterministicEmbeddings):
    def embed_documents(self, texts):
        vector = json.loads(sys.argv[3])
        return super().embed_documents(texts) if vector is None else [vector for _ in texts]
build_index(read_manifest(json.dumps(data['manifest'])),
            [KnowledgeChunk(**item) for item in data['chunks']], Path(sys.argv[2]), TestEmbeddings())
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path("src").resolve()), str(Path("tests/xiaohe").resolve())]
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(payload), str(staging), json.dumps(vector)],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_incomplete_build_cannot_be_published(tmp_path):
    from xiaohe.knowledge.build import publish_index

    manifest, _ = inputs(tmp_path)
    stage = tmp_path / "indexes/.staging"
    stage.mkdir(parents=True)
    with pytest.raises(ValueError):
        publish_index(stage, tmp_path / "indexes", manifest)
    assert not (tmp_path / "indexes" / manifest.version).exists()


def test_real_index_survives_process_exit_and_publish(tmp_path):
    pytest.importorskip("chromadb")
    from xiaohe.knowledge.build import publish_index

    manifest, chunks = inputs(tmp_path)
    root = tmp_path / "indexes"
    root.mkdir()
    stage = root / ".staging"
    result = build_child(stage, manifest, chunks)
    assert result.returncode == 0, result.stderr
    target = publish_index(stage, root, manifest)
    assert target.name == manifest.version
    assert not stage.exists()
    assert (target / "chroma.sqlite3").exists()
    # 再打开客户端的进程也退出，避免测试进程持有 Windows 索引句柄。
    code = "import chromadb,sys; c=chromadb.PersistentClient(path=sys.argv[1]); assert c.get_collection('xiaohe_knowledge').get()['documents']==['仅用于测试的虚构政策。']"
    check = subprocess.run(
        [sys.executable, "-c", code, str(target)], capture_output=True, timeout=45
    )
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize("vector", [[1.0, 0.0], [float("nan"), 0.0, 0.0], [float("inf"), 0.0, 0.0]])
def test_bad_embeddings_never_complete_build(tmp_path, vector):
    pytest.importorskip("chromadb")
    manifest, chunks = inputs(tmp_path)
    stage = tmp_path / "stage"
    result = build_child(stage, manifest, chunks, vector)
    assert result.returncode != 0
    assert not (stage / "complete.json").exists()


def test_same_version_cannot_overwrite_corrupt_existing_directory(tmp_path):
    from xiaohe.knowledge.build import publish_index

    manifest, _ = inputs(tmp_path)
    root = tmp_path / "indexes"
    target = root / manifest.version
    target.mkdir(parents=True)
    (target / "keep").write_text("online", "utf-8")
    stage = root / ".stage"
    stage.mkdir()
    (stage / "manifest.json").write_text(manifest.model_dump_json(), "utf-8")
    (stage / "complete.json").write_text(
        json.dumps({"version": manifest.version, "count": len(manifest.entries)}), "utf-8"
    )
    with pytest.raises(ValueError):
        publish_index(stage, root, manifest)
    assert (target / "keep").read_text("utf-8") == "online"
    assert not (target / "manifest.json").exists()


def test_empty_release_removes_knowledge_without_embedding(tmp_path):
    pytest.importorskip("chromadb")
    from xiaohe.knowledge.build import publish_index

    manifest, chunks = prepare_manifest(
        [], embedding_model="fixture", embedding_identity="a" * 64, dimensions=3
    )
    root = tmp_path / "indexes"
    root.mkdir()
    stage = root / ".stage"
    result = build_child(stage, manifest, chunks)
    assert result.returncode == 0, result.stderr
    target = publish_index(stage, root, manifest)
    assert json.loads((target / "complete.json").read_text("utf-8"))["count"] == 0


def test_cli_errors_do_not_echo_private_settings(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    env["XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY"] = "private-fixture-secret"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xiaohe.knowledge",
            "build",
            "--resources",
            str(tmp_path),
            "--index-root",
            str(tmp_path / "index"),
            "--manifest-out",
            str(tmp_path / "candidate.json"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode != 0
    assert "private-fixture-secret" not in result.stderr + result.stdout
    assert "knowledge build failed" in result.stderr
    assert not (tmp_path / "candidate.json").exists()


def test_failed_new_version_does_not_change_live_index(tmp_path):
    pytest.importorskip("chromadb")
    from xiaohe.knowledge.build import publish_index

    manifest, chunks = inputs(tmp_path)
    root = tmp_path / "indexes"
    root.mkdir()
    stage = root / ".old-stage"
    assert build_child(stage, manifest, chunks).returncode == 0
    old = publish_index(stage, root, manifest)
    before = (old / "chroma.sqlite3").read_bytes()
    newer, changed = inputs(tmp_path, text="修改后的测试资料")
    stage = root / ".new-stage"
    assert build_child(stage, newer, changed, [1.0]).returncode != 0
    with pytest.raises(ValueError):
        publish_index(stage, root, newer)
    assert (old / "chroma.sqlite3").read_bytes() == before
    assert not (root / newer.version).exists()


def test_cli_builds_candidate_but_does_not_publish_bundle(tmp_path):
    pytest.importorskip("chromadb")
    root = write_test_bundle(tmp_path / "resources")
    write_knowledge_source(root / "knowledge", topics=("general",))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    with embedding_server() as (endpoint, requests):
        env.update(
            {
                "XIAOHE_KNOWLEDGE_EMBEDDING_BASE_URL": endpoint,
                "XIAOHE_KNOWLEDGE_EMBEDDING_MODEL": "fixture",
                "XIAOHE_KNOWLEDGE_EMBEDDING_DIMENSIONS": "3",
                "XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY": "fixture-private",
            }
        )
        candidate = tmp_path / "candidate.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "xiaohe.knowledge",
                "build",
                "--resources",
                str(root),
                "--index-root",
                str(tmp_path / "indexes"),
                "--manifest-out",
                str(candidate),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stderr
        assert requests[0]["input"] == ["仅用于测试的虚构政策。"]
        assert json.loads(result.stdout)["published"] is False
        assert candidate.exists()
        assert not (root / "knowledge/manifest.json").exists()
        assert "fixture-private" not in result.stdout + result.stderr
