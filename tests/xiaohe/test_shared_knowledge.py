import pytest
import yaml
from knowledge_support import write_knowledge_source, write_test_bundle

from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.skill_schema import AGENT_IDS, SkillValidationError

SHARED = "knowledge/platform-common.md"


def write_shared(root, text="共享测试事实", **changes):
    return write_knowledge_source(
        root / "knowledge",
        id="platform-common",
        agents=AGENT_IDS,
        topics=("general",),
        text=text,
        **changes,
    )


def test_shared_resource_is_pinned_even_without_index(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    first = load_runtime_bundle(root)
    assert SHARED in first.resources
    write_shared(root, text="新版本事实")
    second = load_runtime_bundle(root)
    assert first.bundle_id != second.bundle_id
    assert "新版本事实" in second.resources[SHARED]


@pytest.mark.parametrize(
    "change", ["missing", "blank", "id", "agents", "topic", "alias", "duplicate", "oversized"]
)
def test_new_bundle_requires_valid_shared_source(tmp_path, change):
    root = write_test_bundle(tmp_path / "resources")
    path = root / SHARED
    if change == "missing":
        path.unlink()
    elif change == "blank":
        write_shared(root, text=" ")
    elif change == "oversized":
        write_shared(root, text="中" * 2731)
    else:
        header, body = path.read_text("utf-8")[4:].split("\n---\n", 1)
        metadata = yaml.safe_load(header)
        if change == "id":
            metadata["id"] = "another"
        if change == "agents":
            metadata["agents"] = ["xiaohe-buyer"]
        if change == "topic":
            metadata["topics"] = ["buyer-rfx"]
        header = yaml.safe_dump(metadata)
        if change == "alias":
            header += "extra: &bad [*bad]\n"
        if change == "duplicate":
            header += "id: platform-common\n"
        path.write_text("---\n" + header + "---\n" + body, "utf-8")
    with pytest.raises(SkillValidationError):
        load_runtime_bundle(root)


def test_shared_body_exact_limit_and_historical_bundle(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    write_shared(root, text="中" * 2730 + "aa")
    bundle = load_runtime_bundle(root)
    assert SHARED in bundle.resources
    from xiaohe.knowledge.shared import shared_source

    assert len(shared_source(bundle).text.encode("utf-8")) == 8192
    old = bundle.model_copy(
        update={"resources": {k: v for k, v in bundle.resources.items() if k != SHARED}}
    )
    assert shared_source(old) is None


def test_shared_symlink_cannot_escape_resources(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    path = root / SHARED
    outside = tmp_path / "outside.md"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic link permission unavailable")
    with pytest.raises(SkillValidationError):
        load_runtime_bundle(root)


@pytest.mark.parametrize(
    "change", ["same", "text", "more", "less", "source", "conditions", "agents"]
)
def test_shared_manifest_change_excludes_all_old_chunks(tmp_path, change):
    from xiaohe.knowledge import shared
    from xiaohe.knowledge.sources import prepare_manifest

    root = write_test_bundle(tmp_path / "resources")
    write_shared(root, text="共享正文。" * 400)
    bundle = load_runtime_bundle(root)
    source = shared.shared_source(bundle)
    changes = {
        "text": {"text": "旧正文。" * 600},
        "more": {"text": source.text + "额外旧块。" * 600},
        "less": {"text": "旧简版"},
        "source": {"source": "旧来源"},
        "conditions": {"conditions": "旧条件"},
        "agents": {"agents": ["xiaohe-buyer"]},
    }
    old = source.model_copy(update=changes.get(change, {}))
    other = source.model_copy(update={"id": "other", "text": "其他知识"})
    manifest, _ = prepare_manifest(
        [old, other], embedding_model="fixture", embedding_identity="a" * 64, dimensions=3
    )
    stale = getattr(shared, "stale_shared_entry_ids", lambda *a: frozenset())(bundle, manifest)
    expected = (
        {k for k, v in manifest.entries.items() if v.id == "platform-common"}
        if change != "same"
        else set()
    )
    assert stale == expected
    historical = bundle.model_copy(
        update={"resources": {k: v for k, v in bundle.resources.items() if k != SHARED}}
    )
    assert (
        getattr(shared, "stale_shared_entry_ids", lambda *a: frozenset())(historical, manifest)
        == frozenset()
    )


def test_shared_materials_are_removed_by_exact_manifest_identity(tmp_path):
    from xiaohe.knowledge import shared
    from xiaohe.knowledge.sources import KnowledgeMaterial, prepare_manifest

    root = write_test_bundle(tmp_path / "resources")
    bundle = load_runtime_bundle(root)
    source = shared.shared_source(bundle)
    manifest, _ = prepare_manifest(
        [source], embedding_model="fixture", embedding_identity="a" * 64, dimensions=3
    )
    bundle.resources["knowledge/manifest.json"] = manifest.model_dump_json()
    material = KnowledgeMaterial(
        text=source.text,
        source=f"kb:{manifest.version}:platform-common:0",
        version=manifest.version,
        display_source=source.source,
        conditions=source.conditions,
    )
    other = material.model_copy(update={"source": f"kb:{manifest.version}:other:0"})
    result = getattr(shared, "filter_shared_materials", lambda b, m: list(m))(
        bundle, [material, other]
    )
    assert result == [other]
