from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from test_auth_operations import auth_setup, operation_body
from test_http_api import client_for, session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_sms_uncertain_result_reuses_durable_key_and_blocks_old_send(pg_factory, caplog):
    from xiaohe.auth_bridge import SsoBridgeClient

    async with client_for(pg_factory) as (client, app):
        await session(client)
        _, seen = await auth_setup(client, app)
        created = (
            await client.post("/ai-api/v1/auth/operations", json=operation_body("register"))
        ).json()
        path = f"/ai-api/v1/auth/operations/{created['operationId']}"
        requests, sends = [], {}
        drop = True
        limited = True
        txn = "00000000-0000-4000-8000-000000000010"

        def center(request):
            nonlocal drop, limited
            requests.append(request)
            if str(request.url).endswith("/sms"):
                key = request.headers["Idempotency-Key"]
                if key not in sends:
                    sends[key] = "challenge-" + str(len(sends) + 1)
                if drop:
                    drop = False
                    raise httpx.ReadError("controlled lost response", request=request)
                if limited:
                    limited = False
                    return httpx.Response(
                        429,
                        json={"code": 1002029010, "msg": "", "data": None},
                        headers={"Retry-After": "60"},
                    )
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "",
                        "data": {
                            "transactionId": txn,
                            "stage": "SMS_SENT",
                            "challengeId": sends[key],
                            "expiresAt": (datetime.now(UTC) + timedelta(seconds=600)).isoformat(),
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "",
                    "data": {
                        "repCode": "9999",
                        "repMsg": "private-captcha-failure",
                        "repData": None,
                    },
                },
            )

        app.state.xiaohe_sso = SsoBridgeClient(
            app.state.xiaohe_settings, transport=httpx.MockTransport(center)
        )
        body = {"mobile": "13800138000", "captchaVerification": "private-captcha-proof-A"}
        assert (await client.post(path + "/sms", json=body)).status_code == 503
        changed = {**body, "captchaVerification": "private-captcha-proof-B"}
        assert (
            await client.post(path + "/sms", json={**changed, "mobile": "13900139000"})
        ).status_code == 409
        assert (await client.post(path + "/sms", json=changed)).status_code == 429
        fresh = {**body, "captchaVerification": "private-captcha-proof-C"}
        recovered = await client.post(path + "/sms", json=fresh)
        assert recovered.status_code == 200 and recovered.json()["challengeId"] == "challenge-1"
        assert recovered.json()["retryAfterSeconds"] <= 60
        assert datetime.fromisoformat(
            recovered.json()["expiresAt"].replace("Z", "+00:00")
        ) <= datetime.now(UTC) + timedelta(seconds=300)
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
        assert (
            await client.post(
                path + "/sms", json={**body, "captchaVerification": "private-captcha-proof-D"}
            )
        ).json()["challengeId"] == "challenge-1"
        from xiaohe.models import AuthOperationRecord

        async with pg_factory.begin() as db:
            from uuid import UUID

            operation = await db.get(AuthOperationRecord, UUID(created["operationId"]))
            operation.sms_started_at = datetime.now(UTC) - timedelta(seconds=61)
        resent = await client.post(
            path + "/sms", json={**body, "captchaVerification": "private-captcha-proof-E"}
        )
        assert resent.status_code == 200 and resent.json()["challengeId"] == "challenge-2"
        count = len(requests)
        assert (await client.post(path + "/sms", json=body)).status_code == 409
        assert len(requests) == count and len(sends) == 2
        failed_captcha = await client.post(
            path + "/captcha/check", json={"token": "private-token", "pointJson": "private-point"}
        )
        assert (
            failed_captcha.status_code == 422
            and "private-captcha-failure" not in failed_captcha.text
        )
        async with pg_factory() as db:
            rows = (
                (await db.execute(text("SELECT to_jsonb(a)::text FROM xiaohe.auth_operations a")))
                .scalars()
                .all()
            )
        saved = "".join(rows) + caplog.text
        for secret in [
            body["mobile"],
            body["captchaVerification"],
            changed["captchaVerification"],
            "private-token",
            "private-point",
        ]:
            assert secret not in saved


async def test_verify_agreement_credential_is_response_only_and_not_chat(pg_factory):
    from xiaohe.auth_bridge import SsoBridgeClient
    from xiaohe.models import Message

    async with client_for(pg_factory) as (client, app):
        await session(client)
        await auth_setup(client, app)
        op = (
            await client.post("/ai-api/v1/auth/operations", json=operation_body("register"))
        ).json()
        path = f"/ai-api/v1/auth/operations/{op['operationId']}"

        def center(request):
            agreement = str(request.url).endswith("/agreement")
            data = {
                "transactionId": "00000000-0000-4000-8000-000000000010",
                "stage": "COMPLETION_READY" if agreement else "AGREEMENT_REQUIRED",
                "expiresAt": (datetime.now(UTC) + timedelta(seconds=500)).isoformat(),
                "agreementVersion": "v-current",
                "agreementPath": "/sso/agreement/current",
            }
            if agreement:
                data.update(
                    credential="private-browser-credential",
                    credentialExpiresAt=(datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                )
            return httpx.Response(200, json={"code": 0, "msg": "", "data": data})

        app.state.xiaohe_sso = SsoBridgeClient(
            app.state.xiaohe_settings, transport=httpx.MockTransport(center)
        )
        verified = await client.post(
            path + "/verify",
            json={"mobile": "13800138000", "challengeId": "challenge-1", "code": "654321"},
        )
        assert verified.status_code == 200 and verified.json()["stage"] == "AGREEMENT_REQUIRED"
        assert (
            await client.post(path + "/agreement", json={"version": "v-current", "accepted": False})
        ).status_code == 422
        completed = await client.post(
            path + "/agreement", json={"version": "v-current", "accepted": True}
        )
        assert (
            completed.json()["completion"]["fields"]["credential"] == "private-browser-credential"
        )
        assert "browserVerifier" not in completed.json()["completion"]["fields"]
        async with pg_factory() as db:
            assert not (await db.scalars(select(Message))).all()
            saved = "".join(
                (
                    await db.execute(text("SELECT to_jsonb(a)::text FROM xiaohe.auth_operations a"))
                ).scalars()
            )
        assert (
            "private-browser-credential" not in saved
            and "654321" not in saved
            and "13800138000" not in saved
        )
