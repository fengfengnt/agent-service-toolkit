from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import event, update
from test_admission import visitor
from test_identity_sessions import actor, bootstrap, login

from xiaohe.contracts import ServiceError
from xiaohe.events import record_event
from xiaohe.history import load_history_page, load_message_baseline, load_snapshot_v2
from xiaohe.models import Command, Conversation, Message, Run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
SECRET = SecretStr("fixture-history-key")
STAMP = datetime(2026, 11, 7, tzinfo=UTC)


async def seed(factory, count=35, who=None, conversation_id=None):
    if who is None:
        who, conversation_id = await visitor(factory)
    async with factory.begin() as db:
        conversation = await db.get(Conversation, conversation_id, with_for_update=True)
        for index in range(1, count + 1):
            message = Message(
                id=UUID(int=index),
                conversation_id=conversation.id,
                system_id=conversation.system_id,
                tenant_id=conversation.tenant_id,
                role="assistant",
                created_at=STAMP,
                membership_sequence=conversation.event_sequence + 1,
                parts=[
                    {"type": "text", "text": f"消息{index}"},
                    {
                        "type": "artifact_ref",
                        "artifactId": "fixture-file",
                        "version": 7,
                        "title": "精确版本",
                        "mediaType": "text/plain",
                        "status": "ready",
                    },
                ],
            )
            db.add(message)
            record_event(
                db,
                conversation,
                "message.upsert",
                {
                    "message": {
                        "id": str(message.id),
                        "role": message.role,
                        "createdAt": STAMP.isoformat(),
                        "parts": message.parts,
                    }
                },
            )
    return who, conversation_id


async def latest(factory, who, conversation):
    async with factory.begin() as db:
        return await load_snapshot_v2(db, who, conversation, cursor_secret=SECRET)


async def older(factory, who, conversation, cursor):
    async with factory.begin() as db:
        return await load_history_page(db, who, conversation, cursor, cursor_secret=SECRET)


async def test_35_messages_page_10_10_10_5(pg_factory):
    who, conversation = await seed(pg_factory)
    queries = []

    def observe(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith("SELECT") and "FROM xiaohe.messages" in statement:
            queries.append((statement, parameters))

    engine = pg_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", observe)
    try:
        initial = await latest(pg_factory, who, conversation)
        pages = [initial.messages]
        history = initial.history
        while history.has_more:
            page = await older(pg_factory, who, conversation, history.older_cursor)
            pages.insert(0, page.messages)
            history = page.history
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert [len(page) for page in reversed(pages)] == [10, 10, 10, 5]
    messages = [message for page in pages for message in page]
    assert [message.id for message in messages] == [str(UUID(int=i)) for i in range(1, 36)]
    assert all(message.parts[1].version == 7 for message in messages)
    assert history.older_cursor is None
    assert len(queries) == 4 and all(
        "LIMIT" in sql and 11 in values.values() for sql, values in queries
    )
    assert initial.sync.watermark_sequence == 35 and initial.sync.event_cursor is not None


@pytest.mark.parametrize("cursor", ["", "x" * 2049, "fixture-event-uuid", "a.b", "e30.AAAA"])
async def test_malformed_or_event_cursor_is_rejected_without_echo(pg_factory, cursor):
    who, conversation = await seed(pg_factory)
    with pytest.raises(ServiceError) as error:
        await older(pg_factory, who, conversation, cursor)
    assert error.value.code == "HISTORY_CURSOR_INVALID"
    assert str(error.value) == "HISTORY_CURSOR_INVALID"


async def test_cursor_cannot_cross_owner_system_or_episode(pg_factory):
    from test_identity_sessions import system

    from xiaohe.conversations import default_conversation
    from xiaohe.identity import _actor, _new_session

    who, conversation = await seed(pg_factory)
    initial = await latest(pg_factory, who, conversation)
    other, other_conversation = await visitor(pg_factory)
    with pytest.raises(ServiceError) as error:
        await older(pg_factory, other, other_conversation, initial.history.older_cursor)
    assert error.value.code == "FORBIDDEN"
    async with pg_factory.begin() as db:
        record, _ = await _new_session(db, system("other"))
        other_system = _actor(record, system("other"))
        other_target = await default_conversation(db, other_system)
    with pytest.raises(ServiceError) as error:
        await older(pg_factory, other_system, other_target.id, initial.history.older_cursor)
    assert error.value.code == "FORBIDDEN"
    with pytest.raises(ServiceError):
        await older(pg_factory, other, conversation, initial.history.older_cursor)
    with pytest.raises(ServiceError) as error:
        await older(pg_factory, who, conversation, initial.history.older_cursor[:-1] + "!")
    assert error.value.code == "HISTORY_CURSOR_INVALID"
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError):
            await load_history_page(
                db,
                who,
                conversation,
                initial.history.older_cursor,
                cursor_secret=SecretStr("rotated-key"),
            )


