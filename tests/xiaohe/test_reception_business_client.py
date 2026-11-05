import json

import httpx
import pytest

from xiaohe.contracts import ServiceError
from xiaohe.reception.business_client import ReceptionBusinessClient
from xiaohe.reception.contracts import ReceptionActor, Submission
from xiaohe.settings import Settings


def configured():
    return Settings(
        toolkit_base_url="http://runtime",
        auth_secret="fixture-auth",
        reception_base_url="http://business",
        reception_application_id="portal-test",
        reception_secret="a" * 48,
    )


def trusted_actor():
    return ReceptionActor(
        ai_user_id="ad7b0631-8a69-4499-92fa-3bfbeddf5757",
        system_id="portal",
        issuer="https://sso.example",
        sub="opaque:subject",
        source_tenant_id="17",
    )


def payload():
    return Submission(
        submission_key="one",
        draft_id="draft-one",
        draft_revision=2,
        operation="create",
        subject_kind="company",
        direction="buyer",
        public_fields={"companyName": "甲公司"},
        request_fields={},
        files=[],
    )


@pytest.mark.asyncio
async def test_dedicated_credentials_and_opaque_identity_with_receipt_validation():
    requests = []

    async def respond(request):
        requests.append(request)
        assert request.headers["X-Reception-Application"] == "portal-test"
        assert request.headers["Authorization"] == "Bearer " + "a" * 48
        body = json.loads(request.content)
        assert body["actor"] == trusted_actor().model_dump(mode="json", by_alias=True)
        assert body["actor"]["sourceTenantId"] == "17" and "tenantId" not in body["actor"]
        return httpx.Response(
            201,
            json={
                "code": 0,
                "data": {
                    "submissionKey": "one",
                    "kind": "service",
                    "applicationId": "500",
                    "revision": 1,
                    "status": "accepted",
                },
            },
        )

    client = ReceptionBusinessClient(configured(), transport=httpx.MockTransport(respond))
    result = await client.submit(trusted_actor(), payload())
    assert result.application_id == "500"
    assert requests[0].url.path == "/internal-api/reception/v1/applications/submit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "http://private-leak"}),
        httpx.Response(
            200, json={"code": 0, "data": {"status": "accepted", "managerId": "forged"}}
        ),
        httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
    ],
)
async def test_redirect_malformed_or_oversized_result_is_unknown(response):
    client = ReceptionBusinessClient(
        configured(), transport=httpx.MockTransport(lambda request: response)
    )
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await client.submit(trusted_actor(), payload())


@pytest.mark.asyncio
async def test_known_conflict_and_unconfigured_client_fail_without_leaking_details():
    client = ReceptionBusinessClient(
        configured(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                409, json={"code": 1050000004, "msg": "private business text"}
            )
        ),
    )
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT") as error:
        await client.submit(trusted_actor(), payload())
    assert "private" not in str(error.value)
    client = ReceptionBusinessClient(
        Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth")
    )
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await client.submit(trusted_actor(), payload())
