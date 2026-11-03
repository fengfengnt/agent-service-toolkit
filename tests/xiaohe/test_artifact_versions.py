import hashlib
from uuid import UUID

import pytest
from sqlalchemy import select
from test_admission import send, visitor

from xiaohe.artifacts import ArtifactService
from xiaohe.contracts import ServiceError
from xiaohe.models import ArtifactVersion
from xiaohe.runs import acquire_run
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def digest(content: bytes):
    return hashlib.sha256(content).hexdigest()


def service(factory):
    return ArtifactService(
        factory, Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth")
    )


async def staged(factory, job_app, *, title="测试文件", artifacts=None):
    who, conversation = await visitor(factory)
    accepted = await send(factory, who, conversation, "initial")
    artifacts = artifacts or service(factory)
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        version = await artifacts.stage_artifact(
            db, fence, title, "text/plain", digest(b"test-only")
        )
    return artifacts, who, fence, version


async def test_ready_only_after_object_and_metadata(pg_factory, job_app):
    artifacts, who, fence, version = await staged(pg_factory, job_app)
    view = await artifacts.read_artifact(who, version.artifact_id, version.version)
    assert view.status == "pending" and view.download_path is None
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError):
            await artifacts.finish_artifact(db, fence, version.id)
    with pytest.raises(ServiceError):
        await artifacts.write_artifact_object(version.id, b"test-only")
    async with pg_factory() as db:
        row = await db.get(ArtifactVersion, version.id)
        assert row.status == "pending" and row.cleanup_pending


async def test_version_retry_is_idempotent_and_updates_are_explicit(pg_factory, job_app):
    artifacts, who, fence, first = await staged(pg_factory, job_app)
    async with pg_factory.begin() as db:
        retry = await artifacts.stage_artifact(
            db, fence, "测试文件", "text/plain", digest(b"test-only")
        )
        assert retry.id == first.id and retry.version == 1
        updated = await artifacts.stage_artifact(
            db,
            fence,
            "测试文件",
            "text/plain",
            digest(b"new-test-only"),
            artifact_id=first.artifact_id,
        )
        assert updated.artifact_id == first.artifact_id and updated.version == 2
        assert updated.object_key != first.object_key
    async with pg_factory() as db:
        assert len((await db.scalars(select(ArtifactVersion))).all()) == 2


async def test_foreign_object_is_forbidden_and_digest_cannot_be_changed(pg_factory, job_app):
    artifacts, who, fence, first = await staged(pg_factory, job_app)
    stranger, _ = await visitor(pg_factory)
    with pytest.raises(ServiceError) as forbidden:
        await artifacts.read_artifact(stranger, first.artifact_id, first.version)
    assert forbidden.value.http_status == 403
    with pytest.raises(ServiceError):
        await artifacts.write_artifact_object(first.id, b"wrong-bytes")
    async with pg_factory() as db:
        assert (await db.get(ArtifactVersion, first.id)).status == "pending"


async def test_worker_rejects_foreign_artifact_references(
    pg_factory, job_app, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.contracts import AgentOutcome
    from xiaohe.graph import build_graph
    from xiaohe.models import Run

    _, _, _, foreign = await staged(pg_factory, job_app)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "other")

    async def handler(state):
        return AgentOutcome(
            parts=[{"type": "text", "text": "不应提交"}],
            wait_for_input=False,
            artifact_version_ids=[foreign.id],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "failed"


async def test_database_rejects_ready_without_verified_digest(pg_factory, job_app):
    from sqlalchemy import func, update
    from sqlalchemy.exc import IntegrityError

    _, _, _, version = await staged(pg_factory, job_app)
    with pytest.raises(IntegrityError):
        async with pg_factory.begin() as db:
            await db.execute(
                update(ArtifactVersion)
                .where(ArtifactVersion.id == version.id)
                .values(
                    uploaded_at=func.clock_timestamp(),
                    bytes_size=9,
                    cleanup_pending=False,
                    status="ready",
                    verified_digest=None,
                )
            )
