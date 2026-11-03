import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from test_identity_sessions import actor, bootstrap, login

from xiaohe import admission
from xiaohe.admission import accept_action, accept_message
from xiaohe.contracts import ServiceError
from xiaohe.conversations import load_snapshot
from xiaohe.models import Command, Conversation, Event, Message, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def visitor(factory):
    snapshot, token, _ = await bootstrap(factory)
    return await actor(factory, token), UUID(snapshot.conversation_id)


async def send(factory, who, conversation_id, key, value="了解采购服务"):
    async with factory.begin() as db:
        return await accept_message(db, who, conversation_id, key, value)


async def finish(factory, run_id):
    async with factory.begin() as db:
        await db.execute(
            update(Run)
            .where(Run.id == UUID(str(run_id)))
            .values(status="completed", holds_slot=False)
        )


async def test_100_accepted_101_rejected(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session).values(
                message_count=99, expires_at=func.clock_timestamp() + text("interval '60 seconds'")
            )
        )
    accepted = await send(pg_factory, who, conversation, "hundredth")
    assert accepted.viewer.message_count == 100
    async with pg_factory() as db:
        assert accepted.viewer.expires_at > await db.scalar(
            select(func.clock_timestamp())
        ) + timedelta(seconds=14390)
    await finish(pg_factory, accepted.run.id)
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "hundred-and-one")
    assert exc.value.code == "VISITOR_LIMIT_REACHED"
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
        assert (
            await db.scalar(
                text("SELECT count(*) FROM procrastinate_jobs WHERE task_name='xiaohe.execute_run'")
            )
            == 1
        )


async def test_rolling_deadline_and_retry(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    first = await send(pg_factory, who, conversation, "one", " 😀 原文 ")
    retry = await send(pg_factory, who, conversation, "one", " 😀 原文 ")
    assert first.message == retry.message and first.run.id == retry.run.id
    assert first.message.parts[0].text == " 😀 原文 "
    assert first.viewer == retry.viewer
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "one", "不同参数")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Event)) == 3
        assert await db.scalar(select(func.count()).select_from(Command)) == 1


async def test_parallel_last_slot(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(Session).values(message_count=99))
    results = await asyncio.gather(
        send(pg_factory, who, conversation, "left"),
        send(pg_factory, who, conversation, "right"),
        return_exceptions=True,
    )
    assert sum(not isinstance(x, BaseException) for x in results) == 1
    async with pg_factory() as db:
        assert await db.scalar(select(Session.message_count)) == 100
        assert await db.scalar(select(func.count()).select_from(Command)) == 1


async def test_parallel_retry_is_one_admission(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    results = await asyncio.gather(
        *(send(pg_factory, who, conversation, "same-key") for _ in range(3))
    )
    assert len({x.message.id for x in results}) == 1
    async with pg_factory() as db:
        assert await db.scalar(select(Session.message_count)) == 1
        assert await db.scalar(select(func.count()).select_from(Run)) == 1


async def test_action_resumes_same_run(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    original = await send(pg_factory, who, conversation, "one")
    question = {
        "type": "choice",
        "requestId": "Q1",
        "prompt": "请选择",
        "options": [{"id": "intro", "label": "服务介绍"}],
    }
    async with pg_factory.begin() as db:
        await db.execute(
            update(Run)
            .where(Run.id == UUID(original.run.id))
            .values(status="waiting_input", pending_request=question)
        )
    async with pg_factory.begin() as db:
        result = await accept_action(db, who, conversation, "answer", "Q1", "intro", None)
    assert result.run.id == original.run.id and result.run.status == "queued"
    assert result.viewer.message_count == 2 and result.message.parts[0].text == "服务介绍"
    async with pg_factory.begin() as db:
        retry = await accept_action(db, who, conversation, "answer", "Q1", "intro", None)
    assert retry.message.id == result.message.id and retry.viewer == result.viewer
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run)) == 1
        assert (
            await db.scalar(
                text("SELECT count(*) FROM procrastinate_jobs WHERE task_name='xiaohe.execute_run'")
            )
            == 2
        )


async def test_retry_returns_current_viewer_without_recharging(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    first = await send(pg_factory, who, conversation, "first")
    await finish(pg_factory, first.run.id)
    second = await send(pg_factory, who, conversation, "second")
    retry = await send(pg_factory, who, conversation, "first")
    assert retry.run.id == first.run.id and retry.run.status == "completed"
    assert retry.message.id == first.message.id
    assert retry.viewer.message_count == 2
    assert retry.viewer.expires_at == second.viewer.expires_at


async def test_busy_merge_and_stale_actions_do_not_charge(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    first = await send(pg_factory, who, conversation, "first")
    for mode in ("busy", "merge", "stale"):
        async with pg_factory.begin() as db:
            if mode == "merge":
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation)
                    .values(merge_pending=True)
                )
            if mode == "stale":
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation)
                    .values(merge_pending=False)
                )
                await db.execute(
                    update(Run).values(
                        status="waiting_input",
                        pending_request={
                            "type": "input_request",
                            "requestId": "other-question",
                            "prompt": "补充",
                        },
                    )
                )
        async with pg_factory.begin() as db:
            with pytest.raises(ServiceError) as exc:
                if mode == "busy":
                    await accept_message(db, who, conversation, "busy", "再发一条")
                else:
                    await accept_action(db, who, conversation, mode, "Q1", None, "补充信息")
            assert (
                exc.value.code
                == {"busy": "RUN_ACTIVE", "merge": "MERGE_PENDING", "stale": "ACTION_STALE"}[mode]
            )
    async with pg_factory() as db:
        stored = await db.get(Session, who.session_id)
        assert stored.message_count == 1 and stored.expires_at == first.viewer.expires_at


