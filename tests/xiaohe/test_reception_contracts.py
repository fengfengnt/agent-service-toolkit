import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xiaohe.contracts import ServiceError
from xiaohe.reception import contracts

ROOT = Path(__file__).parent / "contracts"
FIXTURES = json.loads((ROOT / "reception-fixtures.json").read_text("utf-8"))
CASES = [case for case in FIXTURES["schemaCases"] if hasattr(contracts, case["type"])]


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
