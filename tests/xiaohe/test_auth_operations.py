import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from test_http_api import client_for, session

from xiaohe.models import Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def operation_body(intent="restore"):
    return {
        "intent": intent,
        "oidc": {
            "clientId": "app_localportal",
            "redirectUri": "https://portal.lvh.me:9444/portal/auth/callback",
            "state": "state-" + uuid4().hex,
            "nonce": "nonce-" + uuid4().hex,
            "codeChallenge": "a" * 43,
            "codeChallengeMethod": "S256",
        },
        "browserCompletionChallenge": "b" * 43,
        "browserCompletionChallengeMethod": "S256",
    }


async def auth_setup(client, app):
    from xiaohe.auth_bridge import SsoBridgeClient

    config = app.state.xiaohe_settings
    config.sso_bridge_enabled = True
    config.sso_bridge_base_url = "https://sso.lvh.me:8443"
    config.sso_bridge_redirect_uri = "https://portal.lvh.me:9444/portal/auth/callback"
    config.sso_bridge_application_id = "fixture-app"
    from pydantic import SecretStr

    config.sso_bridge_secret = SecretStr("fixture-bridge-secret")
    client.headers["X-Portal-Client-IP"] = "127.0.0.1"
    seen = []
    txn = "00000000-0000-4000-8000-000000000010"

    def center(request):
        seen.append(request)
        if str(request.url).endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "",
                    "data": {
                        "transactionId": txn,
                        "stage": "CREATED",
                        "expiresAt": (datetime.now(UTC) + timedelta(seconds=600)).isoformat(),
                        "challengeId": None,
                        "credential": None,
                        "credentialExpiresAt": None,
                        "agreementVersion": None,
                        "agreementPath": None,
                    },
                },
            )
        return httpx.Response(
            409, json={"code": 1002029027, "msg": "private-not-ready", "data": None}
        )

    app.state.xiaohe_sso = SsoBridgeClient(config, transport=httpx.MockTransport(center))
    context = await client.post("/ai-api/v1/auth/context", json={})
    assert context.status_code == 200, context.text
    client.headers[context.json()["csrf"]["headerName"]] = context.json()["csrf"]["token"]
    return context, seen


async def test_expired_guest_auth_context_is_independent_and_stable(pg_factory):
    async with client_for(pg_factory) as (client, app):
        before = await session(client)
        async with pg_factory.begin() as db:
            await db.execute(
                update(Session).values(expires_at=func.clock_timestamp(), message_count=100)
            )
        assert (await client.post("/ai-api/v1/session", json={})).status_code == 410
        context, seen = await auth_setup(client, app)
        assert set(context.json()) == {"csrf", "expiresAt"}
        assert context.json()["csrf"]["headerName"] == "X-Xiaohe-Auth-CSRF"
        cookie = context.headers["set-cookie"]
        assert "__Host-XIAOHEAUTH=" in cookie and "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=lax" in cookie and "Domain=" not in cookie
        again = await client.post("/ai-api/v1/auth/context", json={})
        assert again.json() == context.json() and not seen
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Session)) == 1
            assert (await db.scalar(select(Session))).message_count == 100
        assert (
            await client.get(f"/ai-api/v1/conversations/{before['conversationId']}")
        ).status_code == 410


