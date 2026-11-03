from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from test_admission import send, visitor

from xiaohe.contracts import ServiceError
from xiaohe.conversations import load_snapshot
from xiaohe.events import append_event, persist_reply_chunk, stream_events
from xiaohe.models import Conversation, Event, Message, Session
from xiaohe.runs import acquire_run, reply_id

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_snapshot_cursor_has_no_gap_and_event_is_next_cursor(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    async with pg_factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
        fence = await acquire_run(db, UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        event = await append_event(
            db, fence, "run.updated", {"run": {"id": accepted.run.id, "status": "running"}}
        )
    stream = await stream_events(pg_factory, who, conversation, snapshot.cursor, None)
    first = await anext(stream)
    assert first.startswith(b"id: ") and b"event: run.updated" in first
    await stream.aclose()
    resumed = await stream_events(pg_factory, who, conversation, event.event_id, None)
    async with pg_factory.begin() as db:
        newer = await append_event(
            db, fence, "run.updated", {"run": {"id": accepted.run.id, "status": "running"}}
        )
    assert newer.event_id.encode() in await anext(resumed)
    await resumed.aclose()


async def test_replay_upserts_same_message_and_chunks_are_idempotent(pg_factory, job_app):
    from xiaohe.models import Command

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
        command = await db.scalar(select(Command).where(Command.run_id == fence.run_id))
        message_id = reply_id(fence.run_id, command.id)
    async with pg_factory.begin() as db:
        first = await persist_reply_chunk(db, fence, message_id, "1", "你好 😀")
    async with pg_factory.begin() as db:
        retry = await persist_reply_chunk(db, fence, message_id, "1", "你好 😀")
        assert first == retry
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError):
            await persist_reply_chunk(db, fence, message_id, "1", "不同内容")
    async with pg_factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
        assistant = [m for m in snapshot.messages if m.role == "assistant"]
        assert len(assistant) == 1 and assistant[0].id == str(message_id)
        assert assistant[0].parts[0].text == "你好 😀"
    stream = await stream_events(pg_factory, who, conversation, None, None)
    emitted = [await anext(stream) for _ in range(6)]
    await stream.aclose()
    assert sum(b"event: message.delta" in item for item in emitted) == 1
    assert (
        sum(
            b"event: message.upsert" in item and str(message_id).encode() in item
            for item in emitted
        )
        == 1
    )
    card = {"type": "suggestions", "items": [{"id": "next", "text": "了解服务"}]}
    async with pg_factory.begin() as db:
        message = await db.get(Message, message_id)
        message.parts = [*message.parts, card]
        await db.flush()
        await persist_reply_chunk(db, fence, message_id, "2", " 继续")
    async with pg_factory() as db:
        parts = (await db.get(Message, message_id)).parts
        assert parts == [{"type": "text", "text": "你好 😀 继续"}, card]


async def test_foreign_cursor_rejected_and_old_epoch_stream_closes(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    other, foreign = await visitor(pg_factory)
    await send(pg_factory, other, foreign, "foreign")
    async with pg_factory.begin() as db:
        foreign_snapshot = await load_snapshot(db, other, foreign)
    with pytest.raises(ServiceError) as error:
        await stream_events(pg_factory, who, conversation, foreign_snapshot.cursor, None)
    assert error.value.code == "EVENT_CURSOR_INVALID"
    stream = await stream_events(pg_factory, who, conversation, None, None)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Conversation).where(Conversation.id == conversation).values(ownership_epoch=1)
        )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_expiry_closes_stream_with_persisted_expired_event(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    stream = await stream_events(pg_factory, who, conversation, None, None)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp())
        )
    value = await anext(stream)
    assert b"event: viewer.expired" in value and b"VISITOR_EXPIRED" in value
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    async with pg_factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "viewer.expired")
            )
            == 1
        )


async def test_retired_cursor_requires_snapshot(pg_factory, job_app):
    from xiaohe.models import EventTombstone

    who, conversation = await visitor(pg_factory)
    await send(pg_factory, who, conversation, "initial")
    async with pg_factory.begin() as db:
        first = await db.scalar(
            select(Event)
            .where(Event.conversation_id == conversation)
            .order_by(Event.sequence)
            .limit(1)
        )
        cursor = str(first.id)
        db.add(
            EventTombstone(
                id=first.id,
                conversation_id=conversation,
                system_id=first.system_id,
                tenant_id=first.tenant_id,
                sequence=first.sequence,
            )
        )
        await db.delete(first)
    with pytest.raises(ServiceError) as error:
        await stream_events(pg_factory, who, conversation, cursor, None)
    assert error.value.code == "EVENT_CURSOR_EXPIRED" and error.value.http_status == 410
    async with pg_factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
    stream = await stream_events(pg_factory, who, conversation, snapshot.cursor, None)
    await stream.aclose()


async def test_worker_uses_persistent_chunk_receipts(pg_factory, runtime_env, monkeypatch):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from agents.agents import agents
    from xiaohe.contracts import AgentOutcome
    from xiaohe.graph import build_graph
    from xiaohe.models import RunAttempt

    fake = GenericFakeChatModel(messages=iter([AIMessage(content="公开答复")]))

    async def handler(state):
        await fake.ainvoke("public response", config={"tags": ["xiaohe_public"]})
        return AgentOutcome(
            parts=[{"type": "text", "text": "公开答复"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory.begin() as db:
        attempt = await db.get(RunAttempt, (UUID(accepted.run.id), 1))
        assert len(attempt.chunk_receipts) > 0
        message = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation, Message.role == "assistant"
            )
        )
        assert message.parts == [{"type": "text", "text": "公开答复"}]
