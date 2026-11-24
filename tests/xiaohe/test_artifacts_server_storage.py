import hashlib
import json
from uuid import UUID, uuid4

import pytest
from server_file_support import FileServer
from server_file_support import files_env as files_env
from sqlalchemy import select
from test_admission import finish, send
from test_artifact_versions import staged
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import actor as current_actor

from xiaohe.contracts import ServiceError
from xiaohe.models import ArtifactVersion, System
from xiaohe.server_files import guest_owner

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_generated_files_and_previews_are_managed_by_server_not_ai_storage(pg_factory, job_app):
    server = FileServer()
    artifacts, actor, fence, version = await staged(pg_factory, job_app)
    artifacts.files = server.client()
    await artifacts.write_artifact_object(version.id, b"test-only")
    async with pg_factory.begin() as db:
        await artifacts.finish_artifact(db, fence, version.id)
    view = await artifacts.read_artifact(actor, version.artifact_id, version.version)
    assert view.preview == {"format": "text", "text": "test-only"}
    async with pg_factory() as db:
        row = await db.get(ArtifactVersion, version.id)
        assert "test-only" not in str(row.preview)
    assert not hasattr(artifacts, "_s3")
    stream = await artifacts.download_artifact(actor, version.artifact_id, version.version)
    assert b"".join([chunk async for chunk in stream]) == b"test-only"
    await stream.aclose()


async def test_cancel_before_upload_prevents_late_generated_file_from_reappearing(pg_factory, job_app):
    server = FileServer()
    artifacts, actor, _, version = await staged(pg_factory, job_app)
    artifacts.files = server.client()
    async with pg_factory() as db:
        owner = guest_owner(await db.get(System, actor.system.system_id), actor.guest_id)
    await artifacts.delete_expired_object(version.object_key, owner=owner)
    with pytest.raises(ServiceError):
        await artifacts.write_artifact_object(version.id, b"test-only")
    assert all(row["state"] == "removed" for row in server.rows.values())


async def test_guest_claim_transfers_files_and_keeps_failed_transfer_pending(pg_factory, job_app):
    import httpx

    from xiaohe.artifacts import ArtifactService
    from xiaohe.claim import finish_pending_claim
    from xiaohe.models import GuestClaim, User
    from xiaohe.runs import acquire_run
    from xiaohe.server_files import user_owner

    server = FileServer()
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await current_actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "生成测试文件")
        artifacts = ArtifactService(pg_factory, app.state.xiaohe_settings)
        app.state.xiaohe_artifacts = artifacts
        artifacts.files = server.client()
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(turn.run.id))
            version = await artifacts.stage_artifact(db, fence, "测试文件", "text/plain", hashlib.sha256(b"test-only").hexdigest())
        await artifacts.write_artifact_object(version.id, b"test-only")
        async with pg_factory.begin() as db:
            await artifacts.finish_artifact(db, fence, version.id)
        await finish(pg_factory, turn.run.id)
        original = artifacts.files.transport.handler

        def unavailable(request):
            if request.url.path.endswith("/metadata") and json.loads(request.content).get("operation") == "transfer":
                return httpx.Response(503)
            return original(request)

        artifacts.files.transport.handler = unavailable
        operation_id, _ = await prepare(client, app)
        result = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert result.status_code == 202 and result.json()["merge"]["status"] == "MERGE_PENDING"
        artifacts.files.transport.handler = original
        async with pg_factory() as db:
            claim = await db.scalar(select(GuestClaim))
        await finish_pending_claim(pg_factory, claim.id)
        async with pg_factory() as db:
            assert (await db.get(GuestClaim, claim.id)).status == "merged"
            owner = user_owner(await db.get(User, claim.user_id))
        assert server.rows[str(version.id)]["owner"] == owner
        member = await current_actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        assert (await artifacts.read_artifact(member, version.artifact_id, version.version)).preview["text"] == "test-only"


async def test_historical_material_download_needs_only_server_registration(pg_factory, job_app, files_env):
    from test_identity_sessions import login
    from test_reception_files import PDF

    from xiaohe.models import User
    from xiaohe.reception.form_files import download_form_file
    from xiaohe.server_files import user_owner

    _, token = await login(pg_factory)
    member = await current_actor(pg_factory, token)
    async with pg_factory() as db:
        owner = user_owner(await db.get(User, member.user_id))
    file_id = str(uuid4())
    await files_env.service.files.write(owner, file_id, "历史材料.pdf", "application/pdf", PDF, "case")
    ref, content = await download_form_file(pg_factory, member, file_id, artifacts=files_env.service)
    assert ref.id == file_id and ref.category == "case" and content == PDF
