import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from test_admission import send
from test_agui_api import client_for
from test_http_api import session
from test_identity_sessions import actor

from xiaohe.agui_projection_v2 import (
    ProjectionContextV2,
    project_event_v2,
    project_message_baseline_v2,
    project_snapshot_v2,
)
from xiaohe.contracts import ServiceError
from xiaohe.contracts_v2 import ChatSnapshotV2, MessageBaselineV2
from xiaohe.events import persist_reply_chunk, record_event, stream_events
from xiaohe.models import Command, Conversation, Event, Message
from xiaohe.runs import acquire_run, reply_id


def frames(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def context(contract_fixture):
    snapshot = contract_fixture("view_v2")["snapshot"]
    snapshot["messages"] = [
        dict(copy.deepcopy(snapshot["messages"][0]), id=f"M{i:02d}") for i in range(10)
    ]
    snapshot["sync"] = dict(eventCursor="E7", watermarkSequence=7)
    snapshot["activeRun"] = dict(id="R1", status="running")
    initial = project_snapshot_v2(ChatSnapshotV2.model_validate(snapshot), "attempt-1")
    public = next(e["snapshot"]["xiaohe"] for e in initial if e["type"] == "STATE_SNAPSHOT")
    return ProjectionContextV2(
        snapshot["conversationId"], "attempt-1", "R1", public, history_epoch=0
    ), initial


def event(ctx, seq, kind, payload):
    return dict(
        eventId=f"E{seq}",
        conversationId=ctx.conversation_id,
        runId="R1",
        type=kind,
        payload=payload,
        sequence=seq,
        historyEpoch=ctx.history_epoch,
    )


def test_v2_projection_does_not_read_or_emit_all_history(contract_fixture):
    ctx, initial = context(contract_fixture)
    assert "csrf" not in ctx.public and ctx.public["version"] == 2
    assert any(e.get("name") == "xiaohe.v2.ready" for e in initial)
    message = copy.deepcopy(ctx.public["messages"][0])
    for seq in range(8, 38):
        message["id"] = f"M{seq:02d}"
        events = project_event_v2(event(ctx, seq, "message.upsert", {"message": message}), ctx)
        for item in events:
            if item["type"] == "STATE_SNAPSHOT":
                assert len(item["snapshot"]["xiaohe"]["messages"]) <= 10
            if item["type"] == "MESSAGES_SNAPSHOT":
                assert len(item["messages"]) <= 10
        assert events[0]["name"] == "xiaohe.v2.event"
    assert len(ctx.public["messages"]) == 10


def test_page_baseline_then_older_delta_does_not_duplicate_or_skip_other_message(contract_fixture):
    ctx, _ = context(contract_fixture)
    message = dict(
        copy.deepcopy(ctx.public["messages"][0]),
        id="hidden",
        parts=[{"type": "text", "text": "完整基线"}],
    )
    baseline = MessageBaselineV2(
        version=2,
        conversation_id=ctx.conversation_id,
        message=message,
        history_epoch=0,
        watermark_sequence=9,
    )
    initial = project_message_baseline_v2(baseline, ctx)
    assert initial[0]["name"] == "xiaohe.v2.message-baseline"
    old = project_event_v2(
        event(ctx, 8, "message.delta", {"messageId": "hidden", "delta": "重复"}), ctx
    )
    assert not any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in old)
    assert ctx.public["sync"]["watermarkSequence"] == 8
    other = project_event_v2(
        event(ctx, 9, "message.delta", {"messageId": "M09", "delta": "其他消息"}), ctx
    )
    assert sum(e["type"] == "TEXT_MESSAGE_CONTENT" for e in other) == 1
    assert "重复" not in json.dumps(ctx.public, ensure_ascii=False)
    assert len(ctx.public["messages"]) == 10
    with pytest.raises(ServiceError):
        project_event_v2(
            event(ctx, 10, "message.delta", {"messageId": "missing", "delta": "不能从空拼接"}), ctx
        )


def test_hidden_waiting_card_gets_a_locator_without_expanding_window(contract_fixture):
    ctx, _ = context(contract_fixture)
    message = dict(
        id="hidden",
        role="assistant",
        createdAt="2020-01-01T00:00:00Z",
        parts=[
            {
                "type": "choice",
                "requestId": "Q1",
                "prompt": "请选择",
                "options": [{"id": "one", "label": "服务"}],
            }
        ],
    )
    project_event_v2(event(ctx, 8, "message.upsert", {"message": message}), ctx)
    result = project_event_v2(
        event(ctx, 9, "run.updated", {"run": {"id": "R1", "status": "waiting_input"}}), ctx
    )
    assert ctx.public["activeInput"] == dict(
        runId="R1", messageId="hidden", requestId="Q1", kind="choice"
    )
    assert len(ctx.public["messages"]) == 10 and all(
        m["id"] != "hidden" for m in ctx.public["messages"]
    )
    assert result[-1]["type"] == "RUN_FINISHED"


def test_future_hidden_baseline_keeps_its_card_locator_after_older_upsert(contract_fixture):
    ctx, _ = context(contract_fixture)
    message = dict(
        id="hidden",
        role="assistant",
        createdAt="2020-01-01T00:00:00Z",
        parts=[{"type": "input_request", "requestId": "Q1", "prompt": "请补充"}],
    )
    project_message_baseline_v2(
        MessageBaselineV2(
            version=2,
            conversation_id=ctx.conversation_id,
            message=message,
            history_epoch=0,
            watermark_sequence=10,
        ),
        ctx,
    )
    stale = message | {"parts": [{"type": "text", "text": "旧正文"}]}
    project_event_v2(event(ctx, 8, "message.upsert", {"message": stale}), ctx)
    project_event_v2(
        event(ctx, 9, "run.updated", {"run": {"id": "R1", "status": "waiting_input"}}), ctx
    )
    assert ctx.public["activeInput"] == dict(
        runId="R1", messageId="hidden", requestId="Q1", kind="input_request"
    )


