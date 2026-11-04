from uuid import UUID

import pytest
from sqlalchemy import select, text
from test_admission import finish, send
from test_artifact_versions import digest
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import actor, login

from integration.minio_support import minio_env as minio_env
from xiaohe.contracts import AgentOutcome, ToolkitOutcome
from xiaohe.conversations import default_conversation
from xiaohe.jobs import MAINTENANCE_QUEUE, RUN_QUEUE, build_job_app, recover_stalled_jobs
from xiaohe.models import ArtifactVersion, Command, GuestClaim
from xiaohe.runs import acquire_run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.minio
async def test_claim_preserves_real_private_object_key_version_and_message_ids(
    pg_factory, runtime_env, minio_env
):
    payload = "认领前后的同一私有文件".encode()
    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = minio_env.service
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "document")
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(turn.run.id))
            version = await minio_env.service.stage_artifact(
                db, fence, "认领文档", "text/plain", digest(payload)
            )
            command = await db.scalar(select(Command).where(Command.run_id == fence.run_id))
        await minio_env.service.write_artifact_object(version.id, payload)
        async with pg_factory.begin() as db:
            await minio_env.service.finish_artifact(db, fence, version.id)
        await runtime_env.worker._save(
            fence,
            command.id,
            ToolkitOutcome(
                kind="outcome",
                outcome=AgentOutcome(
                    parts=[{"type": "text", "text": "文档已生成"}],
                    wait_for_input=False,
                    artifact_version_ids=[version.id],
                ),
            ),
        )
        before = (await client.get(f"/ai-api/v1/conversations/{guest['conversationId']}")).json()
        operation_id, calls = await prepare(client, app)
        claimed = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert claimed.status_code == 200
        assert [(m["id"], m["createdAt"]) for m in claimed.json()["messages"]] == [
            (m["id"], m["createdAt"]) for m in before["messages"]
        ]
        download = await client.get(
            f"/ai-api/v1/artifacts/{version.artifact_id}/versions/1/download"
        )
        assert download.status_code == 200 and download.content == payload
        async with pg_factory() as db:
            current = await db.get(ArtifactVersion, version.id)
            assert current.object_key == version.object_key and current.version == 1
        assert len(calls) == 1


@pytest.mark.parametrize("lost_delivery", [False, True])
async def test_claim_framework_delivery_and_stalled_maintenance_recovery(
    pg_factory, pg_url, job_app, runtime_env, lost_delivery
):
    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        target = await default_conversation(db, member)
    target_turn = await send(pg_factory, member, target.id, "target")
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        source = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, source, UUID(guest["conversationId"]), "source")
        await finish(pg_factory, turn.run.id)
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        assert (await client.post(path, json={})).status_code == 202
        old_worker = await job_app.job_manager.register_worker()
        assert (
            await job_app.job_manager.fetch_job(queues=[MAINTENANCE_QUEUE], worker_id=old_worker)
            is None
        )
        worker = runtime_env.worker
        async with build_job_app(
            pg_url, worker.execute_run, on_claim=worker.finish_claim
        ).open_async() as jobs:
            await jobs.run_worker_async(
                queues=[RUN_QUEUE], wait=False, listen_notify=False, install_signal_handlers=False
            )
            if lost_delivery:
                lost = await job_app.job_manager.fetch_job(
                    queues=[MAINTENANCE_QUEUE], worker_id=old_worker
                )
                assert lost is not None
                await recover_stalled_jobs(job_app, seconds_since_heartbeat=0)
                async with pg_factory() as db:
                    assert (await db.scalar(select(GuestClaim))).status == "pending"
                    assert (
                        await db.scalar(
                            text("SELECT status FROM procrastinate_jobs WHERE id=:id"),
                            {"id": lost.id},
                        )
                        == "failed"
                    )
                await worker.maintain()
            else:
                await jobs.run_worker_async(
                    queues=[MAINTENANCE_QUEUE],
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                )
        result = await client.post(path, json={})
        assert result.status_code == 200 and result.json()["merge"]["status"] == "MERGED"
        assert {m["id"] for m in result.json()["messages"]}.issuperset(
            {turn.message.id, target_turn.message.id}
        )
        assert len(calls) == 1
