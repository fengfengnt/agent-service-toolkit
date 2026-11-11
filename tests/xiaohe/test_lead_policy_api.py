import pytest
from test_application_forms import FormBackend
from test_http_api import client_for, session
from test_identity_sessions import login

from xiaohe.identity import SESSION_COOKIE
from xiaohe.reception.api import create_reception_router

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
HEADER = "X-Xiaohe-Lead-Policy"


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_capable_client_gets_effective_policy(pg_factory, role):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        path = "/ai-api/v1/reception/conversations/" + snapshot["conversationId"]
        empty = await client.get(path, headers={HEADER: "1"})
        assert "leadPolicy" in empty.json(), "opt-in policy projection missing"
        assert empty.json()["leadPolicy"] is None
        await client.post(
            path + "/drafts", json={"clientDraftId": "one", "kind": "service", "direction": role}
        )
        old = (await client.get(path)).json()
        new = (await client.get(path, headers={HEADER: "1"})).json()
        policy = new.pop("leadPolicy")
        assert new == old
        assert policy["direction"] == role and policy["version"] == 1
        assert policy["requiredFields"] == old["activeDraft"]["fieldLayout"]["requiredFields"]
        assert policy["rounds"] == old["activeDraft"]["fieldLayout"]["rounds"]
        assert len(policy["fieldRuleVersion"]) == len(policy["interactionVersion"]) == 64
        assert (
            "companyName" in {f["id"] for f in policy["fields"]}
            if role != "organization"
            else "organizationName" in {f["id"] for f in policy["fields"]}
        )
        assert policy["notices"]["accepted"] == "本次需求已受理，可在“我的进展”查看状态。"
        client.cookies.clear()
        assert (await client.get(path, headers={HEADER: "1"})).status_code == 401
        await session(client)
        assert (await client.get(path, headers={HEADER: "1"})).status_code == 403


async def test_legacy_read_shape_is_unchanged(pg_factory):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        path = "/ai-api/v1/reception/conversations/" + snapshot["conversationId"]
        expected = {
            "version": 1,
            "conversationId": snapshot["conversationId"],
            "direction": None,
            "invitationState": "none",
            "pendingRequestId": None,
            "draftRefs": [],
            "activeDraft": None,
        }
        for headers in ({}, {HEADER: "3"}, [(HEADER, "1"), (HEADER, "1")]):
            assert (await client.get(path, headers=headers)).json() == expected


async def test_form_projection_matches_actual_form_rules(pg_factory):
    _, token = await login(pg_factory)
    backend = FormBackend()
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, backend))
        client.cookies.set(SESSION_COOKIE, token)
        await session(client)
        path = "/ai-api/v1/reception/application-form?sourceApplicationId=10&mode=edit"
        old = (await client.get(path)).json()
        assert set(old) == {
            "sourceApplicationId",
            "mode",
            "subjectId",
            "direction",
            "expectedApplicationRevision",
            "definitionBundleId",
            "fieldLayout",
            "fields",
            "files",
        }
        response = await client.get(path, headers={HEADER: "1"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert "leadPolicy" in data, "standalone form policy missing"
        policy = data.pop("leadPolicy")
        assert data == old
        assert policy["requiredFields"] == old["fieldLayout"]["requiredFields"]
        assert policy["rounds"] == old["fieldLayout"]["rounds"]
        assert backend.created == 0
