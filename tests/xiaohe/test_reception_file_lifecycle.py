from uuid import UUID

import pytest
from integration.minio_support import minio_env as minio_env
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import actor, bootstrap, login
from test_reception_files import PDF, chunks, draft_file
from test_visitor_expiry import due

from xiaohe.expiry import purge_expired_guest
from xiaohe.models import Conversation
from xiaohe.reception.contracts import CreateDraft, InitFile
from xiaohe.reception.files import download_file, init_file, write_file
from xiaohe.reception.models import ReceptionDraft, ReceptionFile, ReceptionFlow
from xiaohe.reception.store import create_draft, load_reception, patch_draft

pytestmark = [pytest.mark.pg, pytest.mark.minio, pytest.mark.asyncio]


async def test_expired_guest_cleans_reception_objects_and_private_rows(pg_factory, minio_env):
    who, _, draft_id, upload = await draft_file(pg_factory)
    await write_file(
        pg_factory, who, draft_id, upload.file.id, chunks(PDF), artifacts=minio_env.service
    )
    async with pg_factory() as db:
        key = (await db.get(ReceptionFile, UUID(upload.file.id))).object_key
    await due(pg_factory, who)
    assert await purge_expired_guest(pg_factory, who.guest_id, minio_env.service)
    async with pg_factory() as db:
        assert await db.get(ReceptionFile, UUID(upload.file.id)) is None
        assert await db.get(ReceptionDraft, UUID(draft_id)) is None
    result = await minio_env.service._io(
        lambda: minio_env.service._s3.list_objects_v2(Bucket=minio_env.bucket, Prefix=key)
    )
    assert not result.get("Contents")


@pytest.mark.parametrize("existing_user", [False, True])
async def test_login_claim_preserves_draft_and_private_file_in_target_conversation(
    pg_factory, minio_env, existing_user
):
    if existing_user:
        _, token = await login(pg_factory)
        target_snapshot, _, _ = await bootstrap(pg_factory, token)
        target_actor = await actor(pg_factory, token)
        async with pg_factory.begin() as db:
            existing = await create_draft(
                db,
                target_actor,
                UUID(target_snapshot.conversation_id),
                CreateDraft(client_draft_id="guest-one", kind="service", direction="supplier"),
            )
            await patch_draft(db, target_actor, existing.id, 1, {"companyName": "历史公司"})
    async with client_for(pg_factory) as (client, app):
        initial = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation_id = UUID(initial["conversationId"])
        async with pg_factory.begin() as db:
            draft = await create_draft(
                db,
                who,
                conversation_id,
                CreateDraft(client_draft_id="guest-one", kind="service", direction="supplier"),
            )
            draft = await patch_draft(
                db, who, draft.id, draft.revision, {"companyName": "访客公司"}
            )
            upload = await init_file(
                db,
                who,
                draft.id,
                InitFile(
                    client_upload_id="file-one",
                    category="product_manual",
                    filename="企业.pdf",
                    media_type="application/pdf",
                    bytes=len(PDF),
                    expected_revision=draft.revision,
                ),
            )
            (await db.get(Conversation, conversation_id)).direction = "supplier"
        await write_file(
            pg_factory, who, draft.id, upload.file.id, chunks(PDF), artifacts=minio_env.service
        )
        operation_id, _ = await prepare(client, app)
        completed = await client.post(
            "/ai-api/v1/auth/operations/" + operation_id + "/complete", json={}
        )
        assert completed.status_code == 200, completed.text
        value = completed.json()
        target = UUID(value["conversationId"])
        user = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        async with pg_factory.begin() as db:
            view = await load_reception(db, user, target)
            assert any(ref.id == draft.id for ref in view.draft_refs)
            if existing_user:
                assert {ref.subject_name for ref in view.draft_refs} == {"历史公司", "访客公司"}
                assert view.active_draft is None
            assert (await db.get(ReceptionFile, UUID(upload.file.id))).conversation_id == target
            if target != conversation_id:
                assert await db.get(ReceptionFlow, conversation_id) is None
        _, content = await download_file(
            pg_factory, user, upload.file.id, artifacts=minio_env.service
        )
        assert content == PDF
