import asyncio
import hashlib
import time
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import ChatMessage, EventEnvelope, ServiceError
from xiaohe.contracts_v2 import EventEnvelopeV2
from xiaohe.models import Command, Conversation, Event, EventTombstone, Message, RunAttempt, Session

_EVENT = TypeAdapter(EventEnvelope)
_EVENT_V2 = TypeAdapter(EventEnvelopeV2)


def next_position(value: int) -> int:
    if type(value) is not int or not 0 <= value < 2**53 - 1:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    return value + 1


def record_event(
    db: AsyncSession,
    conversation: Conversation,
    event_type: str,
    payload: dict,
    run_id: UUID | None = None,
) -> Event:
    event_id = uuid4()
    _EVENT.validate_python(
        {
            "eventId": str(event_id),
            "conversationId": str(conversation.id),
            "runId": str(run_id) if run_id else None,
            "type": event_type,
            "payload": payload,
        }
    )
    conversation.event_sequence = next_position(conversation.event_sequence)
    event = Event(
        id=event_id,
        conversation_id=conversation.id,
        system_id=conversation.system_id,
        tenant_id=conversation.tenant_id,
        sequence=conversation.event_sequence,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(event)
    return event


def envelope(event: Event, *, view_version=1, history_epoch=None):
    value = {
        "eventId": str(event.id),
        "conversationId": str(event.conversation_id),
        "runId": str(event.run_id) if event.run_id else None,
        "type": event.event_type,
        "payload": event.payload,
    }
    if view_version == 2:
        return _EVENT_V2.validate_python(
            value | {"sequence": event.sequence, "historyEpoch": history_epoch}
        )
    return _EVENT.validate_python(value)


async def append_event(db, fence, type: str, payload: dict):
    from xiaohe.runs import require_write

    run = await require_write(db, fence)
    conversation = await db.get(Conversation, run.conversation_id)
    event = record_event(db, conversation, type, payload, run.id)
    await db.flush()
    return envelope(event)


async def persist_reply_chunk(db, fence, message_id: UUID, chunk_key: str, text: str):
    from xiaohe.runs import reply_id, require_write

    run = await require_write(db, fence)
    command = await db.scalar(
        select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
    )
    if command is None or message_id != reply_id(run.id, command.id):
        raise ServiceError("FORBIDDEN", 403)
    if not chunk_key.strip() or len(chunk_key) > 128:
        raise ServiceError("INPUT_INVALID", 422)
    attempt = await db.get(RunAttempt, (run.id, run.attempt))
    digest = hashlib.sha256((str(message_id) + ":" + text).encode()).hexdigest()
    receipt = attempt.chunk_receipts.get(chunk_key)
    if receipt is not None:
        if receipt["digest"] != digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
        event = await db.get(Event, UUID(receipt["event_id"]))
        if event is None:
            raise ServiceError("EVENT_CURSOR_EXPIRED", 410)
        return envelope(event)
    conversation = await db.get(Conversation, run.conversation_id)
    message = await db.get(Message, message_id)
    if message is None:
        message = Message(
            id=message_id,
            conversation_id=run.conversation_id,
            system_id=run.system_id,
            tenant_id=run.tenant_id,
            role="assistant",
            parts=[],
            membership_sequence=next_position(conversation.event_sequence),
        )
        db.add(message)
        await db.flush()
        record_event(
            db,
            conversation,
            "message.upsert",
            {
                "message": ChatMessage(
                    id=str(message.id), role="assistant", created_at=message.created_at, parts=[]
                ).model_dump(mode="json", by_alias=True)
            },
            run.id,
        )
    parts = [dict(part) for part in message.parts]
    existing = next((part for part in reversed(parts) if part["type"] == "text"), None)
    if existing is None:
        parts.append({"type": "text", "text": text})
    else:
        existing["text"] += text
    message.parts = parts
    event = record_event(
        db, conversation, "message.delta", {"messageId": str(message.id), "delta": text}, run.id
    )
    attempt.chunk_receipts = {
        **attempt.chunk_receipts,
        chunk_key: {"digest": digest, "event_id": str(event.id)},
    }
    await db.flush()
    await require_write(db, fence)
    return envelope(event)


async def _position(db, actor, conversation_id, after, last_event_id):
    from xiaohe.conversations import require_conversation

    conversation = await require_conversation(db, actor, conversation_id)
    if after is not None and last_event_id is not None and after != last_event_id:
        raise ServiceError("EVENT_CURSOR_INVALID", 409)
    cursor = after if after is not None else last_event_id
    sequence = 0
    if cursor is not None:
        key = None
        try:
            key = UUID(cursor)
        except (ValueError, TypeError, AttributeError):
            pass
        if key is None:
            raise ServiceError("EVENT_CURSOR_INVALID", 409)
        event = await db.scalar(
            select(Event).where(Event.id == key, Event.conversation_id == conversation.id)
        )
        if event is None:
            retired = await db.scalar(
                select(EventTombstone.id).where(
                    EventTombstone.id == key, EventTombstone.conversation_id == conversation.id
                )
            )
            raise ServiceError(
                "EVENT_CURSOR_EXPIRED" if retired else "EVENT_CURSOR_INVALID",
                410 if retired else 409,
            )
        sequence = event.sequence
        if sequence > conversation.event_sequence:
            raise ServiceError("EVENT_CURSOR_INVALID", 409)
    if await db.scalar(
        select(EventTombstone.id)
        .where(
            EventTombstone.conversation_id == conversation.id, EventTombstone.sequence > sequence
        )
        .limit(1)
    ):
        raise ServiceError("EVENT_CURSOR_EXPIRED", 410)
    return sequence, conversation.ownership_epoch


def _frame(event, *, view_version=1, history_epoch=None):
    value = envelope(event, view_version=view_version, history_epoch=history_epoch)
    return (
        f"id: {value.event_id}\nevent: {value.type}\ndata: {value.model_dump_json(by_alias=True)}\n\n"
    ).encode()


async def _expiry_event(
    factory, actor, conversation_id, epoch, *, view_version=1, history_epoch=None
):
    from xiaohe.identity import registered_system

    async with factory.begin() as db:
        await registered_system(db, actor.system)
        session = await db.scalar(
            select(Session).where(Session.id == actor.session_id).with_for_update()
        )
        if (
            session is None
            or session.revoked_at is not None
            or session.guest_id != actor.guest_id
            or actor.guest_id is None
        ):
            return None
        if (
            session.system_id != actor.system.system_id
            or session.tenant_id != actor.system.tenant_id
        ):
            return None
        if session.expires_at > await db.scalar(select(func.clock_timestamp())):
            return None
        conversation = await db.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.guest_id == actor.guest_id,
                Conversation.system_id == actor.system.system_id,
                Conversation.tenant_id == actor.system.tenant_id,
            )
            .with_for_update()
        )
        if conversation is None or conversation.ownership_epoch != epoch:
            return None
        if view_version == 2 and conversation.history_epoch != history_epoch:
            return None
        event = await db.scalar(
            select(Event).where(
                Event.conversation_id == conversation.id, Event.event_type == "viewer.expired"
            )
        )
        if event is None:
            event = record_event(db, conversation, "viewer.expired", {"code": "VISITOR_EXPIRED"})
            await db.flush()
        return _frame(event, view_version=view_version, history_epoch=history_epoch)


