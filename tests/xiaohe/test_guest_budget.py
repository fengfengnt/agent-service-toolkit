import asyncio
import hashlib
from http.cookies import SimpleCookie

import pytest
from fastapi import Response
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from test_identity_sessions import actor, request, system

from xiaohe.contracts import ServiceError
from xiaohe.guest_budget import BUDGET_COOKIE, adopt_legacy_guest
from xiaohe.identity import SESSION_COOKIE, _new_session, bootstrap_session
from xiaohe.models import Conversation, GuestBudget, GuestBudgetCredential, Message, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def open_chat(factory, cookies=None):
    response = Response()
    headers = (
        []
        if cookies is None
        else [(b"cookie", cookies.output(header="", sep=";").strip().encode())]
    )
    async with factory.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout = '2s'"))
        value = await bootstrap_session(request(headers=headers), response, db)
    result = SimpleCookie()
    if cookies is not None:
        result.load(cookies.output(header="", sep=";"))
    for header in response.headers.getlist("set-cookie"):
        result.load(header)
    return value, result, response


async def use_and_expire(factory, count):
    async with factory.begin() as db:
        await db.execute(update(GuestBudget).values(message_count=count))
        await db.execute(
            update(Session)
            .where(Session.guest_id.is_not(None))
            .values(
                message_count=count, expires_at=func.clock_timestamp() - text("interval '1 day'")
            )
        )


async def test_budget_survives_close_next_day_and_content_purge(pg_factory):
    first, cookies, response = await open_chat(pg_factory)
    await use_and_expire(pg_factory, 20)
    async with pg_factory.begin() as db:
        # 正文是否还存在不参与预算承接；真实安全清理竞争另由 Task 3 验证。
        await db.execute(text("DELETE FROM xiaohe.messages"))
        await db.execute(update(Session).values(purged_at=func.clock_timestamp()))
    second, newer, _ = await open_chat(pg_factory, cookies)
    assert first.viewer.guest_id != second.viewer.guest_id
    assert first.conversation_id != second.conversation_id
    assert second.viewer.message_count == 20 and second.viewer.message_limit == 100
    assert len(second.messages) == 1
    again, _, _ = await open_chat(pg_factory, newer)
    assert again == second
    with pytest.raises(ServiceError) as error:
        await actor(pg_factory, cookies[SESSION_COOKIE].value)
    assert error.value.code == "VISITOR_EXPIRED"
    for name in (BUDGET_COOKIE, SESSION_COOKIE):
        item = newer[name]
        assert item["max-age"] == "31536000"
        assert item["secure"] and item["httponly"] and item["samesite"] == "lax"
        assert item["path"] == "/" and not item["domain"]
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
        credential = await db.scalar(select(GuestBudgetCredential))
        assert (
            credential.token_digest
            == hashlib.sha256(cookies[BUDGET_COOKIE].value.encode()).hexdigest()
        )


async def test_legacy_adoption_uses_retained_count_once(pg_factory):
    async with pg_factory.begin() as db:
        record, token = await _new_session(db, system())
        record.message_count = 37
        record.purged_at = record.expires_at
        record.expires_at = await db.scalar(
            select(func.clock_timestamp() - text("interval '1 day'"))
        )
    cookies = SimpleCookie({SESSION_COOKIE: token.get_secret_value()})
    value, cookies, _ = await open_chat(pg_factory, cookies)
    assert value.viewer.message_count == 37
    async with pg_factory.begin() as db:
        old = await db.get(Session, record.id, with_for_update=True)
        assert (await adopt_legacy_guest(db, old)).id == old.budget_id
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1


async def test_budget_cookie_is_not_content_authority(pg_factory):
    _, cookies, _ = await open_chat(pg_factory)
    only_budget = SimpleCookie({BUDGET_COOKIE: cookies[BUDGET_COOKIE].value})
    with pytest.raises(ServiceError) as error:
        await open_chat(pg_factory, only_budget)
    assert error.value.code == "AUTH_REQUIRED"
    for bad in ("bad", "a" * 43):
        with pytest.raises(ServiceError):
            await open_chat(pg_factory, SimpleCookie({BUDGET_COOKIE: bad}))
    async with pg_factory.begin() as db:
        await db.execute(update(GuestBudgetCredential).values(revoked_at=func.clock_timestamp()))
    with pytest.raises(ServiceError):
        await open_chat(pg_factory, cookies)
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1


