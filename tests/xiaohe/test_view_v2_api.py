from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, update
from test_admission import finish, send
from test_auth_operations import auth_setup, operation_body
from test_guest_claim import ready_center
from test_history_v2 import seed
from test_http_api import client_for, session
from test_identity_sessions import actor, bootstrap, login

from xiaohe import history, identity
from xiaohe.contracts import ChatSnapshot
from xiaohe.contracts_v2 import ChatSnapshotV2, HistoryPageV2, MessageBaselineV2
from xiaohe.models import (
    AuthContextRecord,
    AuthOperationRecord,
    Conversation,
    GuestBudget,
    GuestClaim,
    Session,
    User,
)

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
HEADER = "X-Xiaohe-View-Version"


def expected(sub="member:1:fixture"):
    return dict(issuer="https://sso.lvh.me:8443", sub=sub, sourceTenantId=1)


async def counts(factory):
    async with factory() as db:
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (User, Session, Conversation, GuestClaim)
            ]
        )


async def create_restore(
    client, app, sub="member:1:fixture", claim_sub=None, claim_source_tenant_id=1
):
    context, _ = await auth_setup(client, app)
    assert context.headers[HEADER] == "2" and set(context.json()) == {"csrf", "expiresAt"}
    response = await client.post(
        "/ai-api/v1/auth/operations", json=operation_body() | {"expectedIdentity": expected(sub)}
    )
    assert response.status_code == 201, response.text
    assert response.headers[HEADER] == "2" and "version" not in response.json()
    calls = ready_center(app, sub=claim_sub or sub, source_tenant_id=claim_source_tenant_id)
    return response.json()["operationId"], calls


