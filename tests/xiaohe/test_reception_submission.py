from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import install, send_in_db, talk

from xiaohe.contracts import ServiceError
from xiaohe.models import Run
from xiaohe.reception.contracts import CreateDraft, SubmissionResult, SubmitReceipt
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.reception.submission import confirm_draft, create_draft_from_business, submit_confirmed
from xiaohe.runs import acquire_run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def confirmed(factory):
    _, token = await login(factory)
    initial, _, _ = await bootstrap(factory, token)
    who, conversation = await actor(factory, token), UUID(initial.conversation_id)
    async with factory.begin() as db:
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction="buyer", kind="service"),
        )
        view = await patch_draft(
            db,
            who,
            view.id,
            1,
            {"companyName": "甲公司", "contactName": "测试联系人", "contactPhone": "总台分机"},
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
        accepted = await send_in_db(db, who, conversation, "确认提交当前汇总")
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with factory.begin() as db:
        submission = await confirm_draft(
            db, who, view.id, view.revision, view.revision, UUID(accepted.message.id)
        )
        again = await confirm_draft(
            db, who, view.id, view.revision, view.revision, UUID(accepted.message.id)
        )
        assert submission.submission_key == again.submission_key
    return fence, submission


@pytest.mark.parametrize("legacy_payload", [False, True])
async def test_worker_restart_reconciles_submission(pg_factory, job_app, legacy_payload):
    fence, submission = await confirmed(pg_factory)
    if legacy_payload:
        async with pg_factory.begin() as db:
            pending = await db.get(ReceptionSubmission, submission.id)
            pending.payload = pending.payload | {"requestFields": {}}
    backend = {"receipt": None, "creates": 0}

    class LostReplyClient:
        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted" if backend["receipt"] else "not_found", receipt=backend["receipt"]
            )

        async def submit(self, actor, payload):
            assert actor.ai_user_id != actor.sub and actor.source_tenant_id == "1"
            backend["creates"] += 1
            backend["receipt"] = SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="same-application",
                revision=1,
                status="accepted",
            )
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)

    client = LostReplyClient()
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit_confirmed(pg_factory, fence, submission.id, client)
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, fence.run_id, recover=True)
    client = LostReplyClient()
    recovered = await submit_confirmed(pg_factory, fence, submission.id, client)
    assert recovered.application_id == "same-application"
    assert (await submit_confirmed(pg_factory, fence, submission.id, client)) == recovered
    assert backend["creates"] == 1
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReceptionSubmission)) == 1
        assert (await db.get(ReceptionDraft, submission.draft_id)).state == "accepted"


async def test_wrong_receipt_does_not_accept_draft(pg_factory, job_app):
    fence, submission = await confirmed(pg_factory)

    class WrongReceiptClient:
        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted",
                receipt=SubmitReceipt(
                    submission_key="wrong-key",
                    kind="service",
                    application_id="foreign",
                    revision=1,
                    status="accepted",
                ),
            )

    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit_confirmed(pg_factory, fence, submission.id, WrongReceiptClient())
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, submission.draft_id)).state == "submitting"


async def test_edit_reads_current_formal_revision_and_unavailable_source_creates_nothing(
    pg_factory,
):
    from xiaohe.reception.contracts import ApplicationView

    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    request = CreateDraft(
        client_draft_id="edit-one",
        kind="service",
        direction="buyer",
        source_application_id="existing-application",
    )

    class CurrentSource:
        async def application(self, actor, application_id):
            return ApplicationView(
                id=application_id,
                subject_id="existing-company",
                direction="buyer",
                revision=8,
                public_fields={"companyName": "正式当前公司"},
                request_fields={"requirement": "后台最新需求"},
                files=[],
            )

    view = await create_draft_from_business(pg_factory, who, conversation, request, CurrentSource())
    assert view.fields == {"companyName": "正式当前公司", "requirement": "后台最新需求"}
    assert view.subject_id == "existing-company"
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, UUID(view.id))).source_revision == 8
    repeated = await create_draft_from_business(
        pg_factory, who, conversation, request, CurrentSource()
    )
    assert repeated.id == view.id

    class Unavailable:
        async def application(self, actor, application_id):
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)

    assert (
        await create_draft_from_business(pg_factory, who, conversation, request, Unavailable())
    ).id == view.id

    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await create_draft_from_business(
            pg_factory,
            who,
            conversation,
            request.model_copy(update={"client_draft_id": "edit-two"}),
            Unavailable(),
        )
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReceptionDraft)) == 1


