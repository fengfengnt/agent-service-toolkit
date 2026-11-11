import importlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from xiaohe.contracts import ServiceError
from xiaohe.reception.fields import validate_fields
from xiaohe.workflows.loader import load_bundle
from xiaohe.workflows.models import WorkflowBundle, WorkflowValidationError

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"
BASELINE = json.loads(
    (Path(__file__).parent / "contracts/lead-fields-baseline.json").read_text("utf-8")
)


def skills(root=ROOT):
    # Missing capability is an assertion failure, rather than a collection error.
    assert importlib.util.find_spec("xiaohe.lead_skills") is not None, (
        "lead skill loader is missing"
    )
    return importlib.import_module("xiaohe.lead_skills.loader").load_lead_skills(root)


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
def test_role_profiles_preserve_current_contract(role):
    policy = skills().field_policies[role]
    old = BASELINE[role]
    assert [r.model_dump() for r in policy.rounds] == old["rounds"]
    assert policy.required_fields == old["required_fields"]
    assert {f.id: f.label for f in policy.fields} == old["labels"]
    assert {f.id for f in policy.fields if f.scope == "public"} == set(old["public_fields"])
    for case in old["cases"]:
        if case["error"]:
            with pytest.raises(ServiceError):
                validate_fields(role, {case["field"]: case["value"]}, policy=policy)
        else:
            assert (
                validate_fields(role, {case["field"]: case["value"]}, policy=policy)
                == case["result"]
            )
    with pytest.raises(ServiceError):
        validate_fields(role, {"unknownField": "x"}, policy=policy)


def test_skill_binding_is_separate_from_qa_topics(tmp_path):
    bundle = skills()
    shutil.copytree(ROOT, tmp_path / "resources")
    for role in bundle.field_policies:
        path = tmp_path / f"resources/workflows/{role}.yaml"
        data = yaml.safe_load(path.read_text("utf-8"))
        data.pop("rounds", None)
        data.pop("required_fields", None)
        data["lead_skill_id"] = role + ".lead_capture"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    workflow = load_bundle(tmp_path / "resources")
    for role, policy in bundle.field_policies.items():
        definition = workflow.definitions["xiaohe-" + role]
        assert definition.required_fields == policy.required_fields
        assert definition.lead_skill_id not in definition.skills
        assert bundle.interaction_policy.guides[role]


@pytest.mark.parametrize(
    "change", ["role", "field", "scope", "action", "template", "alias", "duplicate", "path"]
)
def test_invalid_skill_configuration_is_rejected(tmp_path, change):
    skills()
    shutil.copytree(ROOT / "lead-skills", tmp_path / "lead-skills")
    path = tmp_path / "lead-skills/buyer/policy.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    if change == "role":
        data["direction"] = "other"
    elif change == "field":
        data["fields"][0]["id"] = "unknown"
    elif change == "scope":
        data["fields"][0]["scope"] = "request"
    elif change == "action":
        data["execute"] = "python"
    elif change == "path":
        data["guide"] = "../../outside.md"
    elif change == "template":
        path = tmp_path / "lead-skills/common/policy.yaml"
        data = yaml.safe_load(path.read_text("utf-8"))
        data["notices"]["accepted"] = "已受理{secret}"
    if change == "alias":
        path.write_text("direction: &a buyer\nversion: *a\n", "utf-8")
    elif change == "duplicate":
        path.write_text("direction: buyer\ndirection: supplier\n", "utf-8")
    else:
        path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    with pytest.raises(WorkflowValidationError):
        skills(tmp_path)


def test_legacy_bundle_hash_is_unchanged():
    skills()
    raw = json.loads((ROOT / "legacy-bundle-v1.json").read_text("utf-8"))
    assert WorkflowBundle.model_validate(raw).bundle_id == raw["bundle_id"]