@pytest.mark.pg
@pytest.mark.asyncio
async def test_ready_allows_idle_eof_but_not_active_run_eof(pg_factory, job_app, monkeypatch):
    import xiaohe.agui as agui

    async def empty(*args, **kwargs):
        async def source():
            if False:
                yield b""

        return source()

    monkeypatch.setattr(agui, "stream_events", empty)
    async with client_for(pg_factory) as client:
        client.headers["X-Xiaohe-View-Version"] = "2"
        guest = await session(client)
        endpoint = "/ai-api/v1/agui/connect"
        idle = await client.post(endpoint, json={"threadId": guest["conversationId"]})
        assert frames(idle)[-1]["name"] == "xiaohe.v2.ready"
        assert not any(e["type"] == "RUN_ERROR" for e in frames(idle))
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        await send(pg_factory, who, UUID(guest["conversationId"]), "active")
        active = await client.post(endpoint, json={"threadId": guest["conversationId"]})
        assert any(e.get("name") == "xiaohe.v2.ready" for e in frames(active))
        assert frames(active)[-1]["type"] == "RUN_ERROR"
        assert frames(active)[-1]["code"] == "EVENT_CURSOR_EXPIRED"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_hidden_active_assistant_has_baseline_before_delta(pg_factory, job_app, monkeypatch):
    import xiaohe.agui as agui

    async with client_for(pg_factory) as client:
        client.headers["X-Xiaohe-View-Version"] = "2"
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation = UUID(guest["conversationId"])
        accepted = await send(pg_factory, who, conversation, "active")
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(accepted.run.id))
            command = await db.scalar(select(Command).where(Command.run_id == fence.run_id))
            message_id = reply_id(fence.run_id, command.id)
            await persist_reply_chunk(db, fence, message_id, "first", "原正文")
            current = await db.get(Conversation, conversation)
            for index in range(15):
                row = Message(
                    id=uuid4(),
                    conversation_id=conversation,
                    system_id="portal",
                    tenant_id=1,
                    role="assistant",
                    parts=[{"type": "system_notice", "text": "稍后通知"}],
                    created_at=datetime.now(UTC) + timedelta(seconds=index + 1),
                    membership_sequence=current.event_sequence + 1,
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
                            "createdAt": row.created_at.isoformat(),
                            "parts": row.parts,
                        }
                    },
                )

        async def fake(*args, **kwargs):
            async def source():
                async with pg_factory.begin() as db:
                    value = await persist_reply_chunk(db, fence, message_id, "second", "新增")
                    row = await db.get(Event, UUID(value.event_id))
                    raw = value.model_dump(mode="json", by_alias=True) | {
                        "sequence": row.sequence,
                        "historyEpoch": 0,
                    }
                yield ("data: " + json.dumps(raw) + "\n\n").encode()

            return source()

        monkeypatch.setattr(agui, "stream_events", fake)
        result = await client.post("/ai-api/v1/agui/connect", json={"threadId": str(conversation)})
        values = frames(result)
        baseline = next(
            i for i, e in enumerate(values) if e.get("name") == "xiaohe.v2.message-baseline"
        )
        delta = next(
            i
            for i, e in enumerate(values)
            if e.get("name") == "xiaohe.v2.event" and e["value"]["type"] == "message.delta"
        )
        ready = next(i for i, e in enumerate(values) if e.get("name") == "xiaohe.v2.ready")
        assert baseline < ready < delta
        assert values[baseline]["value"]["message"]["parts"] == [{"type": "text", "text": "原正文"}]
        assert all(
            len(e["messages"]) <= 10 and str(message_id) not in [m["id"] for m in e["messages"]]
            for e in values
            if e["type"] == "MESSAGES_SNAPSHOT"
        )
        assert not any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in values)


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,code", [("history_epoch", "HISTORY_RESET_REQUIRED"), ("ownership_epoch", "FORBIDDEN")]
)
async def test_epoch_or_ownership_change_terminates_old_stream(
    pg_factory, job_app, monkeypatch, field, code
):
    import xiaohe.agui as agui

    async with client_for(pg_factory) as client:
        client.headers["X-Xiaohe-View-Version"] = "2"
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation = UUID(guest["conversationId"])
        accepted = await send(pg_factory, who, conversation, "active")

        async def fake(*args, **kwargs):
            async def source():
                async with pg_factory.begin() as db:
                    await db.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation)
                        .values(**{field: 1})
                    )
                raw = dict(
                    eventId=str(uuid4()),
                    conversationId=str(conversation),
                    runId=accepted.run.id,
                    sequence=5,
                    historyEpoch=0,
                    type="message.upsert",
                    payload={
                        "message": {
                            "id": str(uuid4()),
                            "role": "assistant",
                            "createdAt": datetime.now(UTC).isoformat(),
                            "parts": [{"type": "text", "text": "PRIVATE-LATE"}],
                        }
                    },
                )
                yield ("data: " + json.dumps(raw) + "\n\n").encode()

            return source()

        monkeypatch.setattr(agui, "stream_events", fake)
        response = await client.post(
            "/ai-api/v1/agui/connect", json={"threadId": str(conversation)}
        )
        assert "PRIVATE-LATE" not in response.text
        assert frames(response)[-1]["code"] == code
        if field == "history_epoch":
            with pytest.raises(ServiceError):
                await stream_events(pg_factory, who, conversation, view_version=2, history_epoch=0)
