from uuid import UUID

import pytest
from integration.minio_support import minio_env as minio_env
from test_http_api import client_for, session
from test_identity_sessions import actor, bootstrap, login
from test_reception_files import PDF, chunks
from test_reception_flow import send_in_db

from xiaohe.contracts import ServiceError
from xiaohe.reception.api import create_reception_file_router, create_reception_router
from xiaohe.reception.contracts import (
    ApplicationView,
    CreateDraft,
    InitFile,
    SubmissionResult,
    SubmitReceipt,
)
from xiaohe.reception.files import detach_file, download_operational_file, init_file, write_file
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.reception.submission import confirm_draft, create_draft_from_business, submit_confirmed
from xiaohe.runs import acquire_run
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.minio, pytest.mark.asyncio]


async def test_corrupt_application_reference_cannot_expose_another_owners_file(
    pg_factory, minio_env
):
    from sqlalchemy import select

    from xiaohe.reception.models import ReceptionSubmission

    first, _, first_draft, _, _ = await confirmed_file(pg_factory, minio_env.service)
    _, _, _, second_file, second_payload = await confirmed_file(
        pg_factory, minio_env.service, sub="member:1:another-user"
    )
    async with pg_factory.begin() as db:
        record = await db.scalar(
            select(ReceptionSubmission).where(ReceptionSubmission.draft_id == UUID(first_draft))
        )
        record.payload = record.payload | {"files": second_payload["files"]}
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await download_operational_file(
            pg_factory,
            "portal",
            second_file,
            "accepted-app",
            first.user_id,
            artifacts=minio_env.service,
        )


async def confirmed_file(factory, artifacts, *, invalid=False, sub="member:1:fixture"):
    _, token = await login(factory, sub=sub)
    initial, _, _ = await bootstrap(factory, token)
    who, conversation = await actor(factory, token), UUID(initial.conversation_id)
    async with factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", kind="service", direction="supplier"),
        )
        upload = await init_file(
            db,
            who,
            draft.id,
            InitFile(
                client_upload_id="one",
                category="product_manual",
                filename="企业.pdf",
                media_type="application/pdf",
                bytes=len(PDF),
                expected_revision=1,
            ),
        )
    if invalid:
        with pytest.raises(ServiceError, match="FILE_INVALID"):
            await write_file(
                factory, who, draft.id, upload.file.id, chunks(b"x" * len(PDF)), artifacts=artifacts
            )
    else:
        await write_file(factory, who, draft.id, upload.file.id, chunks(PDF), artifacts=artifacts)
    async with factory.begin() as db:
        row = await db.get(ReceptionDraft, UUID(draft.id))
        view = await patch_draft(db, who, draft.id, row.revision, {"companyName": "甲公司"})
        row.state, row.step, row.summary_version = "review", "summary", view.revision
        turn = await send_in_db(db, who, conversation, "确认提交")
        submission = await confirm_draft(
            db, who, draft.id, row.revision, row.revision, UUID(turn.message.id)
        )
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(turn.run.id))

    class Business:
        async def result(self, actor, key):
            return SubmissionResult(status="not_found", receipt=None)

        async def submit(self, actor, payload):
            return SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="accepted-app",
                revision=1,
                status="accepted",
            )

    await submit_confirmed(factory, fence, submission.id, Business())
    return who, conversation, draft.id, upload.file.id, submission.payload