async def test_card_transaction_binding_and_frontend_success_never_logs_in(pg_factory):
    async with client_for(pg_factory) as (client, app):
        await session(client)
        context, seen = await auth_setup(client, app)
        body = operation_body()
        created = await client.post("/ai-api/v1/auth/operations", json=body)
        assert created.status_code == 201, created.text
        operation = created.json()
        assert operation["stage"] == "BROWSER_COMPLETION_REQUIRED"
        assert operation["completion"] == {
            "method": "POST",
            "action": "https://sso.lvh.me:8443/sso/bridge/browser-completion",
            "fields": {"transactionId": "00000000-0000-4000-8000-000000000010"},
        }
        repeated = await client.post("/ai-api/v1/auth/operations", json=body)
        assert repeated.json()["operationId"] == operation["operationId"]
        assert seen[0].headers["Idempotency-Key"] == seen[1].headers["Idempotency-Key"]
        sent = json.loads(seen[0].content)
        assert "intent" not in sent and "tenantId" not in sent and "systemId" not in sent
        assert sent["browserVerifierChallenge"] == body["browserCompletionChallenge"]
        complete_path = f"/ai-api/v1/auth/operations/{operation['operationId']}/complete"
        forged = await client.post(complete_path, json={"success": True})
        assert forged.status_code == 422
        not_ready = await client.post(complete_path, json={})
        assert (
            not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "AUTH_NOT_READY"
        )
        assert "private-not-ready" not in not_ready.text and not_ready.headers["Retry-After"] == "1"
        assert (await session(client))["viewer"]["kind"] == "guest"
        client.headers.pop("X-Xiaohe-Auth-CSRF")
        assert (await client.post(complete_path, json={})).status_code == 403
        async with client_for(pg_factory) as (other, other_app):
            other_app.state.xiaohe_settings = app.state.xiaohe_settings
            other_app.state.xiaohe_sso = app.state.xiaohe_sso
            await auth_setup(other, other_app)
            assert (await other.post(complete_path, json={})).status_code == 403


async def test_disabled_bridge_and_wrong_origin_fail_closed(pg_factory):
    async with client_for(pg_factory) as (client, app):
        assert (await client.post("/ai-api/v1/auth/context", json={})).status_code == 503
        await auth_setup(client, app)
        client.headers["Origin"] = "https://evil.example"
        assert (await client.post("/ai-api/v1/auth/context", json={})).status_code == 403


async def test_cancel_is_idempotent_and_never_allows_late_completion(pg_factory):
    from xiaohe.auth_bridge import SsoBridgeClient

    async with client_for(pg_factory) as (client, app):
        await auth_setup(client, app)
        created = (await client.post("/ai-api/v1/auth/operations", json=operation_body())).json()
        calls = []

        def center(request):
            calls.append(request)
            assert str(request.url).endswith("/cancel")
            return httpx.Response(200, json={"code": 0, "msg": "", "data": True})

        app.state.xiaohe_sso = SsoBridgeClient(
            app.state.xiaohe_settings, transport=httpx.MockTransport(center)
        )
        path = f"/ai-api/v1/auth/operations/{created['operationId']}"
        assert (await client.post(path + "/cancel", json={})).status_code == 204
        assert (await client.post(path + "/cancel", json={})).status_code == 204
        assert (await client.post(path + "/complete", json={})).status_code == 409
        assert len(calls) == 1


@pytest.mark.parametrize("valid", [True, False])
async def test_captcha_check_uses_contract_response_and_rejects_failed_puzzle(pg_factory, valid):
    from xiaohe.auth_bridge import SsoBridgeClient

    async with client_for(pg_factory) as (client, app):
        await auth_setup(client, app)
        created = (await client.post("/ai-api/v1/auth/operations", json=operation_body())).json()

        def center(request):
            assert str(request.url).endswith("/captcha/check")
            assert json.loads(request.content) == {
                "captchaType": "blockPuzzle",
                "token": "fixture-picture",
                "pointJson": "fixture-point",
            }
            return httpx.Response(
                200, json={"code": 0, "data": {"repCode": "0000" if valid else "0011"}}
            )

        app.state.xiaohe_sso = SsoBridgeClient(
            app.state.xiaohe_settings, transport=httpx.MockTransport(center)
        )
        response = await client.post(
            f"/ai-api/v1/auth/operations/{created['operationId']}/captcha/check",
            json={"token": "fixture-picture", "pointJson": "fixture-point"},
        )
        if valid:
            assert response.status_code == 200
            assert response.json() == {"valid": True}
        else:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "INPUT_INVALID"
        assert (await session(client))["viewer"]["kind"] == "guest"
