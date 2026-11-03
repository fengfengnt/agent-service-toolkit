import json
from pathlib import Path

import pytest


@pytest.fixture
def contract_fixture():
    def load(name):
        return json.loads((Path(__file__).parent / "contracts" / f"{name}.json").read_text("utf-8"))

    return load
