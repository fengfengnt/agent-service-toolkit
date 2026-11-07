from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func

from xiaohe.api import create_public_router, install_error_handlers
from xiaohe.models import GuestBudget, Session
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@asynccontextmanager
async def client_for(factory):
    app = FastAPI()
    app.state.xiaohe_settings = Settings(
        toolkit_base_url="http://runtime",
        auth_secret="fixture-auth",
        trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    install_error_handlers(app)
    app.include_router(create_public_router(factory))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://portal.lvh.me:9444",
        headers={"X-Xiaohe-System-Id": "portal", "Origin": "https://portal.lvh.me:9444"},
    ) as client:
        yield client, app


async def session(client):
    response = await client.post("/ai-api/v1/session", json={})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    client.headers[snapshot["csrf"]["headerName"]] = snapshot["csrf"]["token"]
    return snapshot


async def test_rest_admission_snapshot_cancel_and_safe_validation(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}"
        accepted = await client.post(
            path + "/messages", json={"clientMessageId": "m1", "text": "你好 😀"}
        )
        assert accepted.status_code == 202
        value = accepted.json()
        assert value["message"]["parts"] == [{"type": "text", "text": "你好 😀"}]
        assert value["viewer"]["messageCount"] == 1 and "cursor" not in value
        current = await client.get(path)
        assert current.headers.get("cache-control") == "private, no-store"
        assert current.json()["cursor"] and current.json()["activeRun"]["id"] == value["run"]["id"]
        cancelled = await client.post(f"/ai-api/v1/runs/{value['run']['id']}/cancel", json={})
        assert cancelled.json()["status"] == "cancelled"
        bad = await client.post(
            path + "/actions",
            json={"clientActionId": "a1", "requestId": "q1", "text": "x", "otp": "private-otp"},
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "INPUT_INVALID"
        assert "private-otp" not in bad.text and "detail" not in bad.json()
        broken = await client.get("/ai-api/v1/conversations/private-secret-not-a-uuid")
        assert broken.status_code == 422 and "private-secret" not in broken.text


async def test_public_cookie_csrf_and_internal_bearer_are_separate(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}/messages"
        client.headers.pop("X-Xiaohe-CSRF", None)
        missing = await client.post(
            path,
            json={"clientMessageId": "m1", "text": "hello"},
            headers={"Authorization": "Bearer fixture-auth"},
        )
        assert missing.status_code == 403 and missing.json()["error"]["code"] == "CSRF_INVALID"
        client.cookies.clear()
        unauthenticated = await client.post(
            path,
            json={"clientMessageId": "m1", "text": "hello"},
            headers={"Authorization": "Bearer fixture-auth"},
        )
        assert unauthenticated.status_code == 401


@pytest.mark.parametrize("version", ["1", "2"])
async def test_unexpected_server_failure_is_safe_json(pg_factory, monkeypatch, version):
    import xiaohe.api as api

    async def broken(*args):
        raise RuntimeError("private-database-credential")

    monkeypatch.setattr(api, "_actor", broken)
    async with client_for(pg_factory) as (_, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://portal.lvh.me:9444",
        ) as client:
            response = await client.get(
                "/ai-api/v1/conversations/00000000-0000-4000-8000-000000000001",
                headers={"X-Xiaohe-View-Version": version},
            )
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    )
    assert "private-database" not in response.text
    assert response.headers["X-Xiaohe-View-Version"] == version


async def test_sse_expiry_precedes_stream_and_new_episode_preserves_budget(pg_factory, job_app):
    async with client_for(pg_factory) as (client, app):
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}/events"
        conflict = await client.get(path, params={"after": "one"}, headers={"Last-Event-ID": "two"})
        assert conflict.status_code == 409 and conflict.headers["content-type"].startswith(
            "application/json"
        )
        async with pg_factory.begin() as db:
            from sqlalchemy import update

            await db.execute(
                update(Session).values(expires_at=func.clock_timestamp(), message_count=20)
            )
            await db.execute(update(GuestBudget).values(message_count=20))
        expired = await client.get(path)
        assert expired.status_code == 410 and expired.json()["error"]["code"] == "VISITOR_EXPIRED"
        assert "set-cookie" not in expired.headers
        reopened = await client.post("/ai-api/v1/session", json={})
        assert reopened.status_code == 200 and reopened.json()["viewer"]["messageCount"] == 20
        assert reopened.json()["conversationId"] != snapshot["conversationId"]


async def test_disconnect_does_not_cancel_run(pg_factory, runtime_env, monkeypatch):
    import asyncio
    import json

    from agents.agents import agents
    from xiaohe.contracts import AgentOutcome
    from xiaohe.graph import build_graph
    from xiaohe.identity import SESSION_COOKIE

    started, release = asyncio.Event(), asyncio.Event()

    async def handler(state):
        started.set()
        await release.wait()
        return AgentOutcome(
            parts=[{"type": "text", "text": "后台继续完成 😀"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    async with client_for(pg_factory) as (client, app):
        snapshot = await session(client)
        path = f"/ai-api/v1/conversations/{snapshot['conversationId']}"
        accepted = (
            await client.post(path + "/messages", json={"clientMessageId": "m1", "text": "你好 😀"})
        ).json()
        worker = asyncio.create_task(runtime_env.worker.execute_run(UUID(accepted["run"]["id"])))
        frames = []
        disconnected = asyncio.Event()
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send_frame(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
            if message["type"] == "http.response.body" and message.get("body"):
                frames.append(message["body"])
                disconnected.set()

        try:
            await asyncio.wait_for(started.wait(), 5)
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "https",
                "root_path": "",
                "path": path + "/events",
                "query_string": b"",
                "server": ("portal.lvh.me", 9444),
                "client": ("127.0.0.1", 12345),
                "headers": [
                    (b"host", b"portal.lvh.me:9444"),
                    (b"x-xiaohe-system-id", b"portal"),
                    (b"cookie", f"{SESSION_COOKIE}={client.cookies.get(SESSION_COOKIE)}".encode()),
                ],
            }
            await asyncio.wait_for(app(scope, receive, send_frame), 5)
            assert frames and not worker.done()
            text = frames[0].decode("utf-8")
            fields = dict(line.split(": ", 1) for line in text.strip().split("\n"))
            event = json.loads(fields["data"])
            assert fields["id"] == event["eventId"] and fields["event"] == event["type"]
            release.set()
            await asyncio.wait_for(worker, 5)
            result = (await client.get(path)).json()
            assert result["activeRun"] is None
            assert result["messages"][-1]["parts"] == [{"type": "text", "text": "后台继续完成 😀"}]
        finally:
            release.set()
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
