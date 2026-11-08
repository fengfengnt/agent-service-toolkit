import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from xiaohe.contracts import ServiceError
from xiaohe.reception import contracts

ROOT = Path(__file__).parent / "contracts"
FIXTURES = json.loads((ROOT / "reception-fixtures.json").read_text("utf-8"))
CASES = FIXTURES["schemaCases"]


@pytest.mark.parametrize("direction", ["organization", "supplier"])
def test_partner_switch_control_requires_its_direction(direction):
    control = contracts.ReceptionControl.model_validate(
        {"clientActionId": "partner", "action": "switch_direction", "direction": direction}
    )
    assert control.direction == direction


@pytest.mark.parametrize(
    "body",
    [
        {"action": "switch_direction"},
        {"action": "switch_direction", "direction": "buyer"},
        {"action": "switch_direction", "direction": "unknown"},
        {"action": "switch_direction", "direction": "supplier", "text": "确认提交"},
        {"action": "accept_invitation", "direction": "supplier"},
        {"action": "start_collection", "direction": None},
    ],
)
def test_partner_switch_control_rejects_invalid_payload(body):
    with pytest.raises(ValidationError):
        contracts.ReceptionControl.model_validate({"clientActionId": "partner"} | body)


def test_reception_schema_snapshot_matches_master_digest():
    assert (
        hashlib.sha256((ROOT / "reception-v1.schema.json").read_bytes()).hexdigest()
        == FIXTURES["schemaSha256"]
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_implemented_models_follow_shared_examples(case):
    model = getattr(contracts, case["type"])
    if case["valid"]:
        model.model_validate(case["value"])
    else:
        with pytest.raises((ValidationError, ServiceError)):
            model.model_validate(case["value"])


def test_reception_ids_are_bounded_and_filename_keeps_its_own_limit():
    with pytest.raises(ValidationError):
        contracts.CreateDraft(client_draft_id="x" * 201, direction="buyer", kind="service")
    filename = "文" * 251 + ".pdf"
    assert (
        contracts.FileRef(
            id="file",
            category="case",
            filename=filename,
            media_type="application/pdf",
            bytes=1,
            sha256=None,
            status="pending",
        ).filename
        == filename
    )


@pytest.mark.parametrize("key", ["clientActionId", "requestId"])
def test_reception_actions_keep_original_admission_identifier_limit(key):
    action = {
        "clientActionId": "a",
        "requestId": "q",
        "expectedRevision": 1,
        "action": "save_round",
    }
    contracts.ReceptionAction.model_validate(action | {key: "x" * 128})
    with pytest.raises(ValidationError):
        contracts.ReceptionAction.model_validate(action | {key: "x" * 129})


@pytest.mark.parametrize(
    "name,key",
    [
        ("buyer-name-only", "painPoints"),
        ("supplier-name-only", "capabilityTags"),
        ("organization-minimum", "aiNeeds"),
    ],
)
def test_formal_array_values_follow_shared_schema_uniqueness(name, key):
    value = deepcopy(next(case["value"] for case in CASES if case["name"] == name))
    value["requestFields"][key] = ["重复", "重复"]
    with pytest.raises(ValidationError):
        contracts.Submission.model_validate(value)
