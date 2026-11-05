import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xiaohe.contracts import ServiceError
from xiaohe.reception import contracts

ROOT = Path(__file__).parent / "contracts"
FIXTURES = json.loads((ROOT / "reception-fixtures.json").read_text("utf-8"))
CASES = FIXTURES["schemaCases"]


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
