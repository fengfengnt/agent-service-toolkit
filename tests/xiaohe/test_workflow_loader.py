import json
import os
import subprocess

import pytest
import yaml
from workflow_support import workflow_data, write_test_bundle

from xiaohe.workflows.loader import load_bundle, parse_workflow, workflow_for
from xiaohe.workflows.models import WorkflowDefinition, WorkflowValidationError


def test_yaml_roundtrip_and_closed_types():
    parsed = parse_workflow(yaml.safe_dump(workflow_data(), allow_unicode=True))
    assert WorkflowDefinition.model_validate(parsed.model_dump()) == parsed
    assert parsed.agent_id == "xiaohe-buyer"


@pytest.mark.parametrize(
    "change", ["unknown_node", "unknown_field", "unknown_agent", "bool_schema"]
)
def test_invalid_workflow_types_are_rejected(change):
    data = workflow_data()
    if change == "unknown_node":
        data["nodes"][0]["type"] = "python"
    elif change == "unknown_field":
        data["exec"] = "private-configuration-sentinel"
    elif change == "unknown_agent":
        data["agent_id"] = "arbitrary-agent"
    else:
        data["schema_version"] = True
    with pytest.raises(WorkflowValidationError) as error:
        parse_workflow(yaml.safe_dump(data))
    assert "private-configuration-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "schema_version: 1\nschema_version: 2\n",
        "value: &one [*one]\n",
        "first: &one value\nsecond: *one\n",
        "value: !!python/object/apply:os.system [echo unsafe]\n",
        "value: " + "a" * (256 * 1024),
    ],
    ids=["duplicate-key", "recursive-alias", "alias", "python-tag", "oversized"],
)
def test_unsafe_yaml_is_rejected(text):
    with pytest.raises(WorkflowValidationError):
        parse_workflow(text)


def test_bundle_digest_includes_prompt_content(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    before = load_bundle(root)
    assert len(before.definitions) == 4
    assert workflow_for(before, "xiaohe-buyer").required_fields == ["companyName"]
    (root / "prompts/answer.md").write_text("变更后的已确认资料。", "utf-8")
    after = load_bundle(root)
    assert before.bundle_id != after.bundle_id
    assert before.resources["prompts/answer.md"] == "依据已确认资料回答。"
    assert before == type(before).model_validate(before.model_dump())


@pytest.mark.parametrize("reference", ["../outside.md", "/outside.md", "C:/outside.md", "a\\b.md"])
def test_resource_reference_cannot_escape_allowed_directory(tmp_path, reference):
    root = write_test_bundle(tmp_path / "resources")
    data = workflow_data()
    data["prompts"]["answer"] = reference
    (root / "workflows/buyer.yaml").write_text(yaml.safe_dump(data), "utf-8")
    with pytest.raises(WorkflowValidationError):
        load_bundle(root)


def test_symlinked_resource_cannot_escape_allowed_directory(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    outside = tmp_path / "private.md"
    outside.write_text("private-configuration-sentinel", "utf-8")
    linked = root / "prompts/answer.md"
    linked.unlink()
    try:
        linked.symlink_to(outside)
    except OSError:
        if os.name != "nt":
            raise
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside.rename(outside_dir / "private.md")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root / "prompts/external"), str(outside_dir)],
            check=True,
            capture_output=True,
        )
        data = workflow_data()
        data["prompts"]["answer"] = "external/private.md"
        (root / "workflows/buyer.yaml").write_text(yaml.safe_dump(data), "utf-8")
    with pytest.raises(WorkflowValidationError) as error:
        load_bundle(root)
    assert "private-configuration-sentinel" not in str(error.value)


def test_skill_materials_are_frozen_and_role_limited(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    skills = root / "skills"
    (skills / "buyer/rfx").mkdir(parents=True)
    (skills / "buyer/rfx/SKILL.md").write_text("# 费用与比价\n费用待正式确认。", "utf-8")
    (skills / "catalog.json").write_text(
        json.dumps(
            [
                {
                    "id": "buyer.rfx",
                    "direction": "buyer",
                    "title": "费用",
                    "path": "buyer/rfx/SKILL.md",
                }
            ]
        ),
        "utf-8",
    )
    buyer = workflow_data()
    buyer["skills"] = ["buyer.rfx"]
    (root / "workflows/buyer.yaml").write_text(yaml.safe_dump(buyer), "utf-8")
    bundle = load_bundle(root)
    assert "费用待正式确认" in bundle.resources["skills/buyer/rfx/SKILL.md"]
    supplier = workflow_data("xiaohe-supplier")
    supplier["skills"] = ["buyer.rfx"]
    (root / "workflows/supplier.yaml").write_text(yaml.safe_dump(supplier), "utf-8")
    with pytest.raises(WorkflowValidationError):
        load_bundle(root)


def test_file_name_and_agent_identity_must_agree(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    (root / "workflows/buyer.yaml").write_text(
        yaml.safe_dump(workflow_data("xiaohe-supplier")), "utf-8"
    )
    with pytest.raises(WorkflowValidationError):
        load_bundle(root)


def test_resource_directory_link_cannot_replace_allowed_root(tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    outside = tmp_path / "private-prompts"
    outside.mkdir()
    for name in ("intent.md", "answer.md"):
        (outside / name).write_text("private-configuration-sentinel", "utf-8")
    (root / "prompts").rename(root / "original-prompts")
    try:
        (root / "prompts").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root / "prompts"), str(outside)],
            check=True,
            capture_output=True,
        )
    with pytest.raises(WorkflowValidationError):
        load_bundle(root)
