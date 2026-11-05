import pytest
from test_http_api import client_for, session

from xiaohe.reception.api import create_reception_router

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_draft_routes_share_auth_csrf_and_safe_errors(pg_factory):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        path = "/ai-api/v1/reception/conversations/" + snapshot["conversationId"]
        create = await client.post(
            path + "/drafts", json={"clientDraftId": "one", "kind": "service", "direction": "buyer"}
        )
        assert create.status_code == 201, create.text
        draft = create.json()
        assert "sourceApplicationId" not in draft and draft["missingRequired"] == ["companyName"]
        endpoint = "/ai-api/v1/reception/drafts/" + draft["id"]
        result = await client.patch(
            endpoint, json={"expectedRevision": 1, "fields": {"companyName": "甲公司"}}
        )
        assert result.status_code == 200 and result.json()["step"] == "round1"
        result = await client.patch(
            endpoint, json={"expectedRevision": 1, "fields": {"companyName": "旧资料"}}
        )
        assert result.status_code == 409 and result.json()["error"]["code"] == "DRAFT_CONFLICT"
        result = await client.patch(
            endpoint, json={"expectedRevision": 2, "fields": {}, "userId": "forged-secret"}
        )
        assert result.status_code == 422 and "forged-secret" not in result.text
        del client.headers[snapshot["csrf"]["headerName"]]
        assert (
            await client.patch(endpoint, json={"expectedRevision": 2, "fields": {}})
        ).status_code == 403
        view = await client.get(path)
        assert view.status_code == 200 and view.headers["cache-control"] == "private, no-store"
        assert view.json()["activeDraft"]["fields"] == {"companyName": "甲公司"}
        client.cookies.clear()
        assert (await client.get(path)).status_code == 401
