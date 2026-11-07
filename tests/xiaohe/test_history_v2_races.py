import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from test_history_v2 import STAMP, latest, older, seed

from xiaohe.contracts import ServiceError
from xiaohe.events import record_event
from xiaohe.history import load_message_baseline
from xiaohe.models import Conversation, Message, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_append_with_same_timestamp_cannot_enter_frozen_older_page(pg_factory):
    who, conversation = await seed(pg_factory)
    first = await latest(pg_factory, who, conversation)
    async with pg_factory.begin() as db:
        current = await db.get(Conversation, conversation, with_for_update=True)
        for key in (0, 100):
            row = Message(
                id=UUID(int=key),
                conversation_id=conversation,
                system_id="portal",
                tenant_id=1,
                role="assistant",
                created_at=STAMP,
                membership_sequence=current.event_sequence + 1,
                parts=[{"type": "text", "text": "新加入"}],
            )
            db.add(row)
            record_event(
                db,
                current,
                "message.upsert",
                {
                    "message": {
                        "id": str(row.id),
                        "role": row.role,
                        "createdAt": STAMP.isoformat(),
                        "parts": row.parts,
                    }
                },
            )
    page = await older(pg_factory, who, conversation, first.history.older_cursor)
    assert [m.id for m in page.messages] == [str(UUID(int=i)) for i in range(16, 26)]
    assert page.watermark_sequence == first.sync.watermark_sequence + 2
    assert page.history.history_epoch == first.history.history_epoch


async def test_late_page_baseline_is_not_global_event_progress(pg_factory):
    who, conversation = await seed(pg_factory)
    first = await latest(pg_factory, who, conversation)
    page = await older(pg_factory, who, conversation, first.history.older_cursor)
    async with pg_factory.begin() as db:
        current = await db.get(Conversation, conversation, with_for_update=True)
        row = await db.get(Message, UUID(int=20))
        row.parts = [{"type": "text", "text": "消息20新正文"}]
        session = await db.get(Session, who.session_id)
        run = Run(
            id=uuid4(),
            conversation_id=conversation,
            system_id="portal",
            tenant_id=1,
            session_id=who.session_id,
            guest_id=who.guest_id,
            execution_expires_at=session.expires_at,
            initial_message_id=row.id,
            status="completed",
            holds_slot=False,
        )
        db.add(run)
        await db.flush()
        record_event(
            db, current, "message.delta", {"messageId": str(row.id), "delta": "新正文"}, run.id
        )
    async with pg_factory.begin() as db:
        baseline = await load_message_baseline(db, who, conversation, UUID(int=20))
    assert baseline.watermark_sequence > page.watermark_sequence
    assert baseline.message.parts[0].text == "消息20新正文"
    cached = {baseline.message.id: (baseline.watermark_sequence, baseline.message.parts[0].text)}
    for message in page.messages:
        if page.watermark_sequence >= cached.get(message.id, (-1, ""))[0]:
            cached[message.id] = (page.watermark_sequence, message.parts[0].text)
    assert cached[baseline.message.id][1] == "消息20新正文"
    assert first.sync.watermark_sequence == 35  # Page/baseline is not a global SSE acknowledgement.


async def test_sequence_overflow_fails_before_persisting_event(pg_factory):
    who, conversation = await seed(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation)
            .values(event_sequence=2**53 - 1)
        )
    async with pg_factory.begin() as db:
        current = await db.get(Conversation, conversation, with_for_update=True)
        with pytest.raises(ServiceError):
            record_event(db, current, "viewer.expired", {"code": "VISITOR_EXPIRED"})
        assert current.event_sequence == 2**53 - 1


async def test_epoch_overflow_rejects_merge_without_moving_content(pg_factory, job_app):
    from test_guest_claim import prepare
    from test_http_api import client_for, session
    from test_identity_sessions import bootstrap, login

    _, token = await login(pg_factory)
    old, _, _ = await bootstrap(pg_factory, token)
    target = UUID(old.conversation_id)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Conversation).where(Conversation.id == target).values(history_epoch=2**53 - 1)
        )
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        operation, _ = await prepare(client, app)
        result = await client.post(f"/ai-api/v1/auth/operations/{operation}/complete", json={})
        assert result.status_code == 503
        async with pg_factory() as db:
            row = await db.get(Message, UUID(guest["messages"][0]["id"]))
            assert str(row.conversation_id) == guest["conversationId"]


async def test_page_body_and_watermark_follow_the_same_conversation_lock(pg_factory):
    who, conversation = await seed(pg_factory)
    initial = await latest(pg_factory, who, conversation)
    async with pg_factory.begin() as writer:
        current = await writer.get(Conversation, conversation, with_for_update=True)
        reading = asyncio.create_task(
            older(pg_factory, who, conversation, initial.history.older_cursor)
        )
        async with pg_factory() as observer:

            async def waiting():
                while not await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event_type='Lock' AND query LIKE '%conversations%'"
                    )
                ):
                    await observer.rollback()
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(waiting(), 5)
        row = await writer.get(Message, UUID(int=20))
        row.parts = [{"type": "text", "text": "锁后新正文"}]
        record_event(
            writer,
            current,
            "message.upsert",
            {
                "message": {
                    "id": str(row.id),
                    "role": row.role,
                    "createdAt": STAMP.isoformat(),
                    "parts": row.parts,
                }
            },
        )
    page = await asyncio.wait_for(reading, 5)
    assert next(m for m in page.messages if m.id == str(UUID(int=20))).parts[0].text == "锁后新正文"
    assert page.watermark_sequence == initial.sync.watermark_sequence + 1


async def test_all_message_writers_keep_the_first_membership_sequence(pg_factory, runtime_env):
    from test_admission import send
    from test_guest_budget import open_chat
    from test_identity_sessions import actor

    from xiaohe.admission import _accept
    from xiaohe.contracts import AgentOutcome, ToolkitOutcome
    from xiaohe.events import persist_reply_chunk
    from xiaohe.models import Command, Event
    from xiaohe.runs import acquire_run, reply_id

    first, cookies, _ = await open_chat(pg_factory)
    who = await actor(pg_factory, cookies["__Host-XIAOHESESSION"].value)
    conversation = UUID(first.conversation_id)
    ordinary = await send(pg_factory, who, conversation, "ordinary")
    await runtime_env.worker.execute_run(UUID(ordinary.run.id))
    streaming = await send(pg_factory, who, conversation, "streaming")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(streaming.run.id))
        command = await db.scalar(select(Command).where(Command.run_id == fence.run_id))
        await persist_reply_chunk(db, fence, reply_id(fence.run_id, command.id), "chunk-1", "开头")
    await runtime_env.worker._save(
        fence,
        command.id,
        ToolkitOutcome(
            kind="outcome",
            outcome=AgentOutcome(
                parts=[{"type": "text", "text": "完整回复"}],
                wait_for_input=False,
                artifact_version_ids=[],
            ),
        ),
    )
    async with pg_factory.begin() as db:
        await _accept(
            db,
            who,
            conversation,
            "message",
            "control",
            {"receptionControl": "collect"},
            system_notice="采集信息",
        )
    async with pg_factory() as db:
        first_sequence = {}
        for event in await db.scalars(
            select(Event).where(Event.event_type == "message.upsert").order_by(Event.sequence)
        ):
            first_sequence.setdefault(event.payload["message"]["id"], event.sequence)
        rows = list(await db.scalars(select(Message)))
        assert len(rows) == 6
        assert all(row.membership_sequence == first_sequence[str(row.id)] > 0 for row in rows)
