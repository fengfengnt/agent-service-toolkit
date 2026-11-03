import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from langchain_core.messages import messages_to_dict
from sqlalchemy import func, select, text
from test_admission import send, visitor
from test_toolkit_execution import frame

from xiaohe import api
from xiaohe.artifacts import ArtifactService
from xiaohe.contracts import ServiceError
from xiaohe.jobs import build_job_app, recover_stalled_jobs
from xiaohe.models import Command, Event, Message, Run
from xiaohe.runs import acquire_run
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "value",
    [
        "access_token=fixture-secret",
        "Authorization: Bearer fixture-secret",
        "browserVerifier=fixture-secret",
        '{"access_token":"fixture-secret"}',
        "Cookie: fixture-secret",
        "client_secret=fixture-secret",
    ],
)
async def test_credential_text_is_rejected_before_any_persistent_write(pg_factory, job_app, value):
    who, conversation = await visitor(pg_factory)
    with pytest.raises(ServiceError) as error:
        await send(pg_factory, who, conversation, "secret", value)
    assert error.value.code == "SENSITIVE_INPUT_REQUIRES_CARD"
    async with pg_factory() as db:
        for model in (Message, Command, Run, Event):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_legacy_credential_text_never_enters_checkpoint(pg_factory, runtime_env):
    who, conversation = await visitor(pg_factory)
    legacy = "access_token=legacy-fixture-secret"
    async with pg_factory.begin() as db:
        db.add(
            Message(
                conversation_id=conversation,
                system_id="portal",
                tenant_id=1,
                role="user",
                parts=[{"type": "text", "text": legacy}],
            )
        )
    command = await frame(pg_factory, who, conversation)
    _ = [event async for event in runtime_env.client.stream_execution(command)]
    state = await runtime_env.graph.aget_state({"configurable": {"thread_id": command.thread_id}})
    serialized = json.dumps(messages_to_dict(state.values["messages"]), ensure_ascii=False)
    assert "legacy-fixture-secret" not in serialized


async def test_recovered_job_waits_for_live_run_guard_before_ack(
    pg_factory, pg_url, job_app, runtime_env
):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    run_id = UUID(accepted.run.id)
    old_worker = await job_app.job_manager.register_worker()
    job = await job_app.job_manager.fetch_job(queues=["xiaohe"], worker_id=old_worker)
    engine = pg_factory.kw["bind"]
    async with engine.connect() as held:
        transaction = await held.begin()
        await held.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "xiaohe.run:" + str(run_id)},
        )
        async with pg_factory.begin() as db:
            await acquire_run(db, run_id)
        assert await recover_stalled_jobs(job_app, seconds_since_heartbeat=0) == 1
        async with pg_factory.begin() as db:
            await db.execute(
                text("UPDATE procrastinate_jobs SET scheduled_at=clock_timestamp() WHERE id=:id"),
                {"id": job.id},
            )
        app = build_job_app(
            pg_url,
            runtime_env.worker.execute_run,
            on_exhausted=runtime_env.worker.finalize_lost_run,
        )
        async with app.open_async():
            waiter = asyncio.create_task(
                app.run_worker_async(
                    queues=["xiaohe"],
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                )
            )
            try:

                async def status():
                    async with pg_factory() as db:
                        return await db.scalar(
                            text("SELECT status FROM procrastinate_jobs WHERE id=:id"),
                            {"id": job.id},
                        )

                for _ in range(100):
                    if await status() in ("doing", "succeeded"):
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.2)
                assert await status() == "doing" and not waiter.done()
                await transaction.commit()
                await asyncio.wait_for(waiter, 5)
                assert await status() == "succeeded"
                async with pg_factory() as db:
                    assert (await db.get(Run, run_id)).status == "completed"
                assert len(runtime_env.calls) == 1
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                if transaction.is_active:
                    await transaction.rollback()


async def test_guard_wait_stops_at_execution_deadline(pg_factory, job_app, runtime_env):
    from sqlalchemy import update

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    run_id = UUID(accepted.run.id)
    async with pg_factory.kw["bind"].connect() as held, held.begin():
        await held.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "xiaohe.run:" + str(run_id)},
        )
        async with pg_factory.begin() as db:
            await acquire_run(db, run_id)
            await db.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(execution_expires_at=func.clock_timestamp())
            )
        await asyncio.wait_for(runtime_env.worker.execute_run(run_id), 3)
    async with pg_factory() as db:
        assert (await db.get(Run, run_id)).status == "expired"
    assert runtime_env.calls == []


class Body:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


async def download_without_external_io(monkeypatch):
    @asynccontextmanager
    async def transaction():
        yield None

    service = ArtifactService(
        SimpleNamespace(begin=transaction),
        Settings(toolkit_base_url="http://unused", auth_secret="fixture-auth"),
    )
    body = Body()
    service._s3 = SimpleNamespace(get_object=lambda **kwargs: {"Body": body})

    async def read(*args):
        return SimpleNamespace(status="ready", object_key="unit-test-key")

    monkeypatch.setattr(service, "_read", read)
    return body, await service.download_artifact(None, None, 1)


async def test_unstarted_download_close_releases_body(monkeypatch):
    body, stream = await download_without_external_io(monkeypatch)
    await stream.aclose()
    assert body.closed


async def test_response_start_disconnect_closes_download(monkeypatch):
    from starlette.requests import ClientDisconnect

    body, stream = await download_without_external_io(monkeypatch)

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        raise OSError("test disconnect at headers")

    with pytest.raises(ClientDisconnect):
        await api.StreamingResponse(stream)(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
    assert body.closed


async def test_disconnect_after_yield_closes_iterator():
    from starlette.requests import ClientDisconnect

    stopped = asyncio.Event()

    async def stream():
        try:
            yield b"first chunk"
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("test disconnect at body")

    iterator = stream()
    try:
        with pytest.raises(ClientDisconnect):
            await api.StreamingResponse(iterator)(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert stopped.is_set()
    finally:
        await iterator.aclose()
