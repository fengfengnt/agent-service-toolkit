from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from test_admission import send, visitor
from workflow_support import write_test_bundle

from xiaohe.models import Run, WorkflowDefinitionRecord
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import create_draft
from xiaohe.workflows.loader import load_bundle, workflow_for
from xiaohe.workflows.models import WorkflowValidationError
from xiaohe.workflows.store import (
    bind_run_bundle,
    bundle_for_run,
    load_persisted_bundle,
    persist_bundle,
)

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_bundle_is_immutable_and_deduplicated(pg_factory, tmp_path):
    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    async with pg_factory.begin() as db:
        assert await persist_bundle(db, bundle) == bundle.bundle_id
        assert await persist_bundle(db, bundle) == bundle.bundle_id
        assert await db.scalar(select(func.count()).select_from(WorkflowDefinitionRecord)) == 1
    forged = bundle.model_copy(update={"resources": {"prompts/intent.md": "篡改"}})
    with pytest.raises(WorkflowValidationError):
        async with pg_factory.begin() as db:
            await persist_bundle(db, forged)
    async with pg_factory() as db:
        assert await load_persisted_bundle(db, bundle.bundle_id) == bundle


async def test_restart_and_handoff_keep_run_bundle(pg_factory, job_app, tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    first = load_bundle(root)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "workflow-run")
    run_id = UUID(accepted.run.id)
    async with pg_factory.begin() as db:
        await persist_bundle(db, first)
        assert await bind_run_bundle(db, run_id, first.bundle_id) == first.bundle_id
    (root / "prompts/answer.md").write_text("下一版回答", "utf-8")
    latest = load_bundle(root)
    async with pg_factory.begin() as db:
        await persist_bundle(db, latest)
    async with pg_factory() as db:
        recovered = await bundle_for_run(db, run_id)
        assert recovered == first
        assert len(recovered.definitions) == 4
        assert recovered.resources["prompts/answer.md"] != "下一版回答"
        assert (await db.get(Run, run_id)).runtime_version == "dsl-v1"
    with pytest.raises(WorkflowValidationError):
        async with pg_factory.begin() as db:
            await bind_run_bundle(db, run_id, latest.bundle_id)


async def test_legacy_run_is_not_reclassified(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "legacy-run")
    async with pg_factory() as db:
        run_id = UUID(accepted.run.id)
        assert await bundle_for_run(db, run_id) is None
        assert (await db.get(Run, run_id)).runtime_version == "legacy"


async def test_started_legacy_run_cannot_be_rebound(pg_factory, job_app, tmp_path):
    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "started-legacy")
    run_id = UUID(accepted.run.id)
    async with pg_factory.begin() as db:
        await persist_bundle(db, bundle)
        run = await db.get(Run, run_id)
        run.status = "running"
        run.attempt = 1
    with pytest.raises(WorkflowValidationError):
        async with pg_factory.begin() as db:
            await bind_run_bundle(db, run_id, bundle.bundle_id)


async def test_old_draft_rules_are_not_replaced_by_new_defaults(pg_factory, tmp_path):
    root = write_test_bundle(tmp_path / "resources")
    first = load_bundle(root)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await persist_bundle(db, first)
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="bound", kind="service", direction="buyer"),
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        row.definition_bundle_id = first.bundle_id
    (root / "prompts/intent.md").write_text("变更后的流程资料", "utf-8")
    latest = load_bundle(root)
    async with pg_factory.begin() as db:
        await persist_bundle(db, latest)
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, UUID(view.id))
        bound = await load_persisted_bundle(db, row.definition_bundle_id)
        assert bound.bundle_id == first.bundle_id != latest.bundle_id
        assert workflow_for(bound, "xiaohe-buyer").required_fields == ["companyName"]


async def test_corrupt_or_missing_snapshot_is_not_replaced_with_latest(pg_factory, tmp_path):
    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    async with pg_factory.begin() as db:
        await persist_bundle(db, bundle)
        bad = bundle.model_dump(mode="json")
        bad["resources"]["prompts/answer.md"] = "内容已被非法改写"
        await db.execute(
            update(WorkflowDefinitionRecord)
            .where(WorkflowDefinitionRecord.bundle_id == bundle.bundle_id)
            .values(payload=bad)
        )
    async with pg_factory() as db:
        with pytest.raises(WorkflowValidationError):
            await load_persisted_bundle(db, bundle.bundle_id)
        with pytest.raises(WorkflowValidationError):
            await load_persisted_bundle(db, "0" * 64)


async def test_downgrade_refuses_to_discard_workflow_snapshots(pg_factory, pg_url, tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    async with pg_factory.begin() as db:
        await persist_bundle(db, bundle)
    engine = create_engine(pg_url, hide_parameters=True)
    try:
        with pytest.raises(RuntimeError):
            with engine.begin() as connection:
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                command.downgrade(config, "0008")
    finally:
        engine.dispose()
    async with pg_factory() as db:
        assert await load_persisted_bundle(db, bundle.bundle_id) == bundle
