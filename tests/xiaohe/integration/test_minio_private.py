import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import func, update
from test_artifact_versions import staged

from integration.minio_support import minio_env as minio_env
from xiaohe.contracts import ServiceError
from xiaohe.models import ArtifactVersion, Session

pytestmark = [pytest.mark.pg, pytest.mark.minio, pytest.mark.asyncio]


async def test_minio_image_lock_is_immutable():
    lock = json.loads(Path("deploy/xiaohe/images.lock.json").read_text("utf-8"))["minio"]
    assert lock["tag"] == "RELEASE.2025-10-15T17-29-55Z"
    assert lock["digest"].startswith("sha256:") and len(lock["digest"]) == 71
    assert lock["platform"] == "linux/amd64" and lock["publisher"] == "coollabsio"
    assert "XIAOHE_TEST_MINIO_IMAGE" in Path("deploy/xiaohe/compose.test.yml").read_text("utf-8")


async def test_real_private_minio_upload_download_and_dedicated_credentials(
    pg_factory, job_app, minio_env
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    await service.write_artifact_object(version.id, b"test-only")
    async with pg_factory.begin() as db:
        view = await service.finish_artifact(db, fence, version.id)
    assert view.status == "ready" and view.preview.text == "test-only"
    assert view.download_path.startswith("/ai-api/v1/")
    stream = await service.download_artifact(who, version.artifact_id, 1)
    assert b"".join([part async for part in stream]) == b"test-only"
    async with httpx.AsyncClient(trust_env=False) as client:
        anonymous = await client.get(
            f"{minio_env.endpoint}/{minio_env.bucket}/{version.object_key}"
        )
    assert anonymous.status_code == 403
    with pytest.raises(ClientError) as denied:
        await asyncio.to_thread(service._s3.list_objects_v2, Bucket="test-xiaohe-foreign")
    assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403


async def test_expire_during_upload_never_ready_and_io_does_not_block_loop(
    pg_factory, job_app, minio_env, monkeypatch
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    uploaded, release = threading.Event(), threading.Event()
    original = service._s3.put_object

    def blocked(**kwargs):
        value = original(**kwargs)
        uploaded.set()
        if not release.wait(10):
            raise TimeoutError("test upload gate")
        return value

    monkeypatch.setattr(service._s3, "put_object", blocked)
    task = asyncio.create_task(service.write_artifact_object(version.id, b"test-only"))
    try:
        assert await asyncio.to_thread(uploaded.wait, 5)
        # A separate ORM transaction progresses while the synchronous S3 call is gated.
        async with pg_factory.begin() as db:
            await db.execute(
                update(Session)
                .where(Session.id == who.session_id)
                .values(expires_at=func.clock_timestamp())
            )
        release.set()
        with pytest.raises(ServiceError):
            await task
        async with pg_factory() as db:
            row = await db.get(ArtifactVersion, version.id)
            assert row.status != "ready" and row.cleanup_pending
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_http_artifact_download_is_private_and_does_not_expose_storage_key(
    pg_factory, job_app, minio_env
):
    from uuid import UUID

    from test_artifact_versions import digest
    from test_http_api import client_for, session

    from xiaohe.runs import acquire_run

    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = minio_env.service
        snapshot = await session(client)
        accepted = (
            await client.post(
                f"/ai-api/v1/conversations/{snapshot['conversationId']}/messages",
                json={"clientMessageId": "m1", "text": "生成测试文件"},
            )
        ).json()
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(accepted["run"]["id"]))
            version = await minio_env.service.stage_artifact(
                db, fence, "演示文件 😀", "text/markdown", digest(b"test-only")
            )
        await minio_env.service.write_artifact_object(version.id, b"test-only")
        async with pg_factory.begin() as db:
            view = await minio_env.service.finish_artifact(db, fence, version.id)
        metadata = await client.get(view.download_path.removesuffix("/download"))
        assert metadata.status_code == 200 and metadata.json()["preview"]["format"] == "text"
        assert version.object_key not in metadata.text and minio_env.endpoint not in metadata.text
        response = await client.get(view.download_path)
        assert response.status_code == 200 and response.content == b"test-only"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
        from xiaohe.models import Run

        async with pg_factory.begin() as db:
            run = await db.get(Run, fence.run_id)
            await db.execute(
                update(Session)
                .where(Session.id == run.session_id)
                .values(expires_at=func.clock_timestamp())
            )
        assert (await client.get(view.download_path)).status_code == 410


async def test_real_minio_survives_container_and_client_restart(pg_factory, job_app, minio_env):
    import subprocess
    import time

    from xiaohe.artifacts import ArtifactService

    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    async with pg_factory.begin() as db:
        await service.finish_artifact(db, fence, version.id)

    def restart():
        command = ["wsl", "-d", "Ubuntu", "--", "docker"]
        label = subprocess.run(
            [
                *command,
                "inspect",
                minio_env.container,
                "--format",
                '{{index .Config.Labels "codex.task"}}',
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert label == "xiaohe-agent-test"
        subprocess.run([*command, "restart", minio_env.container], check=True, capture_output=True)
        with httpx.Client(trust_env=False, timeout=2) as http:
            for _ in range(100):
                try:
                    if http.get(minio_env.endpoint + "/minio/health/ready").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise AssertionError("isolated MinIO restart timeout")

    await asyncio.to_thread(restart)
    replacement = ArtifactService(pg_factory, minio_env.settings)
    try:
        assert (await replacement.read_artifact(who, version.artifact_id, 1)).status == "ready"
        stream = await replacement.download_artifact(who, version.artifact_id, 1)
        assert b"".join([chunk async for chunk in stream]) == b"test-only"
    finally:
        await replacement.close()


async def test_bad_existing_object_never_becomes_ready(pg_factory, job_app, minio_env):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await asyncio.to_thread(
        service._s3.put_object,
        Bucket=minio_env.bucket,
        Key=version.object_key,
        Body=b"corrupt-test-object",
    )
    with pytest.raises(ServiceError):
        await service.write_artifact_object(version.id, b"test-only")
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError):
            await service.finish_artifact(db, fence, version.id)
    assert (await service.read_artifact(who, version.artifact_id, 1)).status == "pending"


async def test_download_rechecks_session_between_chunks(pg_factory, job_app, minio_env):
    from test_artifact_versions import digest

    service, who, fence, first = await staged(pg_factory, job_app, artifacts=minio_env.service)
    content = b"test-only-" * 8000
    async with pg_factory.begin() as db:
        version = await service.stage_artifact(
            db, fence, "大测试文件", "text/plain", digest(content), artifact_id=first.artifact_id
        )
    await service.write_artifact_object(version.id, content)
    async with pg_factory.begin() as db:
        await service.finish_artifact(db, fence, version.id)
    stream = await service.download_artifact(who, version.artifact_id, version.version)
    assert len(await anext(stream)) == 65536
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp())
        )
    with pytest.raises(ServiceError) as error:
        await anext(stream)
    assert error.value.http_status == 410