async def stream_events(
    factory,
    actor,
    conversation_id,
    after=None,
    last_event_id=None,
    *,
    view_version=1,
    history_epoch=None,
):
    from xiaohe.conversations import require_conversation

    async with factory.begin() as db:
        sequence, epoch = await _position(db, actor, conversation_id, after, last_event_id)
        if view_version == 2:
            if type(history_epoch) is not int or not 0 <= history_epoch <= 2**53 - 1:
                raise ServiceError("INPUT_INVALID", 422)
            conversation = await db.get(Conversation, conversation_id)
            if conversation.history_epoch != history_epoch:
                raise ServiceError("HISTORY_RESET_REQUIRED", 409)

    async def poll():
        nonlocal sequence
        heartbeat = time.monotonic()
        while True:
            try:
                async with factory.begin() as db:
                    conversation = await require_conversation(db, actor, conversation_id)
                    if conversation.ownership_epoch != epoch:
                        return
                    if view_version == 2 and conversation.history_epoch != history_epoch:
                        return
                    rows = (
                        await db.scalars(
                            select(Event)
                            .where(
                                Event.conversation_id == conversation_id, Event.sequence > sequence
                            )
                            .order_by(Event.sequence)
                            .limit(100)
                        )
                    ).all()
                for row in rows:
                    async with factory.begin() as db:
                        current = await require_conversation(db, actor, conversation_id)
                        if current.ownership_epoch != epoch:
                            return
                        if view_version == 2 and current.history_epoch != history_epoch:
                            return
                    if row.sequence != sequence + 1:
                        return
                    yield _frame(row, view_version=view_version, history_epoch=history_epoch)
                    sequence = row.sequence
            except ServiceError as error:
                if error.code == "VISITOR_EXPIRED":
                    expired = await _expiry_event(
                        factory,
                        actor,
                        conversation_id,
                        epoch,
                        view_version=view_version,
                        history_epoch=history_epoch,
                    )
                    if expired is not None:
                        yield expired
                return
            if not rows:
                if time.monotonic() - heartbeat >= 15:
                    yield b": keepalive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(1)

    return poll()
