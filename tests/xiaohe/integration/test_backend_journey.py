from contextlib import aclosing
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, text, update
from test_artifact_versions import digest
from test_http_api import client_for, session
from test_identity_sessions import actor
from test_reception_graph import model
from test_visitor_expiry import due

from integration.minio_support import minio_env as minio_env
from xiaohe.contracts import AgentOutcome, WriteFence
from xiaohe.events import stream_events
from xiaohe.jobs import RUN_QUEUE, build_job_app
from xiaohe.models import ArtifactVersion, Command, Conversation, Event, Run, Session
from xiaohe.worker import RunWorker

pytestmark = [pytest.mark.pg, pytest.mark.minio, pytest.mark.asyncio]


async def test_guest_directions_reconnect_private_artifact_and_expiry(
    pg_factory, pg_url, runtime_env, minio_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.graph import build_graph

    payload = "隔离组合验收文件".encode()

    async def document(state):
        fence = WriteFence.model_validate(state["write_fence"])
        async with pg_factory.begin() as db:
            version = await minio_env.service.stage_artifact(
                db, fence, "接待说明", "text/plain", digest(payload)
            )
        await minio_env.service.write_artifact_object(version.id, payload)
        async with pg_factory.begin() as db:
            await minio_env.service.finish_artifact(db, fence, version.id)
        return AgentOutcome(
            parts=[{"type": "text", "text": "验收用接待结果"}],
            wait_for_input=False,
            artifact_version_ids=[version.id],
        )

    graph = build_graph(
        factory=pg_factory,
        model=model(intent="professional"),
        handlers={"buyer": document, "organization": document, "supplier": document},
    )
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)

    async def run_pending():
        worker = RunWorker(pg_factory, runtime_env.client)
        async with build_job_app(pg_url, worker.execute_run).open_async() as app:
            await app.run_worker_async(
                queues=[RUN_QUEUE], wait=False, listen_notify=False, install_signal_handlers=False
            )

    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = minio_env.service
        initial = await session(client)
        path = f"/ai-api/v1/conversations/{initial['conversationId']}"
        accepted = await client.post(
            path + "/messages",
            json={"clientMessageId": "journey-1", "text": "我代表供应商，想了解服务"},
        )
        assert accepted.status_code == 202
        cookies = client.cookies
    await run_pending()

    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = minio_env.service
        client.cookies = cookies
        restored = await session(client)
        assert restored["conversationId"] == initial["conversationId"]
        assert len(restored["messages"]) == 2
        async with pg_factory() as db:
            assert (
                await db.get(Conversation, UUID(initial["conversationId"]))
            ).direction == "supplier"
        again = await client.post(
            path + "/messages",
            json={"clientMessageId": "journey-1", "text": "我代表供应商，想了解服务"},
        )
        assert again.json()["run"]["id"] == accepted.json()["run"]["id"]
        assert again.json()["viewer"]["messageCount"] == 1
        switched = await client.post(
            path + "/messages", json={"clientMessageId": "journey-2", "text": "切换到协会组织方向"}
        )
        assert switched.status_code == 202
        await run_pending()
        final = (await client.get(path)).json()
        assert len(final["messages"]) == 4
        async with pg_factory() as db:
            assert (
                await db.get(Conversation, UUID(initial["conversationId"]))
            ).direction == "organization"
        refs = [p for m in final["messages"] for p in m["parts"] if p["type"] == "artifact_ref"]
        assert len(refs) == 2 and all(p["status"] == "ready" for p in refs)
        url = f"/ai-api/v1/artifacts/{refs[0]['artifactId']}/versions/1/download"
        downloaded = await client.get(url)
        assert downloaded.status_code == 200 and downloaded.content == payload
        assert downloaded.headers["cache-control"] == "private, no-store"
        token = client.cookies.get("__Host-XIAOHESESSION")
        who = await actor(pg_factory, token)
        async with pg_factory() as db:
            events = (await db.scalars(select(Event).order_by(Event.sequence))).all()
            assert len((await db.scalars(select(Command))).all()) == 2
            assert all(run.status == "completed" for run in (await db.scalars(select(Run))).all())
            version = await db.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.artifact_id == UUID(refs[0]["artifactId"])
                )
            )
        # A reconnect replays durable events; it never resubmits the input.
        async with aclosing(
            await stream_events(pg_factory, who, UUID(initial["conversationId"]))
        ) as stream:
            replay = [await anext(stream) for _ in events]
        assert [str(row.id) for row in events] == [
            frame.decode().splitlines()[0].removeprefix("id: ") for frame in replay
        ]
        async with httpx.AsyncClient(trust_env=False) as anonymous:
            assert (
                await anonymous.get(f"{minio_env.endpoint}/{minio_env.bucket}/{version.object_key}")
            ).status_code == 403
        async with client_for(pg_factory) as (stranger, other_app):
            other_app.state.xiaohe_artifacts = minio_env.service
            stranger.headers.update(
                {"X-Xiaohe-System-Id": "other", "Origin": "https://other.example"}
            )
            await session(stranger)
            assert (await stranger.get(url)).status_code == 403
        await due(pg_factory, who)
        await runtime_env.worker.maintain()
        assert (await client.get(path)).status_code == 410
        assert (await client.get(url)).status_code == 410
        assert (await client.post("/ai-api/v1/session", json={})).status_code == 410
        async with pg_factory() as db:
            assert (await db.get(Session, who.session_id)).message_count == 2