async def test_bootstrap_complete_snapshot_share_v2_shape(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        guest = await session(client)
        ChatSnapshotV2.model_validate(guest)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation = UUID(guest["conversationId"])
        await seed(pg_factory, who=who, conversation_id=conversation)
        path = f"/ai-api/v1/conversations/{conversation}"
        snapshot = await client.get(path)
        value = ChatSnapshotV2.model_validate(snapshot.json())
        assert len(value.messages) == 10 and value.history.has_more
        page = await client.get(path + "/messages", params={"before": value.history.older_cursor})
        assert len(HistoryPageV2.model_validate(page.json()).messages) == 10
        located = await client.get(path + f"/messages/{UUID(int=1)}")
        assert MessageBaselineV2.model_validate(located.json()).message.parts[1].version == 7
        for response in (snapshot, page, located):
            assert response.headers[HEADER] == "2"
            assert response.headers["cache-control"] == "private, no-store"
        assert (
            await client.get(
                path + "/messages", params={"before": value.history.older_cursor, "limit": 100}
            )
        ).status_code == 422
        assert (
            await client.get(
                path + "/messages",
                params={"before": value.history.older_cursor},
                headers={HEADER: "1"},
            )
        ).status_code == 406
        operation, _ = await create_restore(client, app)
        result = await client.post(f"/ai-api/v1/auth/operations/{operation}/complete", json={})
        assert result.status_code == 200
        member = ChatSnapshotV2.model_validate(result.json())
        assert member.viewer.kind == "user" and len(member.messages) == 10
        assert result.headers[HEADER] == "2"


async def test_complete_v1_v2_reread_keeps_same_identity_and_deadline(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        await session(client)
        operation, calls = await create_restore(client, app)
        original = httpx.Cookies(client.cookies)
        path = f"/ai-api/v1/auth/operations/{operation}/complete"
        first = await client.post(path, json={})
        assert first.status_code == 200
        value = first.json()
        for version in ("1", "2"):
            response = await client.post(path, json={}, headers={HEADER: version})
            assert response.status_code == 200 and response.headers[HEADER] == version
            assert response.json()["viewer"] == value["viewer"]
            if version == "1":
                ChatSnapshot.model_validate(response.json())
            else:
                assert response.json()["sessionExpiresAt"] == value["sessionExpiresAt"]
        client.cookies = original
        lost = await client.post(path, json={})
        assert (
            lost.status_code == 200 and lost.json()["sessionExpiresAt"] == value["sessionExpiresAt"]
        )
        assert len(calls) == 1
        async with pg_factory() as db:
            sessions = list(
                await db.scalars(
                    select(Session).where(Session.user_id == value["viewer"]["userId"])
                )
            )
            assert len({(row.created_at, row.expires_at) for row in sessions}) == 1
            assert all(row.expires_at - row.created_at == timedelta(hours=8) for row in sessions)
            assert await db.scalar(select(func.count()).select_from(GuestClaim)) == 1


@pytest.mark.parametrize("values", [["3"], [""], ["2", "2"]])
async def test_unknown_view_cannot_mutate_session_or_auth_operation(pg_factory, values):
    async with client_for(pg_factory) as (client, app):
        headers = [(HEADER, value) for value in values]
        for path, body in (
            ("/session", {}),
            ("/auth/context", {}),
            ("/auth/operations", operation_body()),
        ):
            response = await client.post("/ai-api/v1" + path, json=body, headers=headers)
            assert response.status_code == 406
            assert response.json()["error"]["code"] == "VIEW_VERSION_UNSUPPORTED"
            assert HEADER not in response.headers and not response.headers.get_list("set-cookie")
            assert response.headers["cache-control"] == "private, no-store"
        async with pg_factory() as db:
            for model in (Session, GuestBudget, AuthContextRecord, AuthOperationRecord):
                assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(
    "kind,status",
    [
        ("absent", 401),
        ("guest", 401),
        ("expired_guest", 401),
        ("expired", 401),
        ("other", 409),
        ("matching", 200),
    ],
)
async def test_logged_in_bootstrap_guard_precedes_private_snapshot(
    pg_factory, monkeypatch, kind, status
):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        if kind in ("guest", "expired_guest"):
            await session(client)
            if kind == "expired_guest":
                async with pg_factory.begin() as db:
                    await db.execute(update(Session).values(expires_at=func.clock_timestamp()))
        elif kind != "absent":
            if kind == "expired":
                with monkeypatch.context() as patch:
                    patch.setattr(
                        identity,
                        "database_now",
                        AsyncMock(return_value=datetime.now(UTC) - timedelta(hours=9)),
                    )
                    _, token = await login(pg_factory, sub="account-A")
            else:
                _, token = await login(
                    pg_factory, sub="account-B" if kind == "other" else "account-A"
                )
            client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        before = await counts(pg_factory)
        reader = AsyncMock(
            side_effect=AssertionError("private reader reached before identity guard")
        )
        if status != 200:
            monkeypatch.setattr(history, "load_snapshot_v2", reader)
        response = await client.post(
            "/ai-api/v1/session", json={"expectedIdentity": expected("account-A")}
        )
        assert response.status_code == status, response.text
        assert response.headers[HEADER] == "2"
        if status != 200:
            assert await counts(pg_factory) == before
            assert not response.headers.get_list("set-cookie")
            reader.assert_not_called()
        else:
            assert response.json()["viewer"]["kind"] == "user"
            ChatSnapshotV2.model_validate(response.json())


async def test_v2_get_rejects_stale_csrf_after_cookie_account_change(pg_factory):
    _, a_token = await login(pg_factory, "account-A")
    _, b_token = await login(pg_factory, "account-B")
    a, _, _ = await bootstrap(pg_factory, a_token)
    b, _, _ = await bootstrap(pg_factory, b_token)
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        client.headers["X-Xiaohe-CSRF"] = a.csrf.token
        client.cookies.set("__Host-XIAOHESESSION", b_token, domain="portal.lvh.me", path="/")
        for suffix in ("", "/messages?before=bad", f"/messages/{UUID(int=1)}"):
            response = await client.get(f"/ai-api/v1/conversations/{b.conversation_id}" + suffix)
            assert (
                response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"
            )
            assert response.headers[HEADER] == "2"
        old = await client.get(
            f"/ai-api/v1/conversations/{b.conversation_id}", headers={HEADER: "1"}
        )
        assert old.status_code == 200 and "version" not in old.json()


@pytest.mark.parametrize("source", ["absent", "guest", "member"])
@pytest.mark.parametrize("mismatch", ["sub", "sourceTenantId"])
async def test_restore_other_account_rejected_before_bind_or_merge(pg_factory, source, mismatch):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        if source == "guest":
            await session(client)
        elif source == "member":
            _, token = await login(pg_factory, "account-A")
            client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        operation, calls = await create_restore(
            client,
            app,
            sub="account-A",
            claim_sub="account-B" if mismatch == "sub" else "account-A",
            claim_source_tenant_id=2 if mismatch == "sourceTenantId" else 1,
        )
        before = await counts(pg_factory)
        for version in ("2", "1"):
            response = await client.post(
                f"/ai-api/v1/auth/operations/{operation}/complete",
                json={},
                headers={HEADER: version},
            )
            assert (
                response.status_code == 409
                and response.json()["error"]["code"] == "IDENTITY_MISMATCH"
            )
            assert await counts(pg_factory) == before
            assert not response.headers.get_list("set-cookie")
        assert len(calls) == 1


@pytest.mark.parametrize("revoked", [False, True])
async def test_restore_expected_identity_is_frozen_before_redirect(pg_factory, revoked):
    _, token = await login(pg_factory, "account-A")
    if revoked:
        async with pg_factory.begin() as db:
            await db.execute(update(Session).values(revoked_at=func.clock_timestamp()))
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        _, seen = await auth_setup(client, app)
        wrong_issuer = await client.post(
            "/ai-api/v1/auth/operations",
            json=operation_body()
            | {"expectedIdentity": expected("account-A") | {"issuer": "https://other.example"}},
        )
        assert wrong_issuer.status_code == 409 and not seen
        body = operation_body() | {"expectedIdentity": expected("account-B")}
        bad = await client.post("/ai-api/v1/auth/operations", json=body)
        assert bad.status_code == 409 and not seen
        operation, _ = await create_restore(client, app, sub="account-A")
        async with pg_factory() as db:
            record = await db.get(AuthOperationRecord, UUID(operation))
            assert record.expected_identity == expected("account-A")
            digest = record.request_digest
        forbidden = await client.post(
            f"/ai-api/v1/auth/operations/{operation}/complete",
            json={"expectedIdentity": expected("account-B")},
        )
        assert forbidden.status_code == 422
        async with pg_factory() as db:
            record = await db.get(AuthOperationRecord, UUID(operation))
            assert record.request_digest == digest and record.expected_identity == expected(
                "account-A"
            )


async def test_live_full_guest_is_readable_but_expired_full_guest_has_no_new_episode(pg_factory):
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        await session(client)
        async with pg_factory.begin() as db:
            await db.execute(update(GuestBudget).values(message_count=100))
            await db.execute(update(Session).values(message_count=100))
        live = await client.post("/ai-api/v1/session", json={})
        assert live.status_code == 200 and live.json()["viewer"]["messageCount"] == 100
        async with pg_factory.begin() as db:
            await db.execute(update(Session).values(expires_at=func.clock_timestamp()))
        expired = await client.post("/ai-api/v1/session", json={})
        assert expired.status_code == 429
        assert expired.headers[HEADER] == "2" and "messages" not in expired.json()
        assert (await counts(pg_factory))[1] == 1


async def test_v2_pending_merge_keeps_retry_after(pg_factory, job_app):
    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    original, _, _ = await bootstrap(pg_factory, token)
    active = await send(pg_factory, member, UUID(original.conversation_id), "active-target")
    async with client_for(pg_factory) as (client, app):
        client.headers[HEADER] = "2"
        await session(client)
        operation, calls = await create_restore(client, app)
        path = f"/ai-api/v1/auth/operations/{operation}/complete"
        pending = await client.post(path, json={})
        assert pending.status_code == 202 and pending.headers["retry-after"] == "1"
        assert ChatSnapshotV2.model_validate(pending.json()).merge.status == "MERGE_PENDING"
        await finish(pg_factory, active.run.id)
        done = await client.post(path, json={})
        assert done.status_code == 200 and done.json()["merge"]["status"] == "MERGED"
        assert done.json()["sessionExpiresAt"] == pending.json()["sessionExpiresAt"]
        assert len(calls) == 1


async def test_v1_contract_is_unchanged(pg_factory):
    async with client_for(pg_factory) as (client, app):
        value = await session(client)
        ChatSnapshot.model_validate(value)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        await seed(pg_factory, who=who, conversation_id=UUID(value["conversationId"]))
        response = await client.get(f"/ai-api/v1/conversations/{value['conversationId']}")
        assert response.headers[HEADER] == "1"
        assert len(ChatSnapshot.model_validate(response.json()).messages) == 36
        assert "version" not in response.json() and "history" not in response.json()
        rejected = await client.post("/ai-api/v1/session", json={"expectedIdentity": expected()})
        assert rejected.status_code == 422
        await auth_setup(client, app)
        rejected = await client.post(
            "/ai-api/v1/auth/operations", json=operation_body() | {"expectedIdentity": expected()}
        )
        assert rejected.status_code == 422
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(AuthOperationRecord)) == 0
