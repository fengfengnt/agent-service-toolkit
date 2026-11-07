import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from test_auth_operations import auth_setup
from test_http_api import client_for, session
from test_identity_sessions import login

from xiaohe.models import AuthContextRecord, Session, User

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def auth_context(client, app):
    await auth_setup(client, app)
    result = await client.post("/ai-api/v1/auth/context", json={})
    assert result.status_code == 200
    csrf = result.json()["csrf"]
    client.headers[csrf["headerName"]] = csrf["token"]


@pytest.mark.parametrize("state", ["expired", "revoked"])
async def test_inactive_member_logout_clears_cookie_and_allows_guest(pg_factory, state):
    record, token = await login(pg_factory)
    async with client_for(pg_factory) as (client, app):
        client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        member = await session(client)
        client.headers.pop("X-Xiaohe-CSRF")
        async with pg_factory.begin() as db:
            if state == "expired":
                anchor = datetime.now(UTC) - timedelta(hours=9)
                expired_token = "e" * 43
                record = Session(
                    system_id=record.system_id,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    token_digest=hashlib.sha256(expired_token.encode()).hexdigest(),
                    authenticated_at=record.authenticated_at,
                    csrf_token="fixture-old-csrf",
                    toolkit_user_id=record.user_id,
                    created_at=anchor,
                    expires_at=anchor + timedelta(hours=8),
                )
                db.add(record)
                client.cookies.set(
                    "__Host-XIAOHESESSION", expired_token, domain="portal.lvh.me", path="/"
                )
            else:
                await db.execute(
                    update(Session)
                    .where(Session.id == record.id)
                    .values(
                        revoked_at=func.clock_timestamp(),
                    )
                )
        assert (await client.post("/ai-api/v1/session", json={})).status_code == 401
        await auth_context(client, app)
        response = await client.post("/ai-api/v1/auth/logout", json={})
        assert response.status_code == 204
        assert "__Host-XIAOHESESSION" not in client.cookies
        assert "__Host-XIAOHEAUTH" not in client.cookies
        assert all("BUDGET" not in item for item in response.headers.get_list("set-cookie"))
        anonymous = await session(client)
        assert anonymous["viewer"]["kind"] == "guest"
        assert anonymous["conversationId"] != member["conversationId"]
        assert (
            await client.get("/ai-api/v1/conversations/" + member["conversationId"])
        ).status_code == 403
        async with pg_factory() as db:
            assert await db.get(User, record.user_id)


@pytest.mark.parametrize("state", ["active_member", "expired_guest", "unknown"])
async def test_auth_context_alone_cannot_logout_other_session_states(pg_factory, state):
    async with client_for(pg_factory) as (client, app):
        if state == "active_member":
            _, token = await login(pg_factory)
            client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        elif state == "expired_guest":
            await session(client)
            client.headers.pop("X-Xiaohe-CSRF")
            async with pg_factory.begin() as db:
                await db.execute(
                    update(Session)
                    .where(Session.guest_id.is_not(None))
                    .values(
                        expires_at=func.clock_timestamp() - timedelta(seconds=1),
                        message_count=100,
                    )
                )
        else:
            client.cookies.set("__Host-XIAOHESESSION", "u" * 43, domain="portal.lvh.me", path="/")
        await auth_context(client, app)
        response = await client.post("/ai-api/v1/auth/logout", json={})
        assert (
            response.status_code
            == {"active_member": 403, "expired_guest": 410, "unknown": 401}[state]
        )
        assert not response.headers.get_list("set-cookie")
        if state == "expired_guest":
            async with pg_factory() as db:
                assert await db.scalar(select(Session.message_count)) == 100


@pytest.mark.parametrize(
    "proof",
    ["missing", "wrong", "duplicate", "origin", "scope", "expired_context", "no_auth_cookie"],
)
async def test_inactive_member_cleanup_requires_bound_auth_context(pg_factory, proof):
    record, token = await login(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session).where(Session.id == record.id).values(revoked_at=func.clock_timestamp())
        )
    async with client_for(pg_factory) as (client, app):
        client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        await auth_context(client, app)
        if proof == "missing":
            client.headers.pop("X-Xiaohe-Auth-CSRF")
        elif proof == "wrong":
            client.headers["X-Xiaohe-Auth-CSRF"] = "wrong"
        elif proof == "duplicate":
            client.headers = list(client.headers.multi_items()) + [("X-Xiaohe-Auth-CSRF", "extra")]
        elif proof == "origin":
            client.headers["Origin"] = "https://attacker.example"
        elif proof == "scope":
            client.headers["Origin"] = "https://other.example"
            client.headers["X-Xiaohe-System-Id"] = "other"
        elif proof == "expired_context":
            async with pg_factory.begin() as db:
                await db.execute(
                    update(AuthContextRecord).values(
                        expires_at=func.clock_timestamp() - timedelta(seconds=1)
                    )
                )
        elif proof == "no_auth_cookie":
            client.cookies.delete("__Host-XIAOHEAUTH", domain="portal.lvh.me", path="/")
        response = await client.post("/ai-api/v1/auth/logout", json={})
        assert response.status_code == 403
        assert not response.headers.get_list("set-cookie")
