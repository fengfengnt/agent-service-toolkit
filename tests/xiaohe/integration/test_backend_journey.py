from contextlib import aclosing
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, update
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
