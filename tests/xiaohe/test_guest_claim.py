from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from test_admission import finish, send
from test_auth_operations import auth_setup, operation_body
from test_http_api import client_for, session
from test_identity_sessions import actor, login
from test_visitor_expiry import due

from xiaohe.auth_bridge import SsoBridgeClient
from xiaohe.conversations import default_conversation
from xiaohe.models import AuthOperationRecord, Message, Session, User
from xiaohe.runs import acquire_run, require_write

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def ready_center(app, sub="member:1:fixture", *, source_tenant_id=1):
    calls = []

    def center(request):
        calls.append(request)
        assert str(request.url).endswith("/claim")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "",
                "data": {
                    "issuer": "https://sso.lvh.me:8443",
                    "sub": sub,
                    "sourceTenantId": source_tenant_id,
                    "authenticatedAt": datetime.now(UTC).isoformat(),
                    "portalClientId": "app_localportal",
                    "transactionId": "00000000-0000-4000-8000-000000000010",
                },
            },
        )

    app.state.xiaohe_sso = SsoBridgeClient(
        app.state.xiaohe_settings, transport=httpx.MockTransport(center)
    )
    return calls


async def prepare(client, app):
    await auth_setup(client, app)
    op = await client.post("/ai-api/v1/auth/operations", json=operation_body())
    assert op.status_code == 201
    return op.json()["operationId"], ready_center(app)


async def test_first_identity_claim_moves_stable_messages_and_retry_recovers_lost_cookie(
    pg_factory, job_app
):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "first")
        await finish(pg_factory, turn.run.id)
        operation_id, calls = await prepare(client, app)
        original_cookies = httpx.Cookies(client.cookies)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        first = await client.post(path, json={})
        assert first.status_code == 200, first.text
        value = first.json()
        assert value["viewer"]["kind"] == "user" and value["merge"]["status"] == "MERGED"
        expected_ids = [m["id"] for m in guest["messages"]] + [turn.message.id]
        assert [m["id"] for m in value["messages"]] == expected_ids
        assert value["messages"][0] == guest["messages"][0]
        user_id = value["viewer"]["userId"]
        async with pg_factory() as db:
            operation = await db.get(AuthOperationRecord, UUID(operation_id))
            anchor = operation.authenticated_session_created_at
            assert (await db.get(User, user_id)).sub == "member:1:fixture"
        client.cookies = original_cookies
        again = await client.post(path, json={})
        assert again.status_code == 200 and again.json()["viewer"]["userId"] == user_id
        assert [m["id"] for m in again.json()["messages"]] == expected_ids
        assert len(calls) == 1
        restored = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        async with pg_factory() as db:
            new_session = await db.get(Session, restored.session_id)
            assert (
                new_session.created_at == anchor
                and new_session.expires_at == anchor + timedelta(hours=8)
            )
            assert len((await db.scalars(select(Message))).all()) == len(expected_ids)
            assert len((await db.scalars(select(User))).all()) == 1


async def test_target_active_claim_is_durable_and_auto_finishes_without_oidc(
    pg_factory, job_app, runtime_env
):
    from xiaohe.models import GuestClaim

    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        canonical = await default_conversation(db, member)
    target = await send(pg_factory, member, canonical.id, "target")
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        source = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, source, UUID(guest["conversationId"]), "source")
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(turn.run.id))
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        pending = await client.post(path, json={})
        assert pending.status_code == 202 and pending.json()["viewer"]["kind"] == "user"
        assert pending.json()["merge"]["status"] == "MERGE_PENDING"
        assert pending.headers["Retry-After"] == "1"
        async with pg_factory.begin() as db:
            from xiaohe.contracts import ServiceError

            with pytest.raises(ServiceError):
                await require_write(db, fence)
        await due(pg_factory, source)
        await runtime_env.worker.maintain()
        async with pg_factory() as db:
            assert (await db.scalar(select(GuestClaim))).status == "pending"
            assert (await db.get(Message, UUID(turn.message.id))).conversation_id == UUID(
                guest["conversationId"]
            )
        await finish(pg_factory, target.run.id)
        await runtime_env.worker.maintain()
        final = await client.post(path, json={})
        assert final.status_code == 200 and final.json()["merge"]["status"] == "MERGED"
        assert {m["id"] for m in final.json()["messages"]} == {
            target.message.id,
            turn.message.id,
            *[m["id"] for m in guest["messages"]],
        }
        assert len(calls) == 1


async def test_expired_source_is_skipped_without_resurrection(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "source")
        operation_id, calls = await prepare(client, app)
        await due(pg_factory, who)
        result = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert result.status_code == 200 and result.json()["merge"]["status"] == "SKIPPED_EXPIRED"
        messages = result.json()["messages"]
        source_ids = {turn.message.id, *[m["id"] for m in guest["messages"]]}
        assert len(messages) == 1 and messages[0]["role"] == "assistant"
        assert messages[0]["id"] not in source_ids
        assert any(p["type"] == "suggestions" for p in messages[0]["parts"])
        async with pg_factory() as db:
            assert (await db.get(Message, UUID(turn.message.id))).conversation_id == UUID(
                guest["conversationId"]
            )


async def test_completed_operation_new_context_requires_same_live_account(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        first = await client.post(path, json={})
        assert first.status_code == 200 and first.json()["merge"] is None
        client.cookies.delete("__Host-XIAOHEAUTH")
        context = await client.post("/ai-api/v1/auth/context", json={})
        client.headers["X-Xiaohe-Auth-CSRF"] = context.json()["csrf"]["token"]
        async with pg_factory.begin() as db:
            (
                await db.get(AuthOperationRecord, UUID(operation_id))
            ).expires_at = func.clock_timestamp()
        assert (await client.post(path, json={})).status_code == 200
        client.cookies.delete("__Host-XIAOHESESSION")
        assert (await client.post(path, json={})).status_code == 403
        other, other_token = await login(pg_factory, sub="different-account")
        client.cookies.set("__Host-XIAOHESESSION", other_token, domain="portal.lvh.me", path="/")
        assert (await client.post(path, json={})).status_code == 403
        assert len(calls) == 1


async def test_local_logout_revokes_original_complete_recovery_capability(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        operation_id, calls = await prepare(client, app)
        original_cookies = httpx.Cookies(client.cookies)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        first = await client.post(path, json={})
        assert first.status_code == 200
        client.headers[first.json()["csrf"]["headerName"]] = first.json()["csrf"]["token"]
        assert (await client.post("/ai-api/v1/auth/logout", json={})).status_code == 204
        client.cookies = original_cookies
        assert (await client.post(path, json={})).status_code in (403, 409)
        assert len(calls) == 1
