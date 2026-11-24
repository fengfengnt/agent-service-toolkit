from uuid import UUID

import pytest
from server_file_support import files_env as files_env
from sqlalchemy import func, update
from test_artifact_versions import staged

from xiaohe.contracts import ServiceError
from xiaohe.models import Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_http_artifact_download_is_private_and_does_not_expose_storage_key(
    pg_factory, job_app, files_env
):

    from test_artifact_versions import digest
    from test_http_api import client_for, session

    from xiaohe.runs import acquire_run

    async with client_for(pg_factory) as (client, app):
        app.state.xiaohe_artifacts = files_env.service
        snapshot = await session(client)
        accepted = (
            await client.post(
                f"/ai-api/v1/conversations/{snapshot['conversationId']}/messages",
                json={"clientMessageId": "m1", "text": "生成测试文件"},
            )
        ).json()
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(accepted["run"]["id"]))
            version = await files_env.service.stage_artifact(
                db, fence, "演示文件 😀", "text/markdown", digest(b"test-only")
            )
        await files_env.service.write_artifact_object(version.id, b"test-only")
        async with pg_factory.begin() as db:
            view = await files_env.service.finish_artifact(db, fence, version.id)
        metadata = await client.get(view.download_path.removesuffix("/download"))
        assert metadata.status_code == 200 and metadata.json()["preview"]["format"] == "text"
        assert version.object_key not in metadata.text and "https://server.test" not in metadata.text
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


async def test_download_rechecks_session_between_chunks(pg_factory, job_app, files_env):
    from test_artifact_versions import digest

    service, who, fence, first = await staged(pg_factory, job_app, artifacts=files_env.service)
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
    pg_factory, job_app, files_env, runtime_env, monkeypatch
):

    from test_admission import send, visitor

    from agents.agents import agents
    from xiaohe.contracts import AgentOutcome
    from xiaohe.conversations import load_snapshot
    from xiaohe.graph import build_graph
    from xiaohe.models import Run

    service, who, fence, version = await staged(pg_factory, job_app, artifacts=files_env.service)
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
