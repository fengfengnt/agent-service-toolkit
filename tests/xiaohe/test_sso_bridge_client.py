import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_identity_sessions import system

from xiaohe.contracts import ServiceError

pytestmark = pytest.mark.asyncio


def settings():
    from xiaohe.settings import Settings

    return Settings(
        toolkit_base_url="http://runtime",
        auth_secret="toolkit-separate",
        sso_bridge_enabled=True,
        sso_bridge_base_url="https://sso.lvh.me:8443",
        sso_bridge_application_id="fixture-app",
        sso_bridge_secret="fixture-app-secret",
    )


@pytest.mark.parametrize(
    "http,code,want,status,retry",
    [
        (401, 1002029021, "UPSTREAM_UNAVAILABLE", 503, False),
    (401, 1002029008, "AUTH_REQUIRED", 401, False),
        (403, 1002029023, "UPSTREAM_UNAVAILABLE", 503, False),
        (409, 1002029027, "AUTH_NOT_READY", 409, True),
        (410, 1002029025, "AUTH_OPERATION_EXPIRED", 410, False),
        (409, 1002029022, "IDEMPOTENCY_CONFLICT", 409, False),
        (429, 1002029005, "AUTH_RATE_LIMITED", 429, True),
        (503, 1002029030, "UPSTREAM_UNAVAILABLE", 503, True),
    ],
)
async def test_private_errors_are_safe_and_not_false_user_success(http, code, want, status, retry):
    from xiaohe.auth_bridge import SsoBridgeClient

    def server(request):
        return httpx.Response(
            http,
            json={"code": code, "msg": "private-center-diagnostic", "data": None},
            headers={"Retry-After": "60"},
        )

    client = SsoBridgeClient(settings(), transport=httpx.MockTransport(server))
    with pytest.raises(ServiceError) as raised:
        await client.request(
            "POST",
            "/transactions",
            body={},
            key="operation-stable",
            client_ip="127.0.0.1",
            user_agent="fixture-browser",
        )
    assert (raised.value.code, raised.value.http_status, raised.value.retryable) == (
        want,
        status,
        retry,
    )
    assert "private-center" not in str(raised.value)
    if http == 429:
        assert raised.value.retry_after == 60


async def test_client_uses_separate_application_secret_fixed_path_and_no_redirect():
    from xiaohe.auth_bridge import SsoBridgeClient

    requests = []

    def server(request):
        requests.append(request)
        assert request.url == "https://sso.lvh.me:8443/internal-api/system/sso-bridge/transactions"
        assert request.headers["Authorization"] == "Bearer fixture-app-secret"
        assert request.headers["Idempotency-Key"] == "stable-create"
        assert request.headers["X-SSO-Bridge-Application-Id"] == "fixture-app"
        assert request.headers["X-Bridge-Client-IP"] == "127.0.0.1"
        assert request.headers["X-Bridge-User-Agent"] == "fixture-browser"
        assert json.loads(request.content) == {"portalClientId": "app_localportal"}
        return httpx.Response(302, headers={"Location": "https://evil.example"})

    client = SsoBridgeClient(settings(), transport=httpx.MockTransport(server))
    with pytest.raises(ServiceError):
        await client.request(
            "POST",
            "/transactions",
            body={"portalClientId": "app_localportal"},
            key="stable-create",
            client_ip="127.0.0.1",
            user_agent="fixture-browser",
        )
    assert len(requests) == 1
    assert settings().auth_secret != SecretStr("fixture-app-secret")


async def test_claim_accepts_only_six_bound_fields_and_original_transaction():
    from xiaohe.auth_bridge import SsoBridgeClient

    transaction_id = str(uuid4())
    payload = {
        "issuer": "https://sso.lvh.me:8443",
        "sub": "opaque-not-ai-user",
        "sourceTenantId": 1,
        "authenticatedAt": datetime.now(UTC).isoformat(),
        "portalClientId": "app_localportal",
        "transactionId": transaction_id,
    }

    def server(request):
        assert request.headers["Idempotency-Key"] == transaction_id
        assert str(request.url).endswith("/" + transaction_id + "/claim")
        return httpx.Response(200, json={"code": 0, "msg": "", "data": payload})

    client = SsoBridgeClient(settings(), transport=httpx.MockTransport(server))
    result = await client.claim(
        system(), transaction_id, client_ip="127.0.0.1", user_agent="fixture"
    )
    assert result.sub == "opaque-not-ai-user"
    for change in [
        {"portalClientId": "other"},
        {"transactionId": str(uuid4())},
        {"issuer": "https://evil.example"},
        {"userId": "spoofed"},
        {"authenticatedAt": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ]:
        original = payload.copy()
        payload.update(change)
        with pytest.raises(ServiceError):
            await client.claim(
                system(), transaction_id, client_ip="127.0.0.1", user_agent="fixture"
            )
        payload.clear()
        payload.update(original)


async def test_bad_rate_limit_never_becomes_untrusted_header_or_diagnostic():
    from xiaohe.auth_bridge import SsoBridgeClient

    def server(request):
        return httpx.Response(
            429,
            json={"code": 1002029010, "msg": "private", "data": None},
            headers={"Retry-After": "not-a-positive-integer"},
        )

    with pytest.raises(ServiceError) as raised:
        await SsoBridgeClient(settings(), transport=httpx.MockTransport(server)).request(
            "POST", "/transactions", body={}, client_ip="127.0.0.1", user_agent="fixture"
        )
    assert raised.value.code == "AUTH_RATE_LIMITED" and raised.value.retry_after == 60
