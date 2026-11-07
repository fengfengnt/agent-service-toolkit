import asyncio
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, update
from test_admission import finish, send
from test_cleanup_races import cleanup
from test_guest_budget import open_chat
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import actor
from test_visitor_expiry import due

from xiaohe.contracts import ServiceError
from xiaohe.models import Command, GuestBudget, GuestBudgetReceipt, Message, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_old_episode_retry_after_command_purge_is_not_redirected(pg_factory, runtime_env):
    first, cookies, _ = await open_chat(pg_factory)
    who = await actor(pg_factory, cookies["__Host-XIAOHESESSION"].value)
    accepted = await send(pg_factory, who, UUID(first.conversation_id), "original-key")
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    await due(pg_factory, who)
    assert await cleanup(pg_factory, who.guest_id)
    second, newer, _ = await open_chat(pg_factory, cookies)
    assert second.viewer.message_count == 1
    current = await actor(pg_factory, newer["__Host-XIAOHESESSION"].value)
    for original_actor, conversation in (
        (who, first.conversation_id),
        (current, second.conversation_id),
    ):
        with pytest.raises(ServiceError):
            await send(pg_factory, original_actor, UUID(conversation), "original-key")
    async with pg_factory() as db:
        assert await db.scalar(select(GuestBudget.message_count)) == 1
        assert await db.scalar(select(func.count()).select_from(GuestBudgetReceipt)) == 1
        assert await db.scalar(select(func.count()).select_from(Command)) == 0
        assert await db.scalar(select(func.count()).select_from(Message)) == 1


async def test_claim_logout_and_expiry_do_not_reset_budget(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "source")
        await finish(pg_factory, turn.run.id)
        op, calls = await prepare(client, app)
        complete = await client.post(f"/ai-api/v1/auth/operations/{op}/complete", json={})
        assert complete.status_code == 200
        member = complete.json()
        client.headers["X-Xiaohe-CSRF"] = member["csrf"]["token"]
        assert (await client.post("/ai-api/v1/auth/logout", json={})).status_code == 204
        anonymous = await session(client)
        assert anonymous["viewer"]["kind"] == "guest"
        assert anonymous["viewer"]["messageCount"] == 1
        assert anonymous["conversationId"] != member["conversationId"]
        assert len(anonymous["messages"]) == 1
        assert len(calls) == 1
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
            assert await db.scalar(select(func.count()).select_from(GuestBudgetReceipt)) == 1
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == UUID(member["conversationId"]))
                )
                >= 2
            )


async def test_legacy_claim_adopts_budget_before_revocation(pg_factory, job_app):
    from test_identity_sessions import system

    from xiaohe.conversations import default_conversation, ensure_welcome
    from xiaohe.identity import SESSION_COOKIE, _actor, _new_session

    async with pg_factory.begin() as db:
        record, token = await _new_session(db, system())
        record.message_count = 27
        conversation = await default_conversation(db, _actor(record, system()))
        await ensure_welcome(db, conversation)
    async with client_for(pg_factory) as (client, app):
        client.cookies.set(
            SESSION_COOKIE, token.get_secret_value(), domain="portal.lvh.me", path="/"
        )
        op, _ = await prepare(client, app)
        complete = await client.post(f"/ai-api/v1/auth/operations/{op}/complete", json={})
        assert complete.status_code == 200
        client.headers["X-Xiaohe-CSRF"] = complete.json()["csrf"]["token"]
        assert (await client.post("/ai-api/v1/auth/logout", json={})).status_code == 204
        fresh = await session(client)
        assert fresh["viewer"]["messageCount"] == 27
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
            assert await db.scalar(select(func.count()).select_from(GuestBudgetReceipt)) == 0


async def test_concurrent_bootstrap_admission_claim_cleanup(pg_factory, runtime_env):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        async with pg_factory.begin() as db:
            await db.execute(update(GuestBudget).values(message_count=99))
            await db.execute(update(Session).values(message_count=99))
        op, _ = await prepare(client, app)

        async def reopen():
            return await client.post("/ai-api/v1/session", json={})

        results = await asyncio.wait_for(
            asyncio.gather(
                send(pg_factory, who, UUID(guest["conversationId"]), "race-100"),
                reopen(),
                cleanup(pg_factory, who.guest_id),
                client.post(f"/ai-api/v1/auth/operations/{op}/complete", json={}),
                return_exceptions=True,
            ),
            timeout=15,
        )
        assert not any(isinstance(r, (TimeoutError, asyncio.TimeoutError)) for r in results)
        assert all(not isinstance(r, Exception) or isinstance(r, ServiceError) for r in results)
        assert all(
            not isinstance(r, httpx.Response)
            or r.status_code in (200, 202, 401, 403, 409, 410, 429)
            for r in results
        )
        async with pg_factory() as db:
            count = await db.scalar(select(GuestBudget.message_count))
            receipts = await db.scalar(select(func.count()).select_from(GuestBudgetReceipt))
            assert count in (99, 100) and receipts == count - 99
            assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
