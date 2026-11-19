import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select
from test_auth_operations import auth_setup, operation_body
from test_guest_claim import ready_center
from test_http_api import client_for, session
from test_identity_sessions import login

from xiaohe.models import AuthOperationRecord, User

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_portal_login_is_v2_only_and_keeps_restore_identity_requirement(pg_factory):
    async with client_for(pg_factory) as (client, app):
        await auth_setup(client, app)
        assert (
            await client.post("/ai-api/v1/auth/operations", json=operation_body("portal_login"))
        ).status_code == 422
        client.headers["X-Xiaohe-View-Version"] = "2"
        assert (
            await client.post("/ai-api/v1/auth/operations", json=operation_body("restore"))
        ).status_code == 422
        body = operation_body("portal_login")
        body["expectedIdentity"] = {
            "issuer": "https://sso.lvh.me:8443",
            "sub": "forged",
            "sourceTenantId": 1,
        }
        assert (await client.post("/ai-api/v1/auth/operations", json=body)).status_code == 422


async def test_first_portal_login_binds_one_transaction_and_completes_guest_once(pg_factory):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        _, seen = await auth_setup(client, app)
        client.headers["X-Xiaohe-View-Version"] = "2"
        body = operation_body("portal_login")
        response = await client.post("/ai-api/v1/auth/operations", json=body)
        assert response.status_code == 201, response.text
        operation = response.json()
        assert operation["stage"] == "BROWSER_COMPLETION_REQUIRED"
        assert set(operation["completion"]["fields"]) == {"transactionId"}
        sent = json.loads(seen[0].content)
        assert sent["state"] == body["oidc"]["state"] and sent["nonce"] == body["oidc"]["nonce"]
        path = f"/ai-api/v1/auth/operations/{operation['operationId']}"
        assert (await client.post(path + "/complete", json={})).status_code == 409
        sms = await client.post(
            path + "/sms", json={"mobile": "13800138000", "captchaVerification": "fixture-picture"}
        )
        assert sms.status_code == 409
        calls = ready_center(app)
        first = await client.post(path + "/complete", json={})
        assert first.status_code == 200, first.text
        assert first.json()["viewer"]["kind"] == "user"
        assert first.json()["messages"][0]["id"] == guest["messages"][0]["id"]
        repeated = await client.post(path + "/complete", json={})
        assert repeated.status_code == 200 and repeated.json()["viewer"] == first.json()["viewer"]
        assert len(calls) == 1
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_existing_ai_member_uses_direct_oidc_without_bridge(pg_factory):
    async with client_for(pg_factory) as (client, app):
        _, token = await login(pg_factory)
        client.cookies.set("__Host-XIAOHESESSION", token)
        _, seen = await auth_setup(client, app)
        client.headers["X-Xiaohe-View-Version"] = "2"
        body = operation_body("portal_login")
        response = await client.post("/ai-api/v1/auth/operations", json=body)
        assert response.status_code == 201, response.text
        value = response.json()
        assert value["stage"] == "OIDC_REQUIRED" and value["completion"]["method"] == "GET"
        url = urlsplit(value["completion"]["action"])
        query = parse_qs(url.query)
        assert (
            url.scheme == "https"
            and url.netloc == "sso.lvh.me:8443"
            and url.path == "/oauth2/authorize"
        )
        assert query["state"] == [body["oidc"]["state"]] and query["code_challenge"] == [
            body["oidc"]["codeChallenge"]
        ]
        assert not seen


async def test_expired_member_joint_login_preserves_trusted_identity(pg_factory, monkeypatch):
    async with client_for(pg_factory) as (client, app):
        _, token = await login(pg_factory)
        client.cookies.set("__Host-XIAOHESESSION", token)

        async def later(_db):
            return datetime.now(UTC) + timedelta(hours=9)

        monkeypatch.setattr("xiaohe.authentication.database_now", later)
        await auth_setup(client, app)
        client.headers["X-Xiaohe-View-Version"] = "2"
        response = await client.post(
            "/ai-api/v1/auth/operations", json=operation_body("portal_login")
        )
        assert response.status_code == 201, response.text
        operation = response.json()
        assert operation["stage"] == "BROWSER_COMPLETION_REQUIRED"
        ready_center(app, sub="member:1:other")
        result = await client.post(
            f"/ai-api/v1/auth/operations/{operation['operationId']}/complete", json={}
        )
        assert result.status_code == 409 and result.json()["error"]["code"] == "IDENTITY_MISMATCH"
        async with pg_factory() as db:
            row = await db.scalar(select(AuthOperationRecord))
            assert row.expected_identity["sub"] == "member:1:fixture"
            assert await db.scalar(select(func.count()).select_from(User)) == 1