@pytest.mark.parametrize(
    "value",
    [
        "手机号13812345678",
        "验证码123456",
        "OTP:654321",
        "123456",
        "电话138-1234-5678",
        "手机１３８１２３４５６７８",
    ],
)
async def test_sensitive_input_is_not_persisted_or_enqueued(pg_factory, job_app, value):
    who, conversation = await visitor(pg_factory)
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "sensitive", value)
    assert exc.value.code == "SENSITIVE_INPUT_REQUIRES_CARD"
    assert value not in str(exc.value)
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(Session.message_count)) == 0


async def test_custom_limits_and_queue_failure_rollback(pg_factory, job_app, monkeypatch):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(Session).values(message_limit=2, retention_seconds=60))
    error = RuntimeError("fixture queue unavailable")
    with monkeypatch.context() as patcher:
        patcher.setattr(admission, "defer_run", AsyncMock(side_effect=error))
        async with pg_factory.begin() as db:
            with pytest.raises(RuntimeError):
                await accept_message(db, who, conversation, "failed", "了解服务")
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Command)) == 0
        assert await db.scalar(select(Session.message_count)) == 0
    first = await send(pg_factory, who, conversation, "a")
    await finish(pg_factory, first.run.id)
    await send(pg_factory, who, conversation, "b")
    assert (first.viewer.expires_at - first.message.created_at).total_seconds() < 61
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "c")
    assert exc.value.code == "VISITOR_LIMIT_REACHED"


async def test_current_permissions_and_expiry_precede_idempotent_reply(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    await send(pg_factory, who, conversation, "same")
    stranger, _ = await visitor(pg_factory)
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, stranger, conversation, "same")
    assert exc.value.code == "FORBIDDEN"
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "same")
    assert exc.value.code == "VISITOR_EXPIRED"


async def test_old_waiter_cannot_take_a_busy_slot(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    first = await send(pg_factory, who, conversation, "first")
    async with pg_factory.begin() as db:
        await db.execute(
            update(Run)
            .where(Run.id == UUID(first.run.id))
            .values(
                status="waiting_input",
                holds_slot=False,
                pending_request={"type": "input_request", "requestId": "Q1", "prompt": "请补充"},
            )
        )
    second = await send(pg_factory, who, conversation, "second")
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await accept_action(db, who, conversation, "answer", "Q1", None, "补充说明")
        assert exc.value.code == "RUN_ACTIVE"
    await finish(pg_factory, second.run.id)
    async with pg_factory.begin() as db:
        resumed = await accept_action(db, who, conversation, "answer", "Q1", None, "补充说明")
    assert resumed.run.id == first.run.id and resumed.viewer.message_count == 3


async def test_user_message_does_not_slide_session_or_add_guest_quota(pg_factory, job_app):
    row, token = await login(pg_factory)
    snapshot, _, _ = await bootstrap(pg_factory, token)
    who = await actor(pg_factory, token)
    result = await send(pg_factory, who, UUID(snapshot.conversation_id), "one")
    assert result.viewer.kind == "user"
    async with pg_factory() as db:
        session = await db.get(Session, row.id)
        assert session.expires_at == row.expires_at and session.message_count == 0


async def test_snapshot_watermark_and_immutable_events(pg_factory, job_app):
    from sqlalchemy.exc import IntegrityError

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    async with pg_factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
        last = await db.scalar(select(Event).order_by(Event.sequence.desc()))
        assert snapshot.cursor == str(last.id)
        assert snapshot.active_run.id == accepted.run.id
        assert [message.id for message in snapshot.messages] == [accepted.message.id]
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(update(Event).values(payload={"forged": True}))


async def test_expiry_at_final_admission_rolls_back_enqueued_job(pg_factory, job_app, monkeypatch):
    who, conversation = await visitor(pg_factory)
    original = admission.defer_run

    async def expires_after_enqueue(db, run_id, lock_key):
        job = await original(db, run_id, lock_key)
        deadline = await db.scalar(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp() + text("interval '0.05 second'"))
            .returning(Session.expires_at)
            .execution_options(synchronize_session=False)
        )
        while await db.scalar(select(func.clock_timestamp())) < deadline:
            await asyncio.sleep(0.005)
        return job

    monkeypatch.setattr(admission, "defer_run", expires_after_enqueue)
    with pytest.raises(ServiceError) as exc:
        await send(pg_factory, who, conversation, "one")
    assert exc.value.code == "VISITOR_EXPIRED"
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(func.count()).select_from(Event)) == 0
        assert (
            await db.scalar(
                text("SELECT count(*) FROM procrastinate_jobs WHERE task_name='xiaohe.execute_run'")
            )
            == 0
        )


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_executing_run_cannot_release_write_slot(pg_factory, job_app, status):
    from sqlalchemy.exc import IntegrityError

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "one")
    async with pg_factory.begin() as db:
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    update(Run)
                    .where(Run.id == UUID(accepted.run.id))
                    .values(status=status, holds_slot=False)
                )