async def test_budget_renewal_and_rotation_never_reset_count(pg_factory):
    first, cookies, _ = await open_chat(pg_factory)
    original = cookies[BUDGET_COOKIE].value
    async with pg_factory.begin() as db:
        await db.execute(update(GuestBudget).values(message_count=19))
        await db.execute(update(Session).values(message_count=19))
    missing_budget = SimpleCookie({SESSION_COOKIE: cookies[SESSION_COOKIE].value})
    renewed, rotated, _ = await open_chat(pg_factory, missing_budget)
    assert renewed.viewer.message_count == 19
    assert renewed.conversation_id == first.conversation_id
    assert rotated[BUDGET_COOKIE].value != original
    async with pg_factory.begin() as db:
        ids = list(await db.scalars(select(GuestBudgetCredential.budget_id)))
        assert len(ids) == 2 and len(set(ids)) == 1
    old, _, _ = await open_chat(pg_factory, cookies)
    assert old.viewer.message_count == 19
    async with pg_factory.begin() as db:
        await db.execute(
            update(GuestBudgetCredential)
            .where(
                GuestBudgetCredential.token_digest == hashlib.sha256(original.encode()).hexdigest()
            )
            .values(revoked_at=func.clock_timestamp())
        )
    with pytest.raises(ServiceError):
        await open_chat(pg_factory, cookies)
    remaining, _, _ = await open_chat(pg_factory, rotated)
    assert remaining.viewer.message_count == 19


async def test_revoked_legacy_guest_cannot_receive_a_fresh_budget(pg_factory):
    async with pg_factory.begin() as db:
        record, token = await _new_session(db, system())
        record.revoked_at = record.created_at
    with pytest.raises(ServiceError):
        await open_chat(pg_factory, SimpleCookie({SESSION_COOKIE: token.get_secret_value()}))
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 0


async def test_concurrent_reopen_issues_one_episode_and_welcome(pg_factory):
    _, cookies, _ = await open_chat(pg_factory)
    await use_and_expire(pg_factory, 20)
    results = await asyncio.wait_for(
        asyncio.gather(open_chat(pg_factory, cookies), open_chat(pg_factory, cookies)), timeout=8
    )
    assert results[0][0].conversation_id == results[1][0].conversation_id
    assert results[0][1][SESSION_COOKIE].value == results[1][1][SESSION_COOKIE].value
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 2
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 2
        assert await db.scalar(select(func.count()).select_from(Message)) == 2


async def test_live_full_budget_is_readable_but_cannot_reopen_after_expiry(pg_factory):
    _, cookies, _ = await open_chat(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(GuestBudget).values(message_count=100))
        await db.execute(update(Session).values(message_count=100))
    value, _, _ = await open_chat(pg_factory, cookies)
    assert value.viewer.message_count == 100
    await use_and_expire(pg_factory, 100)
    with pytest.raises(ServiceError) as error:
        await open_chat(pg_factory, cookies)
    assert error.value.code == "VISITOR_LIMIT_REACHED"


async def test_bound_budget_cannot_be_swapped_or_decremented(pg_factory):
    await open_chat(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(GuestBudget).values(message_count=20))
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(update(GuestBudget).values(message_count=0))
        record = await db.scalar(select(Session))
        replacement = GuestBudget(
            system_id=record.system_id,
            tenant_id=record.tenant_id,
            message_count=20,
            message_limit=100,
        )
        db.add(replacement)
        await db.flush()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    update(Session).where(Session.id == record.id).values(budget_id=replacement.id)
                )


async def test_unrelated_guest_and_budget_cookies_cannot_be_joined(pg_factory):
    _, first, _ = await open_chat(pg_factory)
    _, second, _ = await open_chat(pg_factory)
    combined = SimpleCookie(
        {SESSION_COOKIE: first[SESSION_COOKIE].value, BUDGET_COOKIE: second[BUDGET_COOKIE].value}
    )
    with pytest.raises(ServiceError) as error:
        await open_chat(pg_factory, combined)
    assert error.value.code == "AUTH_REQUIRED"
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 2


async def test_concurrent_legacy_adoption_does_not_duplicate_budget(pg_factory):
    async with pg_factory.begin() as db:
        record, token = await _new_session(db, system())
        record.message_count = 23
        record.expires_at = await db.scalar(
            select(func.clock_timestamp() - text("interval '1 day'"))
        )
    cookies = SimpleCookie({SESSION_COOKIE: token.get_secret_value()})
    values = await asyncio.wait_for(
        asyncio.gather(open_chat(pg_factory, cookies), open_chat(pg_factory, cookies)), timeout=8
    )
    assert values[0][0].conversation_id == values[1][0].conversation_id
    assert values[0][0].viewer.message_count == 23
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
