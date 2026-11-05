import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from test_http_api import client_for, session
from test_identity_sessions import login
from test_reception_business_client import configured

from xiaohe.identity import SESSION_COOKIE
from xiaohe.reception.api import create_reception_router
from xiaohe.reception.business_client import ReceptionBusinessClient

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def progress():
    cases = json.loads(
        (Path(__file__).parent / "contracts/reception-fixtures.json").read_text("utf-8")
    )
    group = next(
        case["value"] for case in cases["schemaCases"] if case["name"] == "progress-unassigned"
    )
    return {"groups": [copy.deepcopy(group)], "nextCursor": None}


async def test_unassigned_and_ungraded_are_null_and_identity_is_server_derived(pg_factory):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/internal-api/reception/v1/progress/query"
        body = json.loads(request.content)
        assert body["actor"]["sub"] == "opaque-progress"
        assert body["payload"] == {"cursor": "opaque-page"}
        return httpx.Response(200, json={"code": 0, "data": progress()})

    business = ReceptionBusinessClient(configured(), transport=httpx.MockTransport(respond))
    _, token = await login(pg_factory, sub="opaque-progress")
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, business))
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/ai-api/v1/reception/progress?cursor=opaque-page")
        assert response.status_code == 200, response.text
        item = response.json()["groups"][0]["applications"][0]
        assert item["manager"] is item["grade"] is item["expectedContactAt"] is None
        assert response.headers["cache-control"] == "private, no-store"
        assert (
            await client.get("/ai-api/v1/reception/progress?aiUserId=forged")
        ).status_code == 422
        assert len(requests) == 1


async def test_progress_filters_internal_notes_and_fails_closed(pg_factory):
    leaked = progress()
    leaked["groups"][0]["applications"][0]["internalNotes"] = "private-manager-notes"
    business = ReceptionBusinessClient(
        configured(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 0, "data": leaked})
        ),
    )
    _, token = await login(pg_factory)
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, business))
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/ai-api/v1/reception/progress")
        assert response.status_code == 503
        assert "private-manager-notes" not in response.text


async def test_logout_drops_late_progress_response(pg_factory):
    started, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"code": 0, "data": progress()})

    business = ReceptionBusinessClient(configured(), transport=httpx.MockTransport(respond))
    _, token = await login(pg_factory)
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, business))
        client.cookies.set(SESSION_COOKIE, token)
        await session(client)
        pending = asyncio.create_task(client.get("/ai-api/v1/reception/progress"))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert (await client.post("/ai-api/v1/auth/logout", json={})).status_code == 204
        finally:
            release.set()
        response = await pending
        assert response.status_code == 401 and "groups" not in response.text


async def test_subjects_require_login_and_forward_only_typed_queries(pg_factory):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/internal-api/reception/v1/subjects/query"
        assert json.loads(request.content)["payload"] == {"kind": "company"}
        return httpx.Response(200, json={"code": 0, "data": {"items": [], "nextCursor": None}})

    business = ReceptionBusinessClient(configured(), transport=httpx.MockTransport(respond))
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, business))
        await session(client)
        assert (await client.get("/ai-api/v1/reception/subjects?kind=company")).status_code == 401
        _, token = await login(pg_factory)
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, token)
        assert (await client.get("/ai-api/v1/reception/subjects?kind=member")).status_code == 422
        assert (
            await client.get("/ai-api/v1/reception/subjects?kind=company&kind=organization")
        ).status_code == 422
        response = await client.get("/ai-api/v1/reception/subjects?kind=company")
        assert response.status_code == 200 and response.json() == {"items": [], "nextCursor": None}
        assert len(requests) == 1
