from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from test_admission import finish, send, visitor
from test_agui_api import client_for, input_for
from test_http_api import session
from test_identity_sessions import actor
from test_visitor_expiry import due

from xiaohe.admission import _accept
from xiaohe.contracts import ServiceError
from xiaohe.models import GuestBudget, GuestBudgetReceipt, Message, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_all_paths_share_one_budget_at_99_100_101(pg_factory, job_app):
    async with client_for(pg_factory) as client:
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}/messages"
        async with pg_factory.begin() as db:
            await db.execute(update(GuestBudget).values(message_count=98))
            await db.execute(update(Session).values(message_count=98))
        rest = await client.post(path, json={"clientMessageId": "rest-99", "text": "了解服务"})
        assert rest.status_code == 202 and rest.json()["viewer"]["messageCount"] == 99
        await finish(pg_factory, rest.json()["run"]["id"])
        body = input_for(snapshot["conversationId"], key="agui-100")
        accepted = await client.post("/ai-api/v1/agui/run", json=body)
        assert accepted.status_code == 202 and accepted.json()["viewer"]["messageCount"] == 100
        retry = await client.post("/ai-api/v1/agui/run", json=body)
        assert retry.status_code == 202 and retry.json() == accepted.json()
        await finish(pg_factory, accepted.json()["run"]["id"])
        rejected = await client.post(path, json={"clientMessageId": "rest-101", "text": "再咨询"})
        assert rejected.status_code == 429
        async with pg_factory() as db:
            assert await db.scalar(select(GuestBudget.message_count)) == 100
            assert await db.scalar(select(func.count()).select_from(GuestBudgetReceipt)) == 2
            assert await db.scalar(select(func.count()).select_from(Message)) == 3
            assert await db.scalar(select(func.count()).select_from(Run)) == 2


async def test_system_controls_and_welcome_are_free(pg_factory, job_app):
    async with client_for(pg_factory) as client:
        snapshot = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        async with pg_factory.begin() as db:
            before = (await db.get(Session, who.session_id)).expires_at
            result = await _accept(
                db,
                who,
                UUID(snapshot["conversationId"]),
                "message",
                "control-1",
                {"receptionControl": "collect"},
                system_notice="采集信息",
            )
        assert result.viewer.message_count == 0
        assert result.message.parts[0].type == "system_notice"
        async with pg_factory() as db:
            assert (await db.get(Session, who.session_id)).expires_at == before
            assert await db.scalar(select(GuestBudget.message_count)) == 0
            assert await db.scalar(select(func.count()).select_from(GuestBudgetReceipt)) == 0


async def test_hundredth_reply_finishes_until_real_expiry(pg_factory, runtime_env):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(Session).values(message_count=99))
    accepted = await send(pg_factory, who, conversation, "hundredth")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "completed"
        assert await db.scalar(select(GuestBudget.message_count)) == 100
        assert await db.scalar(select(func.count()).select_from(Message)) == 2
    with pytest.raises(ServiceError) as error:
        await send(pg_factory, who, conversation, "hundred-and-one")
    assert error.value.code == "VISITOR_LIMIT_REACHED"
    await due(pg_factory, who)
    with pytest.raises(ServiceError) as error:
        await send(pg_factory, who, conversation, "hundredth")
    assert error.value.code == "VISITOR_EXPIRED"
