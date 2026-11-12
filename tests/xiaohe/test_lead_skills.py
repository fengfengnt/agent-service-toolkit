import json
from pathlib import Path

import pytest

from xiaohe.contracts import ServiceError
from xiaohe.reception.fields import validate_fields

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"
BASELINE = json.loads(
    (Path(__file__).parent / "contracts/lead-fields-baseline.json").read_text("utf-8")
)


def skills(root=ROOT):
    from xiaohe.deep_runtime.bundles import load_runtime_bundle

    return load_runtime_bundle(root)


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
