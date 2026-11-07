"""组合产品 HTTP/worker/历史协议；身份中心和模型为显式隔离替身。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from test_agui_v2 import context, event
from test_cleanup_races import cleanup
from test_history_v2 import seed
from test_http_api import client_for, session
from test_identity_sessions import actor
from test_view_v2_api import HEADER, create_restore
from test_visitor_expiry import due

from xiaohe.agui_projection_v2 import project_event_v2
from xiaohe.events import record_event
from xiaohe.models import Command, Conversation, GuestBudget, Message, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("组合测试禁止真实 HTTP 外呼，包括模型、短信和 SSO")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


async def test_offline_lifecycle_history_journey(pg_factory, runtime_env):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        guest = await session(client)
        old_path = f"/ai-api/v1/conversations/{guest['conversationId']}"
        original_actor = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        for index in range(20):
            response = await client.post(
                old_path + "/messages",
                json={"clientMessageId": f"guest-{index}", "text": "隔离咨询"},
                headers={HEADER: str(1 + index % 2)},
            )
            assert response.status_code == 202, response.text
            await runtime_env.worker.execute_run(UUID(response.json()["run"]["id"]))
        assert len(runtime_env.calls) == 20
        await due(pg_factory, original_actor)
        assert await cleanup(pg_factory, original_actor.guest_id)
        renewed = await session(client)
        assert renewed["viewer"]["messageCount"] == 20
        assert renewed["viewer"]["messageLimit"] - renewed["viewer"]["messageCount"] == 80
        assert renewed["conversationId"] != guest["conversationId"]
        assert len(renewed["messages"]) == 1
        assert (await client.get(old_path)).status_code in (403, 410)

        operation, calls = await create_restore(client, app)
        cookies_before_complete = httpx.Cookies(client.cookies)
        complete_path = f"/ai-api/v1/auth/operations/{operation}/complete"
        response = await client.post(complete_path, json={})
        assert response.status_code == 200, response.text
        member = response.json()
        assert member["viewer"]["kind"] == "user" and member["merge"]["status"] == "MERGED"
        assert member["messages"] == renewed["messages"]
        client.headers["X-Xiaohe-CSRF"] = member["csrf"]["token"]
        path = f"/ai-api/v1/conversations/{member['conversationId']}"
        accepted = await client.post(
            path + "/messages", json={"clientMessageId": "member-card", "text": "隔离选择"}
        )
        assert accepted.status_code == 202
        run_id = UUID(accepted.json()["run"]["id"])
        await seed(
            pg_factory,
            count=33,
            who=await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION")),
            conversation_id=UUID(member["conversationId"]),
        )
        card = dict(
            type="choice",
            requestId="fixture-choice",
            prompt="请选择",
            options=[dict(id="intro", label="介绍")],
        )
        async with pg_factory.begin() as db:
            run = await db.get(Run, run_id)
            run.status, run.pending_request = "waiting_input", card
            command = await db.scalar(select(Command).where(Command.run_id == run_id))
            command.completed_at = datetime.now(UTC)
            message = await db.get(Message, UUID(int=1))
            message.parts = [card, message.parts[1]]
            conversation = await db.get(
                Conversation, UUID(member["conversationId"]), with_for_update=True
            )
            record_event(
                db,
                conversation,
                "message.upsert",
                {
                    "message": dict(
                        id=str(message.id),
                        role=message.role,
                        createdAt=message.created_at.isoformat(),
                        parts=message.parts,
                    )
                },
                run_id,
            )

        latest = (await client.get(path)).json()
        assert len(latest["messages"]) == 10
        assert latest["activeInput"]["messageId"] == str(UUID(int=1))
        assert str(UUID(int=1)) not in [item["id"] for item in latest["messages"]]
        located = await client.get(path + f"/messages/{UUID(int=1)}")
        assert located.status_code == 200
        assert located.json()["message"]["parts"][0]["requestId"] == "fixture-choice"
        assert located.json()["message"]["parts"][1]["version"] == 7
        pages, ids, cursor = (
            [len(latest["messages"])],
            [m["id"] for m in latest["messages"]],
            latest["history"]["olderCursor"],
        )
        while cursor:
            response = await client.get(path + "/messages", params={"before": cursor})
            assert response.status_code == 200, response.text
            page = response.json()
            pages.append(len(page["messages"]))
            ids.extend(m["id"] for m in page["messages"])
            cursor = page["history"]["olderCursor"]
        assert pages == [10, 10, 10, 5] and len(set(ids)) == 35

        client.cookies = cookies_before_complete
        retry = await client.post(complete_path, json={})
        assert retry.status_code == 200
        assert retry.json()["sessionExpiresAt"] == member["sessionExpiresAt"]
        assert retry.json()["viewer"]["userId"] == member["viewer"]["userId"]
        assert len(calls) == 1
        async with pg_factory() as db:
            assert await db.scalar(select(GuestBudget.message_count)) == 20
            deadlines = list(
                await db.scalars(
                    select(Session).where(Session.user_id == member["viewer"]["userId"])
                )
            )
            assert len({(row.created_at, row.expires_at) for row in deadlines}) == 1
            assert all(row.expires_at - row.created_at == timedelta(hours=8) for row in deadlines)


async def test_reconnect_new_cursor_fills_25_offline_messages(pg_factory):
    async with client_for(pg_factory) as (client, _):
        client.headers[HEADER] = "2"
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation_id = UUID(guest["conversationId"])
        await seed(pg_factory, who=who, conversation_id=conversation_id)
        path = f"/ai-api/v1/conversations/{conversation_id}"
        cached = (await client.get(path)).json()
        known = {m["id"] for m in cached["messages"]}
        async with pg_factory.begin() as db:
            conversation = await db.get(Conversation, conversation_id, with_for_update=True)
            for index in range(25):
                message = Message(
                    id=UUID(int=1000 + index),
                    conversation_id=conversation_id,
                    system_id=who.system.system_id,
                    tenant_id=who.system.tenant_id,
                    role="assistant",
                    parts=[dict(type="text", text=f"离线新增{index}")],
                    membership_sequence=conversation.event_sequence + 1,
                )
                db.add(message)
                record_event(
                    db,
                    conversation,
                    "message.upsert",
                    {
                        "message": dict(
                            id=str(message.id),
                            role=message.role,
                            createdAt=datetime.now(UTC).isoformat(),
                            parts=message.parts,
                        )
                    },
                )
        current = (await client.get(path)).json()
        found = {m["id"] for m in current["messages"]}
        cursor = current["history"]["olderCursor"]
        while not found.intersection(known):
            response = await client.get(path + "/messages", params={"before": cursor})
            assert response.status_code == 200, response.text
            page = response.json()
            found.update(m["id"] for m in page["messages"])
            cursor = page["history"]["olderCursor"]
        assert {str(UUID(int=1000 + i)) for i in range(25)} <= found
        assert current["sync"]["watermarkSequence"] > cached["sync"]["watermarkSequence"]


async def test_custom_delta_and_text_mirror_are_one_business_delta(contract_fixture):
    ctx, _ = context(contract_fixture)
    result = project_event_v2(
        event(ctx, 8, "message.delta", dict(messageId="M09", delta="唯一增量")), ctx
    )
    business = [frame["value"] for frame in result if frame.get("name") == "xiaohe.v2.event"]
    mirrors = [frame for frame in result if frame["type"] == "TEXT_MESSAGE_CONTENT"]
    assert len(business) == len(mirrors) == 1
    assert business[0]["payload"]["delta"] == mirrors[0]["delta"] == "唯一增量"
    assert ctx.public["messages"][-1]["parts"][0]["text"].count("唯一增量") == 1