async def test_quota_retries_reconnect_and_expiry_never_reset_guest(pg_factory, runtime_env):
    async with client_for(pg_factory) as (client, app):
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}"
        async with pg_factory.begin() as db:
            await db.execute(update(Session).values(message_count=99))
        body = {"clientMessageId": "hundredth", "text": "服务介绍"}
        accepted = await client.post(path + "/messages", json=body)
        assert accepted.status_code == 202
        await runtime_env.worker.execute_run(UUID(accepted.json()["run"]["id"]))
        duplicate = await client.post(path + "/messages", json=body)
        assert duplicate.status_code == 202 and duplicate.json()["viewer"]["messageCount"] == 100
        rejected = await client.post(path + "/messages", json={**body, "clientMessageId": "101"})
        assert (
            rejected.status_code == 429
            and rejected.json()["error"]["code"] == "VISITOR_LIMIT_REACHED"
        )
        assert (await session(client))["viewer"]["messageCount"] == 100
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        await due(pg_factory, who)
        await runtime_env.worker.maintain()
        assert (await client.post("/ai-api/v1/session", json={})).status_code == 410
        async with pg_factory() as db:
            assert (await db.get(Session, who.session_id)).message_count == 100


async def test_guest_to_existing_user_with_process_restart_and_artifact(
    pg_factory, pg_url, job_app, minio_env
):
    import asyncio
    import socket

    from test_admission import finish, send
    from test_guest_claim import prepare
    from test_identity_sessions import login

    from integration.test_runtime_crashes import line, ready_api, start, stop
    from xiaohe.conversations import default_conversation
    from xiaohe.jobs import MAINTENANCE_QUEUE, recover_stalled_jobs
    from xiaohe.settings import Settings
    from xiaohe.toolkit_client import ToolkitClient

    _, member_token = await login(pg_factory)
    member = await actor(pg_factory, member_token)
    async with pg_factory.begin() as db:
        canonical = await default_conversation(db, member)
    old = await send(pg_factory, member, canonical.id, "old-user-history")
    await finish(pg_factory, old.run.id)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    processes = []

    async def run_pending(mode="normal"):
        worker = start("worker", mode, port)
        processes.append(worker)
        await line(worker, "READY")
        if mode == "gate":
            await line(worker, "GATED")
        else:
            assert await asyncio.wait_for(asyncio.to_thread(worker.wait), 30) == 0
        return worker

    try:
        api = await ready_api("document", port)
        processes.append(api)
        async with client_for(pg_factory) as (client, app):
            app.state.xiaohe_artifacts = minio_env.service
            guest = await session(client)
            path = f"/ai-api/v1/conversations/{guest['conversationId']}"
            first = await client.post(
                path + "/messages",
                json={"clientMessageId": "supplier", "text": "我代表供应商，想了解服务"},
            )
            assert first.status_code == 202
            await run_pending()
            first_snapshot = (await client.get(path)).json()
            assert any(
                p["type"] == "artifact_ref" for m in first_snapshot["messages"] for p in m["parts"]
            )
            second_body = {"clientMessageId": "organization", "text": "切换到协会组织方向"}
            accepted = await client.post(path + "/messages", json=second_body)
            run_id = UUID(accepted.json()["run"]["id"])
            worker = await run_pending("gate")
            await stop(worker)
            processes.remove(worker)
            await stop(api)
            processes.remove(api)
            cookies = httpx.Cookies(client.cookies)
        assert await recover_stalled_jobs(job_app, seconds_since_heartbeat=0) == 1
        api = await ready_api("document", port)
        processes.append(api)
        await run_pending()
        async with client_for(pg_factory) as (client, app):
            app.state.xiaohe_artifacts = minio_env.service
            client.cookies = cookies
            restored = await session(client)
            assert restored["viewer"]["messageCount"] == 2
            duplicate = await client.post(path + "/messages", json=second_body)
            assert duplicate.json()["run"]["id"] == str(run_id)
            assert len(restored["messages"]) == 4
            before = [(m["id"], m["createdAt"]) for m in restored["messages"]]
            async with pg_factory() as db:
                assert (
                    await db.get(Conversation, UUID(guest["conversationId"]))
                ).direction == "organization"
                assert (
                    await db.scalar(
                        text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:id"),
                        {"id": run_id},
                    )
                    == 1
                )
                objects = (await db.scalars(select(ArtifactVersion))).all()
                original_objects = {(v.id, v.object_key, v.version) for v in objects}
            await send(pg_factory, member, canonical.id, "busy-user-target")
            source = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
            operation_id, calls = await prepare(client, app)
            complete = f"/ai-api/v1/auth/operations/{operation_id}/complete"
            pending = await client.post(complete, json={})
            assert pending.status_code == 202 and pending.json()["viewer"]["kind"] == "user"
            assert pending.headers["Retry-After"] == "1"
            cookies = httpx.Cookies(client.cookies)
            auth_csrf = client.headers["X-Xiaohe-Auth-CSRF"]
            auth_settings = app.state.xiaohe_settings
        await due(pg_factory, source)
        await run_pending()
        coordinator = RunWorker(
            pg_factory,
            ToolkitClient(
                Settings(toolkit_base_url=f"http://127.0.0.1:{port}", auth_secret="fixture-auth")
            ),
            artifacts=minio_env.service,
        )
        async with build_job_app(
            pg_url, coordinator.execute_run, on_claim=coordinator.finish_claim
        ).open_async() as jobs:
            await jobs.run_worker_async(
                queues=[MAINTENANCE_QUEUE],
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
            )
        await coordinator.maintain()
        async with client_for(pg_factory) as (client, app):
            app.state.xiaohe_artifacts = minio_env.service
            app.state.xiaohe_settings = auth_settings
            client.cookies = cookies
            client.headers["X-Xiaohe-Auth-CSRF"] = auth_csrf
            result = await client.post(complete, json={})
            assert result.status_code == 200 and result.json()["merge"]["status"] == "MERGED"
            assert result.json()["viewer"]["userId"] == member.user_id
            assert set(before) <= {(m["id"], m["createdAt"]) for m in result.json()["messages"]}
            assert old.message.id in {m["id"] for m in result.json()["messages"]}
            for version in objects:
                downloaded = await client.get(
                    f"/ai-api/v1/artifacts/{version.artifact_id}/versions/{version.version}/download"
                )
                assert (
                    downloaded.status_code == 200
                    and downloaded.content == "受控进程旅程文件".encode()
                )
            assert (await client.get(path)).status_code == 403
            async with pg_factory() as db:
                assert original_objects <= {
                    (v.id, v.object_key, v.version)
                    for v in await db.scalars(select(ArtifactVersion))
                }
                assert (await db.get(Session, source.session_id)).purged_at is None
            await session(client)
            followup = await client.post(
                f"/ai-api/v1/conversations/{canonical.id}/messages",
                json={"clientMessageId": "user-followup", "text": "继续协会方向"},
            )
            assert followup.status_code == 202
            await run_pending()
            async with pg_factory() as db:
                run = await db.get(Run, UUID(followup.json()["run"]["id"]))
                assert (
                    run.user_id == member.user_id
                    and run.guest_id is None
                    and run.status == "completed"
                )
            client.headers["X-Xiaohe-System-Id"] = "other"
            client.headers["Origin"] = "https://other.example"
            assert (await client.get(f"/ai-api/v1/conversations/{canonical.id}")).status_code == 403
            assert len(calls) == 1
    finally:
        for process in reversed(processes):
            await stop(process)