async def test_active_input_locates_old_card_without_eleventh_initial_message(pg_factory, job_app):
    from test_admission import send

    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "question")
    async with pg_factory.begin() as db:
        await db.execute(update(Command).values(completed_at=STAMP))
        run = await db.get(Run, UUID(accepted.run.id))
        run.status = "waiting_input"
        run.pending_request = {
            "type": "choice",
            "requestId": "Q1",
            "prompt": "请选择",
            "options": [{"id": "one", "label": "服务介绍"}],
        }
    # The fixture's first assistant card predates the latest ten messages.
    await seed(pg_factory, count=20, who=who, conversation_id=conversation)
    async with pg_factory.begin() as db:
        row = await db.get(Message, UUID(int=1))
        row.parts = [run.pending_request]
        current = await db.get(Conversation, conversation, with_for_update=True)
        record_event(
            db,
            current,
            "message.upsert",
            {
                "message": {
                    "id": str(row.id),
                    "role": "assistant",
                    "createdAt": STAMP.isoformat(),
                    "parts": row.parts,
                }
            },
            UUID(accepted.run.id),
        )
    value = await latest(pg_factory, who, conversation)
    assert len(value.messages) == 10 and str(UUID(int=1)) not in [m.id for m in value.messages]
    assert value.active_input.message_id == str(UUID(int=1))
    async with pg_factory.begin() as db:
        result = await load_message_baseline(db, who, conversation, UUID(int=1))
        assert result.message.parts[0].request_id == "Q1"
        with pytest.raises(ServiceError) as error:
            await load_message_baseline(db, who, conversation, UUID(int=9999))
        assert error.value.code == "MESSAGE_NOT_FOUND"


async def test_legacy_zero_membership_and_empty_event_stream_are_readable(pg_factory):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        db.add(
            Message(
                id=UUID(int=1),
                conversation_id=conversation,
                system_id="portal",
                tenant_id=1,
                role="assistant",
                parts=[{"type": "system_notice", "text": "历史通知"}],
            )
        )
    value = await latest(pg_factory, who, conversation)
    assert len(value.messages) == 1
    assert value.sync.watermark_sequence == 0 and value.sync.event_cursor is None


async def test_merge_invalidates_target_history_not_ordinary_append(pg_factory, job_app):
    from test_guest_claim import prepare
    from test_http_api import client_for, session

    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    original, _, _ = await bootstrap(pg_factory, token)
    conversation = UUID(original.conversation_id)
    await seed(pg_factory, who=member, conversation_id=conversation)
    initial = await latest(pg_factory, member, conversation)
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        operation, _ = await prepare(client, app)
        complete = await client.post(f"/ai-api/v1/auth/operations/{operation}/complete", json={})
        assert complete.status_code == 200
    with pytest.raises(ServiceError) as error:
        await older(pg_factory, member, conversation, initial.history.older_cursor)
    assert error.value.code == "HISTORY_RESET_REQUIRED"
    current = await latest(pg_factory, member, conversation)
    assert current.history.history_epoch == initial.history.history_epoch + 1
    async with pg_factory() as db:
        moved = await db.get(Message, UUID(guest["messages"][0]["id"]))
        assert moved.membership_sequence > initial.sync.watermark_sequence
        assert (
            moved.created_at.isoformat().replace("+00:00", "Z") == guest["messages"][0]["createdAt"]
        )
