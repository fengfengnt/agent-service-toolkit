from uuid import UUID

import pytest
from sqlalchemy import func, update
from test_admission import send, visitor

from xiaohe.contracts import ServiceError
from xiaohe.models import Session
from xiaohe.runs import acquire_run, cancel_run, require_write

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_old_attempt_cannot_commit(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    async with pg_factory.begin() as db:
        first = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        assert await acquire_run(db, first.run_id) is None
        second = await acquire_run(db, first.run_id, recover=True)
    assert second.attempt == first.attempt + 1
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await require_write(db, first)
        assert exc.value.code == "WRITE_FENCE_STALE"
        assert (await require_write(db, second)).id == first.run_id


async def test_cancel_and_guest_expiry_revoke_writes(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        result = await cancel_run(db, who, fence.run_id)
        assert result.status == "cancelled"
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError):
            await require_write(db, fence)
    another = await send(pg_factory, who, conversation, "two")
    async with pg_factory.begin() as db:
        fresh = await acquire_run(db, UUID(another.run.id))
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp())
        )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await require_write(db, fresh)
        assert exc.value.code == "EXECUTION_EXPIRED"


async def test_execution_owner_and_epoch_are_rechecked(pg_factory, job_app):
    from xiaohe.models import Conversation

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        await db.execute(
            update(Conversation).where(Conversation.id == conversation).values(ownership_epoch=1)
        )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await require_write(db, fence)
        assert exc.value.code == "WRITE_FENCE_STALE"
