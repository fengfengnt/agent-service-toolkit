from uuid import uuid4

import pytest
from test_application_forms import FormBackend, counts
from test_http_api import client_for, session
from test_identity_sessions import login

from xiaohe.identity import SESSION_COOKIE
from xiaohe.reception.api import create_reception_router

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
HEADER = "X-Xiaohe-Lead-Policy"


async def test_full_v2_fixture(contract_fixture):
    from xiaohe.deep_runtime.models import FormViewV2

    fixture = contract_fixture("lead-policy-v2")
    assert FormViewV2.model_validate(fixture).model_dump(mode="json", by_alias=True) == fixture


async def test_v2_headers_and_empty_chat(pg_factory):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        state = await session(client)
        path = "/ai-api/v1/reception/conversations/" + state["conversationId"]
        legacy = (await client.get(path)).json()
        assert (await client.get(path, headers={HEADER: "2"})).json() == legacy | {
            "leadPolicy": None
        }
        for header in ({HEADER: "3"}, [(HEADER, "2"), (HEADER, "2")], {HEADER: "2, 2"}):
            assert (await client.get(path, headers=header)).json() == legacy
        draft = (
            await client.post(
                path + "/drafts",
                json={"clientDraftId": "test", "direction": "buyer", "kind": "service"},
            )
        ).json()
        v2 = (await client.get(path, headers={HEADER: "2"})).json()["leadPolicy"]
        assert v2["version"] == 2 and v2["scope"] == "draft"
        patch = await client.patch(
            "/ai-api/v1/reception/drafts/" + draft["id"] + "/form",
            json={
                "formId": v2["formId"],
                "expectedRevision": draft["revision"],
                "fields": {"city": "苏州"},
            },
        )
        assert patch.status_code == 200, patch.text


async def test_standalone_v2_has_no_chat_side_effects_and_keeps_upload_form_id(pg_factory):
    _, token = await login(pg_factory)
    backend = FormBackend()
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, backend))
        client.cookies.set(SESSION_COOKIE, token)
        await session(client)
        before = await counts(pg_factory)
        path = "/ai-api/v1/reception/application-form?sourceApplicationId=10&mode=edit"
        legacy = (await client.get(path)).json()
        value = (await client.get(path, headers={HEADER: "2"})).json()
        policy = value.pop("leadPolicy")
        assert (
            value == legacy
            and policy["scope"] == "application"
            and policy["applicationRevision"] == 3
        )
        assert await counts(pg_factory) == before
        for headers in ({HEADER: "3"}, [(HEADER, "2"), (HEADER, "2")]):
            assert (await client.get(path, headers=headers)).json() == legacy
        new = (
            await client.get(path.replace("mode=edit", "mode=new"), headers={HEADER: "2"})
        ).json()
        assert (
            new["leadPolicy"]["applicationRevision"] == 0
            and new["expectedApplicationRevision"] is None
        )
        body = {
            "formId": str(uuid4()),
            "policyFormId": policy["formId"],
            "clientSubmissionId": "v2-submit",
            "sourceApplicationId": "10",
            "mode": "edit",
            "expectedApplicationRevision": 3,
            "definitionBundleId": legacy["definitionBundleId"],
            "fields": {"productDescription": "新说明"},
            "files": [],
        }
        invalid = await client.post(
            "/ai-api/v1/reception/application-form/submissions",
            headers={HEADER: "2"},
            json=body | {"policyFormId": "guessed"},
        )
        assert invalid.status_code == 409 and invalid.json()["error"]["code"] == "ACTION_STALE"
        response = await client.post(
            "/ai-api/v1/reception/application-form/submissions", headers={HEADER: "2"}, json=body
        )
        assert response.status_code == 200, response.text
        assert backend.created == 1 and await counts(pg_factory) == before