async def test_late_receipt_cannot_write_after_fence_is_cancelled(pg_factory, job_app):
    from xiaohe.contracts import TrustedSystem
    from xiaohe.identity import _actor
    from xiaohe.models import Session, System
    from xiaohe.runs import cancel_run

    fence, submission = await confirmed(pg_factory)

    class LateReply:
        async def result(self, actor, key):
            async with pg_factory.begin() as db:
                run = await db.get(Run, fence.run_id)
                session = await db.get(Session, run.session_id)
                system = await db.get(System, run.system_id)
                who = _actor(
                    session,
                    TrustedSystem(
                        system_id=system.system_id,
                        tenant_id=system.tenant_id,
                        portal_client_id=system.portal_client_id,
                        allowed_origin=system.allowed_origin,
                    ),
                )
                await cancel_run(db, who, run.id)
            return SubmissionResult(
                status="accepted",
                receipt=SubmitReceipt(
                    submission_key=key,
                    kind="service",
                    application_id="late",
                    revision=1,
                    status="accepted",
                ),
            )

    with pytest.raises(ServiceError, match="WRITE_FENCE_STALE"):
        await submit_confirmed(pg_factory, fence, submission.id, LateReply())
    async with pg_factory() as db:
        assert (await db.get(ReceptionSubmission, submission.id)).receipt is None


async def test_definite_conflict_returns_to_edit_and_requires_new_confirmation(pg_factory, job_app):
    fence, submission = await confirmed(pg_factory)

    class ConflictClient:
        async def result(self, actor, key):
            return SubmissionResult(status="not_found", receipt=None)

        async def submit(self, actor, payload):
            raise ServiceError("SUBJECT_CONFLICT", 409)

    with pytest.raises(ServiceError, match="SUBJECT_CONFLICT"):
        await submit_confirmed(pg_factory, fence, submission.id, ConflictClient())
    async with pg_factory() as db:
        draft = await db.get(ReceptionDraft, submission.draft_id)
        assert draft.state == "collecting" and draft.summary_version is None
        assert draft.revision > submission.draft_revision
        assert (await db.get(ReceptionSubmission, submission.id)).status == "failed"


async def test_worker_recovers_pending_submission_with_same_key(
    pg_factory, runtime_env, monkeypatch
):
    class LostReply:
        receipt = None
        creates = 0

        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted" if self.receipt else "not_found", receipt=self.receipt
            )

        async def submit(self, actor, payload):
            self.creates += 1
            self.receipt = SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="recovered-application",
                revision=1,
                status="accepted",
            )
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)

    client = LostReply()
    await install(pg_factory, runtime_env, monkeypatch, client)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    await talk(pg_factory, runtime_env, who, conversation, "采购商")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    async with pg_factory.begin() as db:
        await patch_draft(
            db,
            who,
            view.active_draft.id,
            view.active_draft.revision,
            {"companyName": "甲公司", "contactName": "测试联系人", "contactPhone": "总台分机"},
        )
    await talk(pg_factory, runtime_env, who, conversation, "查看汇总")
    message, view = await talk(pg_factory, runtime_env, who, conversation, "确认提交")
    assert view.active_draft.state == "submitting"
    assert "正在确认提交结果" in message.model_dump_json()
    assert "确认受理后" in message.model_dump_json()
    assert "专员跟进" in message.model_dump_json() and "我的进展" in message.model_dump_json()
    assert "提交成功" not in message.model_dump_json()
    pending_id = view.active_draft.id
    await talk(pg_factory, runtime_env, who, conversation, "切换到供应商")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "切换到采购商")
    assert view.active_draft is not None and view.active_draft.id == pending_id
    message, view = await talk(pg_factory, runtime_env, who, conversation, "查询提交结果")
    assert view.active_draft.state == "accepted" and "我的进展" in message.model_dump_json()
    assert "专员跟进" in message.model_dump_json()
    assert client.creates == 1
    first_id = view.active_draft.id
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我要新建需求")
    assert view.active_draft.id != first_id and view.active_draft.state == "collecting"
    assert len(view.draft_refs) == 2 and client.creates == 1