async def test_historic_file_survives_edit_detach_and_wrong_operational_binding_is_denied(
    pg_factory, minio_env
):
    who, conversation, draft_id, file_id, payload = await confirmed_file(
        pg_factory, minio_env.service
    )
    ref, content = await download_operational_file(
        pg_factory, "portal", file_id, "accepted-app", who.user_id, artifacts=minio_env.service
    )
    assert content == PDF
    async with client_for(pg_factory) as (client, app):
        settings = Settings(
            toolkit_base_url="http://runtime",
            auth_secret="fixture-auth",
            reception_file_application_id="co-files",
            reception_file_secret="f" * 48,
            reception_file_system_id="portal",
        )
        app.include_router(create_reception_file_router(pg_factory, settings, minio_env.service))
        result = await client.post(
            "/internal-api/reception-files/v1/files/" + file_id + "/download",
            json={"applicationId": "accepted-app", "ownerAiUserId": who.user_id},
            headers={"Authorization": "Bearer " + "f" * 48, "X-Reception-Application": "co-files"},
        )
        assert result.status_code == 200 and result.content == PDF
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await download_operational_file(
            pg_factory, "portal", file_id, "wrong-app", who.user_id, artifacts=minio_env.service
        )

    class CurrentSource:
        async def application(self, actor, application_id):
            return ApplicationView(
                id=application_id,
                subject_id="company-one",
                direction="supplier",
                revision=1,
                public_fields=payload["publicFields"],
                request_fields=payload["requestFields"],
                files=[ref],
            )

    edit = await create_draft_from_business(
        pg_factory,
        who,
        conversation,
        CreateDraft(
            client_draft_id="edit-one",
            kind="service",
            direction="supplier",
            source_application_id="accepted-app",
        ),
        CurrentSource(),
    )
    async with pg_factory.begin() as db:
        assert not (await detach_file(db, who, edit.id, file_id, edit.revision)).files
    _, content = await download_operational_file(
        pg_factory, "portal", file_id, "accepted-app", who.user_id, artifacts=minio_env.service
    )
    assert content == PDF


async def test_failed_optional_upload_does_not_block_minimum_submission(pg_factory, minio_env):
    _, _, _, _, payload = await confirmed_file(pg_factory, minio_env.service, invalid=True)
    assert payload["files"] == [] and payload["publicFields"]["companyName"] == "甲公司"


async def test_http_file_access_uses_cookie_or_independent_operational_credential(
    pg_factory, minio_env
):
    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = minio_env.service
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        draft = await client.post(
            "/ai-api/v1/reception/conversations/" + snapshot["conversationId"] + "/drafts",
            json={"clientDraftId": "http-one", "kind": "service", "direction": "supplier"},
        )
        path = "/ai-api/v1/reception/drafts/" + draft.json()["id"] + "/files"
        upload = await client.post(
            path,
            json={
                "clientUploadId": "http-file",
                "category": "product_manual",
                "filename": "企业.pdf",
                "mediaType": "application/pdf",
                "bytes": len(PDF),
                "expectedRevision": 1,
            },
        )
        assert upload.status_code == 201, upload.text
        written = await client.put(upload.json()["uploadPath"], content=PDF)
        assert written.status_code == 200 and written.json()["status"] == "ready"
        url = "/ai-api/v1/reception/files/" + upload.json()["file"]["id"] + "/download"
        downloaded = await client.get(url)
        assert (
            downloaded.content == PDF and downloaded.headers["cache-control"] == "private, no-store"
        )
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        client.cookies.clear()
        assert (await client.get(url)).status_code == 401
        settings = Settings(
            toolkit_base_url="http://runtime",
            auth_secret="fixture-auth",
            reception_file_application_id="co-files",
            reception_file_secret="f" * 48,
            reception_file_system_id="portal",
        )
        app.include_router(create_reception_file_router(pg_factory, settings, minio_env.service))
        private_url = (
            "/internal-api/reception-files/v1/files/" + upload.json()["file"]["id"] + "/download"
        )
        body = {"applicationId": "not-accepted", "ownerAiUserId": "not-an-owner"}
        assert (
            await client.post(
                private_url,
                json=body,
                headers={
                    "Authorization": "Bearer fixture-auth",
                    "X-Reception-Application": "co-files",
                },
            )
        ).status_code == 403
        assert (
            await client.post(
                private_url,
                json=body,
                headers={
                    "Authorization": "Bearer " + "f" * 48,
                    "X-Reception-Application": "co-files",
                },
            )
        ).status_code == 403
