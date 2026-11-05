import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import send_in_db

from xiaohe.conversations import require_conversation
from xiaohe.identity import require_session
from xiaohe.runs import acquire_run, require_write


@pytest.mark.pg
@pytest.mark.asyncio
async def test_user_snapshot_and_worker_fence_use_same_session_conversation_order(
    pg_factory, job_app
):
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who = await actor(pg_factory, token)
    conversation = UUID(initial.conversation_id)
    async with pg_factory.begin() as db:
        accepted = await send_in_db(db, who, conversation, "采购商")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as reader:
        await require_session(reader, who)
        started = asyncio.Future()

        async def write():
            async with pg_factory.begin() as db:
                started.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
                await require_write(db, fence)
                await require_write(db, fence)

        task = asyncio.create_task(write())
        pid = await started
        try:
            async with asyncio.timeout(3):
                while True:
                    async with pg_factory() as observer:
                        blocked = await observer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid))>0"), {"pid": pid}
                        )
                    if blocked or task.done():
                        break
                    await asyncio.sleep(0.01)
            await require_conversation(reader, who, conversation)
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    await task
