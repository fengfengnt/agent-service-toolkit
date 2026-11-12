import importlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from xiaohe.contracts import Actor
from xiaohe.skill_schema import SkillValidationError

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"


def loader():
    assert importlib.util.find_spec("xiaohe.deep_runtime") is not None, (
        "standard skill runtime is missing"
    )
    return importlib.import_module("xiaohe.deep_runtime.bundles")


def actor(authenticated=True):
    return Actor(
        system=dict(
            system_id="portal",
            tenant_id=1,
            portal_client_id="app_localportal",
            allowed_origin="https://portal.lvh.me",
        ),
        session_id=uuid4(),
        user_id="skill-test-user" if authenticated else None,
        guest_id=None if authenticated else uuid4(),
    )


@pytest.fixture
def runtime_root(tmp_path):
    loader()
    return Path(shutil.copytree(ROOT, tmp_path / "resources"))


def edit_manifest(root, change):
    path = root / "deep-runtime.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")


def test_twenty_one_standard_packages():
    bundle = loader().load_runtime_bundle(ROOT)
    assert len(bundle.skills) == 21
    assert set(bundle.bindings) == {
        "xiaohe-reception",
        "xiaohe-buyer",
        "xiaohe-organization",
        "xiaohe-supplier",
    }
    assert sum("references/answer.md" in key for key in bundle.resources) == 18
    for name, spec in bundle.skills.items():
        assert spec.description and f"skills/{name}/SKILL.md" in bundle.resources
    assert not (ROOT / "skills/catalog.json").exists()
    assert bundle.bindings["xiaohe-reception"].tools == ["request_handoff"]
    assert bundle == type(bundle).model_validate_json(bundle.model_dump_json())


def test_denied_skill_absent_from_listing_and_read(runtime_root):
    module = loader()
    edit_manifest(
        runtime_root, lambda data: data["skills"]["buyer-security"].update(authenticated=True)
    )
    bundle = module.load_runtime_bundle(runtime_root)
    binding = module.select_binding(bundle, actor(False), "xiaohe-buyer")
    assert "buyer-security" not in binding.skills
    files = module.skill_files(bundle, binding)
    assert not any("buyer-security/" in path or "supplier-" in path for path in files)
    allowed = module.select_binding(bundle, actor(), "xiaohe-buyer")
    assert "buyer-security" in allowed.skills


def test_reference_bytes_change_hash(runtime_root):
    before = loader().load_runtime_bundle(runtime_root)
    path = runtime_root / "skills/buyer-security/references/answer.md"
    path.write_bytes(path.read_bytes() + b"\n")
    changed = loader().load_runtime_bundle(runtime_root)
    assert changed.bundle_id != before.bundle_id


@pytest.mark.parametrize(
    "change",
    [
        "duplicate-name",
        "escape",
        "missing-reference",
        "missing-tool",
        "flow-conflict",
        "invalid-yaml",
        "invalid-version",
    ],
)
def test_invalid_runtime_packages_are_rejected(runtime_root, change):
    if change == "duplicate-name":
        path = runtime_root / "skills/buyer-security/SKILL.md"
        path.write_text(
            path.read_text("utf-8").replace("name: buyer-security", "name: supplier-security"),
            "utf-8",
        )
    elif change == "escape":
        edit_manifest(
            runtime_root, lambda data: data["skills"]["buyer-security"].update(path="../outside")
        )
    elif change == "missing-reference":
        (runtime_root / "skills/buyer-security/references/answer.md").unlink()
    elif change == "missing-tool":
        edit_manifest(
            runtime_root,
            lambda data: data["skills"]["buyer-security"].update(requires_tools=["execute"]),
        )
    elif change == "flow-conflict":
        edit_manifest(
            runtime_root,
            lambda data: data["agents"]["xiaohe-buyer"]["skills"].append("supplier-lead-capture"),
        )
    elif change == "invalid-version":
        edit_manifest(runtime_root, lambda data: data.update(version=True))
    else:
        path = runtime_root / "deep-runtime.yaml"
        path.write_text("version: 1\nversion: 2\n", "utf-8")
    with pytest.raises(SkillValidationError):
        loader().load_runtime_bundle(runtime_root)


def test_symlinked_reference_cannot_escape_package(runtime_root, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("private-sentinel", "utf-8")
    path = runtime_root / "skills/buyer-security/references/answer.md"
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        if os.name != "nt":
            raise
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(SkillValidationError):
        loader().load_runtime_bundle(runtime_root)
