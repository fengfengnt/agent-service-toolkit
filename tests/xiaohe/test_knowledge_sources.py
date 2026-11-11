import json

import pytest
from knowledge_support import write_knowledge_source
from pydantic import ValidationError
from workflow_support import write_test_bundle

ALLOWED = {
    "xiaohe-reception": set(),
    "xiaohe-buyer": {"buyer.rfx"},
    "xiaohe-organization": {"organization.cooperation"},
    "xiaohe-supplier": {"supplier.security"},
}


def prepare(root, **changes):
    from xiaohe.knowledge.sources import load_sources, prepare_manifest

    return prepare_manifest(
        load_sources(root, ALLOWED),
        embedding_model="fixture",
        embedding_identity="a" * 64,
        dimensions=3,
        **changes,
    )


def test_source_body_and_acl_are_preserved(tmp_path):
    from xiaohe.knowledge.sources import load_sources

    write_knowledge_source(tmp_path)
    (source,) = load_sources(tmp_path, ALLOWED)
    assert source.text == "仅用于测试的虚构政策。"
    assert source.agents == ["xiaohe-buyer"]
    assert source.source == "测试审定资料"


@pytest.mark.parametrize(
    "changes",
    [
        {"agents": []},
        {"agents": ["xiaohe-admin"]},
        {"agents": ["xiaohe-reception"]},
        {"topics": []},
        {"topics": ["supplier.security"]},
        {"topics": ["missing"]},
        {"source": ""},
        {"conditions": ""},
        {"id": "../escape"},
        {"extra": "invalid"},
    ],
)
def test_invalid_source_is_rejected(tmp_path, changes):
    from xiaohe.knowledge.sources import load_sources

    write_knowledge_source(tmp_path, **(changes if "id" not in changes else {}))
    if "id" in changes:
        path = tmp_path / "policy.md"
        path.write_text(path.read_text("utf-8").replace("id: policy", "id: ../escape"), "utf-8")
    with pytest.raises(ValueError):
        load_sources(tmp_path, ALLOWED)


@pytest.mark.parametrize(
    "content",
    [
        "no front matter",
        "---\nid: a\nid: b\n---\ntext",
        "---\na: &a [*a]\n---\ntext",
        "---\n!!python/object:os.system {}\n---\ntext",
        "---\n- not-a-mapping\n---\ntext",
    ],
)
def test_unsafe_or_invalid_yaml_is_rejected(tmp_path, content):
    from xiaohe.knowledge.sources import load_sources

    (tmp_path / "bad.md").write_text(content, "utf-8")
    with pytest.raises(ValueError):
        load_sources(tmp_path, ALLOWED)


def test_duplicate_id_and_empty_body_are_rejected(tmp_path):
    from xiaohe.knowledge.sources import load_sources

    path = write_knowledge_source(tmp_path)
    (tmp_path / "duplicate.md").write_bytes(path.read_bytes())
    with pytest.raises(ValueError):
        load_sources(tmp_path, ALLOWED)
    (tmp_path / "duplicate.md").unlink()
    write_knowledge_source(tmp_path, text="  ")
    with pytest.raises(ValueError):
        load_sources(tmp_path, ALLOWED)


def test_symlink_cannot_import_external_content(tmp_path):
    from xiaohe.knowledge.sources import load_sources

    outside = tmp_path / "outside"
    path = write_knowledge_source(outside)
    root = tmp_path / "sources"
    root.mkdir()
    try:
        (root / "linked.md").symlink_to(path)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError):
        load_sources(root, ALLOWED)


def test_oversized_source_rejected_before_parsing(tmp_path):
    from xiaohe.knowledge.sources import load_sources

    (tmp_path / "large.md").write_text("x" * (1024 * 1024 + 1), "utf-8")
    with pytest.raises(ValueError):
        load_sources(tmp_path, ALLOWED)


def test_content_acl_source_and_embedding_identity_change_version(tmp_path):
    from xiaohe.knowledge.sources import load_sources, prepare_manifest

    write_knowledge_source(tmp_path)
    original, chunks = prepare(tmp_path)
    assert chunks[0].id == "policy:0"
    assert prepare(tmp_path)[0] == original
    versions = {original.version}
    for changes in [
        {"text": "不同的虚构内容"},
        {"source": "另一个审定来源"},
        {"agents": ("xiaohe-buyer", "xiaohe-supplier"), "topics": ("general",)},
    ]:
        write_knowledge_source(tmp_path, **changes)
        versions.add(prepare(tmp_path)[0].version)
    changed, _ = prepare_manifest(
        load_sources(tmp_path, ALLOWED),
        embedding_model="fixture",
        embedding_identity="b" * 64,
        dimensions=3,
    )
    versions.add(changed.version)
    assert len(versions) == 5
    assert "text" not in original.entries["policy:0"].model_dump()
    assert original.entries["policy:0"].source == "测试审定资料"


def test_manifest_integrity_and_size(tmp_path):
    from xiaohe.knowledge.sources import read_manifest

    write_knowledge_source(tmp_path)
    manifest, _ = prepare(tmp_path)
    assert read_manifest(manifest.model_dump_json()) == manifest
    raw = manifest.model_dump(mode="json")
    raw["entries"]["policy:0"]["agents"] = ["xiaohe-supplier"]
    with pytest.raises(ValueError):
        read_manifest(json.dumps(raw))
    with pytest.raises(ValueError):
        read_manifest(" " * (1024 * 1024 + 1))


def test_only_published_manifest_changes_bundle(tmp_path):
    from xiaohe.workflows.loader import load_bundle

    root = write_test_bundle(tmp_path / "resources")
    first = load_bundle(root)
    write_knowledge_source(root / "knowledge", topics=("general",))
    assert load_bundle(root) == first
    manifest, _ = prepare(root / "knowledge")
    (root / "knowledge/manifest.json").write_text(manifest.model_dump_json(), "utf-8")
    second = load_bundle(root)
    assert second.bundle_id != first.bundle_id
    assert "knowledge/manifest.json" in second.resources
    write_knowledge_source(root / "knowledge", text="未发布修改", topics=("general",))
    assert load_bundle(root) == second


def test_empty_version_is_valid_and_no_manifest_is_legacy(tmp_path):
    from xiaohe.knowledge.sources import manifest_from_bundle, read_manifest
    from xiaohe.workflows.loader import load_bundle

    manifest, chunks = prepare(tmp_path)
    assert manifest.entries == {}
    assert chunks == []
    assert read_manifest(manifest.model_dump_json()) == manifest
    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    assert manifest_from_bundle(bundle) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/v1",
        "https://host/v1?a=1",
        "file:///tmp",
        "https://host/v1#secret",
        "https://host:secret/v1",
    ],
)
def test_embedding_endpoint_credentials_are_rejected(url):
    from xiaohe.settings import Settings

    with pytest.raises(ValidationError) as failure:
        Settings(
            toolkit_base_url="http://localhost",
            auth_secret="fixture",
            knowledge_embedding_base_url=url,
        )
    assert "secret" not in str(failure.value)


def test_embedding_configuration_is_separate_from_chat():
    from xiaohe.settings import Settings

    settings = Settings(
        toolkit_base_url="http://localhost",
        auth_secret="fixture",
        knowledge_embedding_base_url="https://embeddings.example/v1",
        knowledge_embedding_api_key="private-test-only",
    )
    assert settings.knowledge_embedding_model is None
    assert "private-test-only" not in repr(settings)
