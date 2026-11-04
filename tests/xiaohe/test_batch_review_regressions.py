import asyncio
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, text
from test_admission import send, visitor
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import login

from xiaohe.jobs import RUN_QUEUE, build_job_app
from xiaohe.models import Command, Run, Session, User
from xiaohe.settings import Settings
from xiaohe.toolkit_client import ToolkitClient
from xiaohe.worker import RunWorker

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("local_rollback", [False, True])
async def test_first_complete_cannot_replace_a_new_account_from_another_tab(
    pg_factory, job_app, monkeypatch, local_rollback
):
    import xiaohe.claim as claims

    async with client_for(pg_factory) as (client, app):
        await session(client)
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        if local_rollback:
            original = claims.load_snapshot

            async def broken(*args):
                raise RuntimeError("controlled rollback")

            monkeypatch.setattr(claims, "load_snapshot", broken)
            with pytest.raises(RuntimeError):
                await client.post(path, json={})
            monkeypatch.setattr(claims, "load_snapshot", original)
        other, other_token = await login(pg_factory, sub="another-tab-account")
        client.cookies.set("__Host-XIAOHESESSION", other_token, domain="portal.lvh.me", path="/")
        result = await client.post(path, json={})
        assert result.status_code == 409 and result.json()["error"]["code"] == "IDENTITY_MISMATCH"
        assert "set-cookie" not in result.headers
        assert client.cookies.get("__Host-XIAOHESESSION") == other_token
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_first_complete_preserves_an_intentional_switch_from_its_original_account(
    pg_factory, job_app
):
    original, original_token = await login(pg_factory, sub="original-account")
    async with client_for(pg_factory) as (client, app):
        client.cookies.set("__Host-XIAOHESESSION", original_token, domain="portal.lvh.me", path="/")
        operation_id, calls = await prepare(client, app)
        result = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert result.status_code == 200
        assert result.json()["viewer"]["userId"] != original.user_id
        assert result.json()["merge"] is None and len(calls) == 1


async def test_logout_revokes_a_new_unbound_auth_context_too(pg_factory, job_app):
    original, token = await login(pg_factory)
    async with client_for(pg_factory) as (client, app):
        client.cookies.set("__Host-XIAOHESESSION", token, domain="portal.lvh.me", path="/")
        await session(client)
        operation_id, calls = await prepare(client, app)
        old_cookies = httpx.Cookies(client.cookies)
        assert (await client.post("/ai-api/v1/auth/logout", json={})).status_code == 204
        client.cookies = old_cookies
        result = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert result.status_code in (403, 409) and calls == []


async def test_api_unavailability_waits_without_spending_run_attempts(pg_factory, pg_url, job_app):
    release, probed = asyncio.Event(), asyncio.Event()
    invocations = []

    async def server(request):
        if request.method == "GET":
            probed.set()
            return httpx.Response(
                200 if release.is_set() else 503,
                json={"status": "ready" if release.is_set() else "not_ready"},
            )
        invocations.append(request)
        raise httpx.ConnectError("API is starting", request=request)

    worker = RunWorker(
        pg_factory,
        ToolkitClient(
            Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth"),
            transport=httpx.MockTransport(server),
        ),
    )
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "first")
    app = build_job_app(pg_url, worker.execute_run)
    async with app.open_async():
        running = asyncio.create_task(
            app.run_worker_async(
                queues=[RUN_QUEUE],
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
                shutdown_graceful_timeout=0,
            )
        )
        try:
            await asyncio.sleep(4)
            async with pg_factory() as db:
                command = await db.scalar(select(Command))
                run = await db.get(Run, UUID(accepted.run.id))
                assert command.attempts == 0 and run.status == "queued"
                assert (
                    await db.scalar(
                        text("SELECT status FROM procrastinate_jobs WHERE id=:id"),
                        {"id": run.job_id},
                    )
                    == "doing"
                )
            assert probed.is_set() and invocations == []
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_api_ready_wait_still_enforces_expiry_and_does_not_invoke(pg_factory, job_app):
    async def unavailable(request):
        assert request.method == "GET"
        return httpx.Response(503, json={"status": "not_ready"})

    worker = RunWorker(
        pg_factory,
        ToolkitClient(
            Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth"),
            transport=httpx.MockTransport(unavailable),
        ),
    )
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "first")
    from sqlalchemy import update

    async with pg_factory.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.id == who.session_id)
            .values(expires_at=func.clock_timestamp() + text("interval '0.2 seconds'"))
        )
    await asyncio.wait_for(worker.execute_run(UUID(accepted.run.id)), 5)
    async with pg_factory() as db:
        assert (await db.get(Run, UUID(accepted.run.id))).status == "expired"
        assert (await db.scalar(select(Command))).attempts == 0


async def test_ready_probe_releases_a_run_as_soon_as_api_recovers(
    pg_factory, runtime_env, monkeypatch
):
    ready = asyncio.Event()
    observed = []
    real = httpx.ASGITransport(app=runtime_env.app)

    async def transport(request):
        observed.append(request.method)
        if request.method == "GET":
            return httpx.Response(200 if ready.is_set() else 503, json={"status": "ready"})
        return await real.handle_async_request(request)

    worker = RunWorker(
        pg_factory, ToolkitClient(runtime_env.settings, transport=httpx.MockTransport(transport))
    )
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "first")
    running = asyncio.create_task(worker.execute_run(UUID(accepted.run.id)))
    try:
        await asyncio.sleep(1.1)
        ready.set()
        await asyncio.wait_for(running, 5)
        async with pg_factory() as db:
            assert (await db.get(Run, UUID(accepted.run.id))).status == "completed"
            assert (await db.scalar(select(Command))).attempts == 1
        assert observed.count("POST") == 1 and observed.count("GET") >= 2
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
