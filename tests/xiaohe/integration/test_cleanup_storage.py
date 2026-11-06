import asyncio
import threading
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select
from test_artifact_versions import staged
from test_cleanup_races import cleanup
from test_visitor_expiry import due

from integration.minio_support import minio_env as minio_env
from xiaohe.contracts import ServiceError
from xiaohe.models import ArtifactVersion, GuestObjectTombstone, Message, Session

pytestmark = [pytest.mark.pg, pytest.mark.minio, pytest.mark.asyncio]


async def exists(service, key):
    try:
        await asyncio.to_thread(
            service._s3.head_object, Bucket=service.settings.minio_bucket, Key=key
        )
        return True
    except ClientError as error:
        assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 404
        return False


async def test_real_object_delete_failure_is_retryable_and_expiry_stays_enforced(
    pg_factory, job_app, minio_env, monkeypatch
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    await due(pg_factory, who)
    original = service._s3.delete_object

    def fail(**kwargs):
        raise OSError("controlled delete failure")

    monkeypatch.setattr(service._s3, "delete_object", fail)
    with pytest.raises(ServiceError):
        await cleanup(pg_factory, who.guest_id, service)
    async with pg_factory() as db:
        assert (await db.get(Session, who.session_id)).purged_at is None
        assert await db.get(ArtifactVersion, version.id) is not None
    with pytest.raises(ServiceError) as error:
        await service.read_artifact(who, version.artifact_id, version.version)
    assert error.value.http_status == 410 and await exists(service, version.object_key)
    monkeypatch.setattr(service._s3, "delete_object", original)
    assert await cleanup(pg_factory, who.guest_id, service)
    assert not await exists(service, version.object_key)


@pytest.mark.parametrize("cancel_upload", [False, True])
async def test_cleanup_waits_for_inflight_upload_and_reclaims_late_object(
    pg_factory, job_app, minio_env, monkeypatch, cancel_upload
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    entered, release = threading.Event(), threading.Event()
    original = service._s3.put_object

    def delayed(**kwargs):
        entered.set()
        if not release.wait(15):
            raise TimeoutError("controlled upload gate")
        return original(**kwargs)

    monkeypatch.setattr(service._s3, "put_object", delayed)
    uploading = asyncio.create_task(service.write_artifact_object(version.id, b"test-only"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await due(pg_factory, who)
        if cancel_upload:
            uploading.cancel()
            await asyncio.sleep(0.05)
        assert not await asyncio.wait_for(cleanup(pg_factory, who.guest_id, service), 2)
        async with pg_factory() as db:
            assert await db.get(ArtifactVersion, version.id) is not None
        release.set()
        with pytest.raises(asyncio.CancelledError if cancel_upload else ServiceError):
            await uploading
        assert await exists(service, version.object_key)
        assert await cleanup(pg_factory, who.guest_id, service)
        assert not await exists(service, version.object_key)
        async with pg_factory() as db:
            assert await db.get(ArtifactVersion, version.id) is None
    finally:
        release.set()
        await asyncio.gather(uploading, return_exceptions=True)


async def test_cancel_cleanup_after_object_deleted_retries_without_losing_metadata(
    pg_factory, job_app, minio_env, monkeypatch
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    await due(pg_factory, who)
    deleted = asyncio.Event()
    original = service.delete_expired_object

    async def stop_after_delete(key):
        await original(key)
        deleted.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "delete_expired_object", stop_after_delete)
    task = asyncio.create_task(cleanup(pg_factory, who.guest_id, service))
    try:
        await asyncio.wait_for(deleted.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await exists(service, version.object_key)
        async with pg_factory() as db:
            assert await db.get(ArtifactVersion, version.id) is not None
            assert (await db.get(Session, who.session_id)).purged_at is None
            assert await db.scalar(select(Message.id)) is not None
        monkeypatch.setattr(service, "delete_expired_object", original)
        assert await cleanup(pg_factory, who.guest_id, service)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("pending", [False, True])
async def test_claimed_real_files_never_enter_anonymous_purge(
    pg_factory, job_app, runtime_env, minio_env, pending
):
    from test_admission import finish, send
    from test_guest_claim import prepare
    from test_http_api import client_for, session
    from test_identity_sessions import actor, login

    from xiaohe.conversations import default_conversation
    from xiaohe.models import Artifact

    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        canonical = await default_conversation(db, member)
    target = await send(pg_factory, member, canonical.id, "target")
    if not pending:
        await finish(pg_factory, target.run.id)
    service = minio_env.service
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        source = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        # Stage a file through the new guest's actual run.
        turn = await send(pg_factory, source, UUID(guest["conversationId"]), "file")
        from test_artifact_versions import digest

        from xiaohe.runs import acquire_run

        async with pg_factory.begin() as db:
            active_fence = await acquire_run(db, UUID(turn.run.id))
            owned = await service.stage_artifact(
                db, active_fence, "私有文件", "text/plain", digest(b"test-only")
            )
        await service.write_artifact_object(owned.id, b"test-only")
        async with pg_factory.begin() as db:
            await service.finish_artifact(db, active_fence, owned.id)
        await finish(pg_factory, active_fence.run_id)
        operation_id, calls = await prepare(client, app)
        response = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert response.status_code == (202 if pending else 200)
        await due(pg_factory, source)
        assert not await cleanup(pg_factory, source.guest_id, service)
        assert await exists(service, owned.object_key)
        async with pg_factory() as db:
            artifact = await db.get(Artifact, owned.artifact_id)
            assert artifact.conversation_id == (
                UUID(guest["conversationId"]) if pending else canonical.id
            )
        assert len(calls) == 1


async def test_hard_kill_after_real_object_delete_can_retry_same_cleanup(
    pg_factory, job_app, minio_env
):
    import os
    import subprocess
    import sys
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from integration.test_runtime_crashes import line, stop
    from xiaohe.expiry import expire_guests

    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    await due(pg_factory, who)
    async with pg_factory.begin() as db:
        await expire_guests(db, datetime.now(UTC), 100)
    log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("cleanup_probe.py")), str(who.guest_id)],
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    process.probe_log = log
    try:
        await line(process, "DELETED")
        assert not await exists(service, version.object_key)
        await stop(process)
        async with pg_factory() as db:
            assert await db.get(ArtifactVersion, version.id) is not None
            assert (await db.get(Session, who.session_id)).purged_at is None

        async def retry_after_disconnect():
            while not await cleanup(pg_factory, who.guest_id, service):
                await asyncio.sleep(0.02)

        await asyncio.wait_for(retry_after_disconnect(), 5)
        async with pg_factory() as db:
            assert await db.get(ArtifactVersion, version.id) is None
            assert (await db.get(Session, who.session_id)).purged_at is not None
    finally:
        if process.poll() is None:
            await stop(process)


async def test_delayed_storage_commit_after_purge_is_reaped_from_durable_key_receipt(
    pg_factory, job_app, minio_env
):
    from xiaohe.expiry import purge_expired_guests

    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await due(pg_factory, who)
    assert await cleanup(pg_factory, who.guest_id, service)
    # Simulate S3 completing an already accepted request after its client process died.
    await asyncio.to_thread(
        service._s3.put_object,
        Bucket=service.settings.minio_bucket,
        Key=version.object_key,
        Body=b"test-only",
    )
    assert await exists(service, version.object_key)
    await purge_expired_guests(pg_factory, service, limit=1)
    assert not await exists(service, version.object_key)
    async with pg_factory() as db:
        assert await db.get(ArtifactVersion, version.id) is None


async def test_native_version_marker_never_counts_as_physical_purge(
    pg_factory, job_app, minio_env, monkeypatch
):
    service, who, fence, version = await staged(pg_factory, job_app, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    original = service._s3.head_object

    def versioned(**kwargs):
        return {**original(**kwargs), "VersionId": "fixture-native-version"}

    monkeypatch.setattr(service._s3, "head_object", versioned)
    await due(pg_factory, who)
    with pytest.raises(ServiceError):
        await cleanup(pg_factory, who.guest_id, service)
    assert await exists(service, version.object_key)
    async with pg_factory() as db:
        assert (await db.get(Session, who.session_id)).purged_at is None


@pytest.mark.parametrize("claim_wins", [False, True])
async def test_overlapping_claim_and_real_delete_serialize_at_session_lock(
    pg_factory, job_app, minio_env, monkeypatch, claim_wins
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update
    from test_admission import finish, send
    from test_artifact_versions import digest
    from test_guest_claim import prepare
    from test_http_api import client_for, session
    from test_identity_sessions import actor, login

    from xiaohe import claim, expiry
    from xiaohe.conversations import default_conversation
    from xiaohe.runs import acquire_run

    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        target = await default_conversation(db, member)
    await send(pg_factory, member, target.id, "busy-target")
    service = minio_env.service
    entered, release = asyncio.Event(), asyncio.Event()
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "file")
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(turn.run.id))
            version = await service.stage_artifact(
                db, fence, "并发测试", "text/plain", digest(b"test-only")
            )
        await service.write_artifact_object(version.id, b"test-only")
        await finish(pg_factory, fence.run_id)
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        if claim_wins:
            cutoff = datetime.now(UTC) + timedelta(seconds=1)
            async with pg_factory.begin() as db:
                await db.execute(
                    update(Session).where(Session.id == who.session_id).values(expires_at=cutoff)
                )
            original = claim._claim_guest

            async def gated(*args):
                result = await original(*args)
                entered.set()
                await release.wait()
                return result

            monkeypatch.setattr(claim, "_claim_guest", gated)
            primary = asyncio.create_task(client.post(path, json={}))
        else:
            await due(pg_factory, who)
            original = expiry._delete_guest_bodies

            async def gated(*args):
                entered.set()
                await release.wait()
                await original(*args)

            monkeypatch.setattr(expiry, "_delete_guest_bodies", gated)
            primary = asyncio.create_task(cleanup(pg_factory, who.guest_id, service))
        competitor = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if claim_wins:
                await asyncio.sleep(max(0, (cutoff - datetime.now(UTC)).total_seconds()) + 0.02)
                assert not await asyncio.wait_for(cleanup(pg_factory, who.guest_id, service), 2)
                assert await exists(service, version.object_key)
            else:
                competitor = asyncio.create_task(client.post(path, json={}))
                await asyncio.sleep(0.05)
                assert not competitor.done() and not await exists(service, version.object_key)
            release.set()
            result = await asyncio.wait_for(primary, 5)
            if claim_wins:
                assert result.status_code == 202
                assert not await cleanup(pg_factory, who.guest_id, service)
                assert await exists(service, version.object_key)
            else:
                assert result
                result = await asyncio.wait_for(competitor, 5)
                assert (
                    result.status_code == 200
                    and result.json()["merge"]["status"] == "SKIPPED_EXPIRED"
                )
                assert not await exists(service, version.object_key)
        finally:
            release.set()
            for task in (primary, competitor):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["guest", "receipt"])
async def test_slow_storage_yields_single_worker_to_chat_and_next_recovery_scan(
    pg_factory, pg_url, runtime_env, minio_env, monkeypatch, phase
):
    from test_admission import send, visitor

    from xiaohe import expiry
    from xiaohe.jobs import MAINTENANCE_QUEUE, RECOVERY_TASK, RUN_QUEUE, build_job_app
    from xiaohe.models import GuestObjectTombstone, Run
    from xiaohe.worker import RunWorker

    service, who, fence, version = await staged(pg_factory, None, artifacts=minio_env.service)
    await service.write_artifact_object(version.id, b"test-only")
    await due(pg_factory, who)
    if phase == "receipt":
        assert await cleanup(pg_factory, who.guest_id, service)
    entered = asyncio.Event()

    async def slow_store(key):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "delete_expired_object", slow_store)
    budget = getattr(expiry, "CLEANUP_BUDGET_SECONDS", 5)
    worker = RunWorker(pg_factory, runtime_env.client, artifacts=service)
    scans = []

    async def maintain():
        scans.append(True)
        await worker.maintain()

    app = build_job_app(pg_url, worker.execute_run, on_maintenance=maintain)
    async with app.open_async():
        first_scan = await app.tasks[RECOVERY_TASK].defer_async(timestamp=0)
        running = asyncio.create_task(
            app.run_worker_async(
                queues=[RUN_QUEUE, MAINTENANCE_QUEUE],
                wait=False,
                listen_notify=False,
                concurrency=1,
                install_signal_handlers=False,
                shutdown_graceful_timeout=0,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            live, conversation = await visitor(pg_factory)
            accepted = await send(pg_factory, live, conversation, "chat while storage is slow")
            second_scan = await app.tasks[RECOVERY_TASK].defer_async(timestamp=1)

            async def completed():
                while True:
                    async with pg_factory() as db:
                        if (await db.get(Run, UUID(accepted.run.id))).status == "completed":
                            return
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(completed(), budget + 3)
            await asyncio.wait_for(running, budget * 2 + 3)
            assert len(scans) >= 2
            async with pg_factory() as db:
                from sqlalchemy import text

                for job_id in (first_scan, second_scan):
                    assert (
                        await db.scalar(
                            text("SELECT status FROM procrastinate_jobs WHERE id=:id"),
                            {"id": job_id},
                        )
                        == "succeeded"
                    )
                if phase == "guest":
                    record = await db.get(Session, who.session_id)
                    assert record.purged_at is None and record.cleanup_checked_at is not None
                else:
                    assert (
                        await db.get(GuestObjectTombstone, version.object_key)
                    ).checked_at is not None
            assert await exists(service, version.object_key) is (phase == "guest")
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_large_guest_keeps_object_progress_when_its_time_budget_expires(
    pg_factory, job_app, minio_env, monkeypatch
):
    from datetime import UTC, datetime

    from test_artifact_versions import digest

    from xiaohe import expiry

    service, who, fence, first = await staged(pg_factory, job_app, artifacts=minio_env.service)
    versions = [first]
    for content in (b"second", b"third"):
        async with pg_factory.begin() as db:
            versions.append(
                await service.stage_artifact(
                    db,
                    fence,
                    "测试文件",
                    "text/plain",
                    digest(content),
                    artifact_id=first.artifact_id,
                )
            )
    for version, content in zip(versions, (b"test-only", b"second", b"third"), strict=True):
        await service.write_artifact_object(version.id, content)
    await due(pg_factory, who)
    async with pg_factory.begin() as db:
        await expiry.expire_guests(db, datetime.now(UTC), 100)
    remove = service.delete_expired_object

    async def delayed(key):
        calls.append(key)
        if len(calls) > 1:
            await asyncio.sleep(10)
        await remove(key)

    calls = []
    monkeypatch.setattr(service, "delete_expired_object", delayed)
    monkeypatch.setattr(expiry, "CLEANUP_BUDGET_SECONDS", 1.0)
    await expiry.purge_expired_guests(pg_factory, service)
    async with pg_factory() as db:
        assert (await db.get(Session, who.session_id)).purged_at is None
        assert (
            await db.scalar(
                select(GuestObjectTombstone.checked_at).where(
                    GuestObjectTombstone.object_key == calls[0]
                )
            )
            is not None
        )
    monkeypatch.setattr(service, "delete_expired_object", remove)
    for _ in range(5):
        await expiry.purge_expired_guests(pg_factory, service)
    async with pg_factory() as db:
        assert (await db.get(Session, who.session_id)).purged_at is not None
    assert not any([await exists(service, version.object_key) for version in versions])