async def test_ready_reference_is_canonical_and_foreign_worker_is_denied(
    pg_factory, job_app, minio_env, runtime_env, monkeypatch
):
    from uuid import UUID

    from test_admission import send, visitor

    from agents.agents import agents
    from xiaohe.contracts import AgentOutcome
    from xiaohe.conversations import load_snapshot
    from xiaohe.graph import build_graph
    from xiaohe.models import Run

    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    async with pg_factory.begin() as db:
        await service.finish_artifact(db, fence, version.id)

    async def handler(state):
        return AgentOutcome(
            parts=[
                {"type": "text", "text": "测试文件就绪"},
                {
                    "type": "artifact_ref",
                    "artifactId": str(version.artifact_id),
                    "version": 1,
                    "title": "错误标题",
                    "mediaType": "text/html",
                    "status": "pending",
                },
            ],
            wait_for_input=False,
            artifact_version_ids=[version.id],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    await runtime_env.worker.execute_run(fence.run_id)
    async with pg_factory.begin() as db:
        snapshot = await load_snapshot(db, who, (await db.get(Run, fence.run_id)).conversation_id)
        reference = snapshot.messages[-1].parts[-1]
        assert reference.type == "artifact_ref" and reference.title == version.title
        assert reference.status == "ready" and reference.media_type == "text/plain"
    stranger, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, stranger, conversation, "foreign")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "failed"
