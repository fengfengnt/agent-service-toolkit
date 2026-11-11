from uuid import UUID

import pytest
from test_agent_operations import prepare
from test_lead_skills import skills

from xiaohe.models import Command, Run
from xiaohe.reception.models import ReceptionDraft
from xiaohe.workflows.definitions import draft_definition

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_old_draft_keeps_effective_fields_new_draft_uses_latest(
    pg_factory, job_app, tmp_path
):
    from xiaohe.lead_skills import store

    assert hasattr(store, "resolve_draft_policy"), "draft field binding missing"
    _, frame = await prepare(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        original = await store.resolve_draft_policy(db, row)
        assert original.required_fields == ["companyName", "contactName", "contactPhone"]
        assert row.field_policy_id == original.policy_id
        before = dict(row.fields)
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        again = await store.resolve_draft_policy(db, row)
        assert again == original and row.fields == before
        assert (await draft_definition(db, row)).required_fields == original.required_fields
    import yaml
    from workflow_support import write_test_bundle

    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft
    from xiaohe.workflows.loader import load_bundle
    from xiaohe.workflows.store import persist_bundle

    root = write_test_bundle(tmp_path / "new-rules")
    path = root / "workflows/buyer.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    data["required_fields"] = ["companyName", "city"]
    path.write_text(yaml.safe_dump(data), "utf-8")
    latest = load_bundle(root)
    async with pg_factory.begin() as db:
        await persist_bundle(db, latest)
        view = await create_draft(
            db,
            frame.actor,
            frame.conversation_id,
            CreateDraft(client_draft_id="new-policy", kind="service", direction="buyer"),
            definition_bundle_id=latest.bundle_id,
        )
        new = await db.get(ReceptionDraft, UUID(view.id))
        assert "city" in (await store.resolve_draft_policy(db, new)).required_fields
        old = await db.get(ReceptionDraft, frame.draft_id)
        assert "city" not in (await store.resolve_draft_policy(db, old)).required_fields


async def test_next_command_in_same_waiting_run_uses_new_policy(pg_factory, job_app, tmp_path):
    from xiaohe.lead_skills import store

    assert hasattr(store, "bind_command_policy"), "command policy binding missing"
    bundle, frame = await prepare(pg_factory, tmp_path)
    first = skills().interaction_policy
    latest = first.model_copy(update={"version": 2})
    async with pg_factory.begin() as db:
        cmd = await db.get(Command, frame.command_id)
        run = await db.get(Run, frame.fence.run_id)
        shell = run.workflow_bundle_id
        bound = await store.bind_command_policy(db, cmd, first, workflow_bundle_id=bundle.bundle_id)
        assert bound == first
        assert (
            await store.bind_command_policy(db, cmd, latest, workflow_bundle_id=bundle.bundle_id)
            == first
        )
        assert run.workflow_bundle_id == shell


async def test_resume_keeps_command_policy_and_cached_stages(pg_factory, job_app, tmp_path):
    from xiaohe.lead_skills import store

    assert hasattr(store, "bind_command_policy"), "command policy binding missing"
    bundle, frame = await prepare(pg_factory, tmp_path)
    policy = skills().interaction_policy
    async with pg_factory.begin() as db:
        cmd = await db.get(Command, frame.command_id)
        cmd.payload = cmd.payload | {
            "agentStages": {"xiaohe-buyer": {"answer": {"text": "已缓存"}}}
        }
        await store.bind_command_policy(db, cmd, policy, workflow_bundle_id=bundle.bundle_id)
    async with pg_factory.begin() as db:
        cmd = await db.get(Command, frame.command_id)
        await store.bind_command_policy(
            db, cmd, policy.model_copy(update={"version": 2}), workflow_bundle_id="0" * 64
        )
        assert cmd.payload["agentStages"]["xiaohe-buyer"]["answer"]["text"] == "已缓存"
        assert cmd.payload["leadWorkflowBundleId"] == bundle.bundle_id


async def test_started_legacy_command_is_not_reclassified(pg_factory, job_app, tmp_path):
    from test_agent_semantics import ScriptedModel

    from xiaohe.agents.runtime import run_agent_turn

    bundle, frame = await prepare(pg_factory, tmp_path, text="继续咨询")
    model = ScriptedModel(
        result={},
        responses={
            "SemanticDecision": {"action": "consultation"},
            "AnswerDraft": {
                "text": "请说明采购问题。",
                "basis": "general_advice",
                "source_ids": [],
            },
        },
    )
    await run_agent_turn(pg_factory, frame.fence, bundle=bundle, model=model)
    async with pg_factory() as db:
        cmd = await db.get(Command, frame.command_id)
        assert cmd.interaction_policy_id is None
        assert (await db.get(Run, frame.fence.run_id)).workflow_bundle_id == bundle.bundle_id


async def test_form_projection_matches_form_validation(pg_factory):
    from xiaohe.reception.forms import form_definition

    for role in ("buyer", "organization", "supplier"):
        _, definition = form_definition(role)
        assert definition.required_fields == skills().field_policies[role].required_fields
