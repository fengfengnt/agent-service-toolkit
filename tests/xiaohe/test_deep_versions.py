from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from native_support import prepare
from sqlalchemy import select
from test_identity_sessions import actor, bootstrap, login

from xiaohe.admission import accept_action, accept_message
from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime import bundles
from xiaohe.models import Command, Run
from xiaohe.reception.models import ReceptionDraft
from xiaohe.settings import Settings

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"
pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_native_new_run_is_bound_without_legacy_bundle(pg_factory, job_app):
    assert hasattr(bundles, "bind_deep_command"), "native command binding missing"
    bundle = bundles.load_runtime_bundle(ROOT)
    async with pg_factory.begin() as db:
        await bundles.persist_runtime_bundle(db, bundle)
    pg_factory.configure(info={"deep_runtime_bundle_id": bundle.bundle_id})
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        result = await accept_message(
            db, who, UUID(initial.conversation_id), "native-first", "采购咨询"
        )
        run = await db.get(Run, UUID(result.run.id))
        assert run.deep_runtime_bundle_id == bundle.bundle_id
        cmd = await db.scalar(select(Command).where(Command.message_id == UUID(result.message.id)))
        assert cmd.payload["deepExecution"]["bundle_id"] == bundle.bundle_id
        assert "leadWorkflowBundleId" not in cmd.payload


async def test_existing_run_new_command_pins_new_bundle(pg_factory, job_app, tmp_path):
    assert hasattr(bundles, "bind_deep_command"), "native command binding missing"
    legacy, frame = await prepare(pg_factory, tmp_path)
    first = bundles.load_runtime_bundle(ROOT)
    latest = first.model_copy(update={"resources": first.resources | {"release-note": "next"}})
    executions = []
    async with pg_factory.begin() as db:
        draft = await db.get(ReceptionDraft, frame.draft_id)
        original_policy_id = draft.field_policy_id
    previous_id = frame.command_id
    for bundle in (first, latest):
        request_id = str(uuid4())
        async with pg_factory.begin() as db:
            await bundles.persist_runtime_bundle(db, bundle)
            previous = await db.get(Command, previous_id)
            previous.completed_at = datetime.now(UTC)
            run = await db.get(Run, frame.fence.run_id)
            run.status = "waiting_input"
            run.pending_request = {
                "type": "input_request",
                "requestId": request_id,
                "prompt": "继续",
            }
        pg_factory.configure(info={"deep_runtime_bundle_id": bundle.bundle_id})
        async with pg_factory.begin() as db:
            result = await accept_action(
                db, frame.actor, frame.conversation_id, str(uuid4()), request_id, None, "继续"
            )
            cmd = await db.scalar(
                select(Command).where(Command.message_id == UUID(result.message.id))
            )
            previous_id = cmd.id
            executions.append((cmd.id, cmd.payload["deepExecution"]["bundle_id"]))
    assert executions[0][1] == first.bundle_id
    assert executions[1][1] == latest.bundle_id
    pg_factory.configure(info={})
    async with pg_factory.begin() as db:
        run = await db.get(Run, frame.fence.run_id)
        cmd = await db.get(Command, executions[0][0])
        restored = await bundles.bind_deep_command(db, run, cmd, latest)
        assert restored.bundle_id == first.bundle_id
        assert run.deep_runtime_bundle_id == legacy.bundle_id
        assert (await db.get(ReceptionDraft, frame.draft_id)).field_policy_id == original_policy_id


async def test_native_runtime_is_not_optional():
    assert "deepagents_enabled" not in Settings.model_fields


async def test_enabled_runtime_requires_published_bundle(pg_factory, job_app):
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who = await actor(pg_factory, token)
    pg_factory.configure(info={})
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as error:
            await accept_message(db, who, UUID(initial.conversation_id), "missing-native", "咨询")
        assert error.value.code == "UPSTREAM_UNAVAILABLE"
