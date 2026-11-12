from uuid import UUID

import pytest
from native_support import send_in_db
from sqlalchemy import func, select
from test_identity_sessions import actor, bootstrap, login

from xiaohe.contracts import ServiceError
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
        from sqlalchemy import select

        from xiaohe.models import Command

        cmd = await db.scalar(
            select(Command).where(Command.message_id == UUID(accepted.message.id))
        )
        cmd.payload = cmd.payload | {
            "reception": {
                "action": "confirm",
                "draftId": view.id,
                "expectedRevision": view.revision,
                "summaryVersion": view.revision,
            }
        }
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
