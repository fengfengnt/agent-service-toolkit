import json
from pathlib import Path
from typing import get_args
from uuid import UUID

import pytest
import yaml
from test_admission import visitor

from xiaohe.workflows.compiler import compile_workflow
from xiaohe.workflows.loader import load_bundle, workflow_for
from xiaohe.workflows.models import DecideNode, RouteEvent

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_formal_payload_and_draft_api_use_same_bound_definition(pg_factory, tmp_path):
    from shutil import copytree

    from pydantic import ValidationError

    from xiaohe.reception.contracts import CreateDraft, Submission
    from xiaohe.reception.models import ReceptionDraft
    from xiaohe.reception.store import create_draft, draft_view
    from xiaohe.workflows.store import persist_bundle

    root = tmp_path / "resources"
    copytree(RESOURCE_ROOT, root)
    first = load_bundle(root)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await persist_bundle(db, first)
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="bound", direction="buyer", kind="service"),
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        original = row.definition_bundle_id
    data = yaml.safe_load((root / "workflows/buyer.yaml").read_text("utf-8"))
    data["required_fields"] = ["companyName", "city"]
    data["rounds"][0], data["rounds"][1] = data["rounds"][1], data["rounds"][0]
    (root / "workflows/buyer.yaml").write_text(yaml.safe_dump(data), "utf-8")
    latest = load_bundle(root)
    async with pg_factory.begin() as db:
        await persist_bundle(db, latest)
        row = await db.get(ReceptionDraft, UUID(view.id))
        assert row.definition_bundle_id == original != latest.bundle_id
        assert (await draft_view(db, row)).missing_required == ["companyName"]
        layout = (await draft_view(db, row)).field_layout
        assert layout.required_fields == ["companyName"]
        assert layout.rounds[0].fields == ["companyName", "industry", "companyScale", "city"]
        db.info["workflow_bundle_id"] = latest.bundle_id
        new = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="latest", direction="buyer", kind="service"),
        )
        assert new.field_layout.required_fields == ["companyName", "city"]
        assert new.step == "round2"
    with pytest.raises(ValidationError):
        Submission(
            submission_key="key",
            draft_id=view.id,
            draft_revision=1,
            operation="create",
            subject_kind="company",
            direction="buyer",
            public_fields={"city": "苏州"},
            request_fields={},
            files=[],
        )


def test_four_yaml_are_independent_executable_graphs():
    bundle = load_bundle(RESOURCE_ROOT)

    async def noop(node, state, config):
        return {}

    executors = dict.fromkeys(
        ("understand", "decide", "operation", "answer", "handoff", "result"), noop
    )
    graphs = {key: compile_workflow(value, executors) for key, value in bundle.definitions.items()}
    assert len(graphs) == 4 and len({id(value) for value in graphs.values()}) == 4
    assert len({value.prompts["intent"] for value in bundle.definitions.values()}) == 4
    assert len({value.prompts["answer"] for value in bundle.definitions.values()}) == 4
    for definition in bundle.definitions.values():
        for node in definition.nodes:
            if isinstance(node, DecideNode):
                assert set(node.routes) == set(get_args(RouteEvent))


def test_yaml_preserves_existing_rounds_and_minimums():
    bundle = load_bundle(RESOURCE_ROOT)
    expected = {
        "buyer": ["companyName"],
        "supplier": ["companyName"],
        "organization": [
            "organizationName",
            "memberSize",
            "coverage",
            "onlineChannels",
            "contactName",
        ],
    }
    for direction, minimum in expected.items():
        definition = workflow_for(bundle, "xiaohe-" + direction)
        assert definition.required_fields == minimum
        assert len(definition.skills) == (4 if direction == "supplier" else 7)
        assert [item.id for item in definition.rounds] == ["round1", "round2", "round3"] + (
            ["files"] if direction == "supplier" else []
        ) + ["summary"]
    assert workflow_for(bundle, "xiaohe-supplier").rounds[0].fields == [
        "companyName",
        "companyType",
        "category",
        "city",
    ]
    original = json.loads(
        (Path(__file__).parent / "fixtures/initial_rounds.json").read_text("utf-8")
    )
    for direction, rounds in original.items():
        assert [
            group.fields
            for group in workflow_for(bundle, "xiaohe-" + direction).rounds
            if group.id.startswith("round")
        ] == rounds
