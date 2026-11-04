import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from test_admission import send
from test_guest_claim import prepare
from test_http_api import client_for, session
from test_identity_sessions import actor, login

from xiaohe.contracts import AgentOutcome
from xiaohe.models import AuthOperationRecord, GuestClaim, Message, Run, Session, User

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_parallel_first_completion_has_one_identity_claim_and_absolute_anchor(
    pg_factory, job_app
):
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        await send(pg_factory, who, UUID(guest["conversationId"]), "first")
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        results = await asyncio.gather(*[client.post(path, json={}) for _ in range(5)])
        assert all(r.status_code == 200 for r in results)
        assert len({r.json()["viewer"]["userId"] for r in results}) == 1
        assert len(calls) == 1
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1
            assert await db.scalar(select(func.count()).select_from(GuestClaim)) == 1
            anchors = (
                await db.scalars(select(Session.created_at).where(Session.user_id.is_not(None)))
            ).all()
            assert len(set(anchors)) == 1
            assert await db.scalar(select(func.count()).select_from(Message)) == 1 + len(
                guest["messages"]
            )


async def test_original_context_recovery_rejects_other_account_and_fixed_deadlines(
    pg_factory, job_app
):
    async with client_for(pg_factory) as (client, app):
        operation_id, calls = await prepare(client, app)
        original = httpx.Cookies(client.cookies)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        assert (await client.post(path, json={})).status_code == 200
        _, different = await login(pg_factory, sub="different-account")
        client.cookies.set("__Host-XIAOHESESSION", different, domain="portal.lvh.me", path="/")
        assert (await client.post(path, json={})).status_code == 403
        client.cookies = original
        async with pg_factory.begin() as db:
            operation = await db.get(AuthOperationRecord, UUID(operation_id))
            operation.authenticated_session_created_at = datetime.now(UTC) - timedelta(
                hours=8, seconds=1
            )
        assert (await client.post(path, json={})).status_code == 401
        async with pg_factory.begin() as db:
            operation = await db.get(AuthOperationRecord, UUID(operation_id))
            operation.authenticated_session_created_at = datetime.now(UTC)
            operation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (await client.post(path, json={})).status_code == 410
        assert len(calls) == 1


async def test_waiting_target_keeps_checkpoint_and_question_after_merge(
    pg_factory, job_app, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.admission import accept_action
    from xiaohe.conversations import default_conversation
    from xiaohe.graph import build_graph

    async def handler(state):
        if len(state["messages"]) > 1:
            return AgentOutcome(
                parts=[{"type": "text", "text": "原问题已接续"}],
                wait_for_input=False,
                artifact_version_ids=[],
            )
        return AgentOutcome(
            parts=[
                {
                    "type": "choice",
                    "requestId": "old-question",
                    "prompt": "请确认",
                    "options": [{"id": "yes", "label": "确认"}],
                }
            ],
            wait_for_input=True,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    _, token = await login(pg_factory)
    member = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        canonical = await default_conversation(db, member)
    target = await send(pg_factory, member, canonical.id, "target")
    await runtime_env.worker.execute_run(UUID(target.run.id))
    async with pg_factory() as db:
        old_thread = (await db.get(Run, UUID(target.run.id))).thread_id
    async with client_for(pg_factory) as (client, app):
        await session(client)
        operation_id, calls = await prepare(client, app)
        merged = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert merged.status_code == 200
        updated = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        async with pg_factory.begin() as db:
            waiting = await db.get(Run, UUID(target.run.id))
            assert waiting.status == "waiting_input" and not waiting.holds_slot
            assert (
                waiting.pending_request["requestId"] == "old-question"
                and waiting.thread_id == old_thread
            )
            accepted = await accept_action(
                db, updated, canonical.id, "answer", "old-question", "yes", None
            )
            assert accepted.run.id == target.run.id
        await runtime_env.worker.execute_run(UUID(target.run.id))
        async with pg_factory() as db:
            assert (await db.get(Run, UUID(target.run.id))).status == "completed"


async def test_source_action_rebuild_uses_latest_input_and_a_new_user_namespace(
    pg_factory, job_app, runtime_env, monkeypatch
):
    from agents.agents import agents
    from xiaohe.admission import accept_action
    from xiaohe.graph import build_graph
    from xiaohe.models import Command

    async def handler(state):
        if state["messages"][-1].content == "确认":
            return AgentOutcome(
                parts=[{"type": "text", "text": "接续完成"}],
                wait_for_input=False,
                artifact_version_ids=[],
            )
        return AgentOutcome(
            parts=[
                {
                    "type": "choice",
                    "requestId": "guest-question",
                    "prompt": "请确认",
                    "options": [{"id": "yes", "label": "确认"}],
                }
            ],
            wait_for_input=True,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)
    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        original = await send(pg_factory, who, UUID(guest["conversationId"]), "first")
        await runtime_env.worker.execute_run(UUID(original.run.id))
        async with pg_factory.begin() as db:
            old_thread = (await db.get(Run, UUID(original.run.id))).thread_id
            accepted = await accept_action(
                db, who, UUID(guest["conversationId"]), "answer", "guest-question", "yes", None
            )
        operation_id, _ = await prepare(client, app)
        response = await client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
        assert response.status_code == 200
        async with pg_factory() as db:
            claim = await db.scalar(select(GuestClaim))
            continuation = await db.get(Run, claim.continuation_run_id)
            command = await db.scalar(select(Command).where(Command.run_id == continuation.id))
            assert command.message_id == UUID(accepted.message.id)
            assert command.question["requestId"] == "guest-question"
            assert (
                continuation.user_id == response.json()["viewer"]["userId"]
                and continuation.guest_id is None
            )
        await runtime_env.worker.execute_run(continuation.id)
        async with pg_factory() as db:
            resumed = await db.get(Run, continuation.id)
            assert resumed.status == "completed" and resumed.thread_id != old_thread


async def test_claim_blocked_until_after_deadline_skips_expired_source(pg_factory, job_app):
    from sqlalchemy import text, update

    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "first")
        operation_id, calls = await prepare(client, app)
        async with pg_factory.begin() as db:
            await db.execute(
                update(Session)
                .where(Session.id == who.session_id)
                .values(expires_at=func.clock_timestamp() + text("interval '0.2 seconds'"))
            )
        async with pg_factory() as holder:
            async with holder.begin():
                await holder.scalar(
                    select(Session).where(Session.id == who.session_id).with_for_update()
                )
                pending = asyncio.create_task(
                    client.post(f"/ai-api/v1/auth/operations/{operation_id}/complete", json={})
                )
                await asyncio.sleep(0.3)
                assert not pending.done()
            result = await asyncio.wait_for(pending, 5)
        assert result.status_code == 200 and result.json()["merge"]["status"] == "SKIPPED_EXPIRED"
        messages = result.json()["messages"]
        source_ids = {turn.message.id, *[m["id"] for m in guest["messages"]]}
        assert len(messages) == 1 and messages[0]["role"] == "assistant"
        assert messages[0]["id"] not in source_ids and len(calls) == 1
        assert any(p["type"] == "suggestions" for p in messages[0]["parts"])


async def test_local_commit_failure_reuses_verified_result_and_keeps_source_intact(
    pg_factory, job_app, monkeypatch
):
    import xiaohe.claim as claims

    async with client_for(pg_factory) as (client, app):
        guest = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        turn = await send(pg_factory, who, UUID(guest["conversationId"]), "first")
        operation_id, calls = await prepare(client, app)
        path = f"/ai-api/v1/auth/operations/{operation_id}/complete"
        original = claims.load_snapshot

        async def broken(*args):
            raise RuntimeError("controlled local commit abort")

        monkeypatch.setattr(claims, "load_snapshot", broken)
        with pytest.raises(RuntimeError, match="controlled local commit abort"):
            await client.post(path, json={})
        async with pg_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 0
            assert await db.scalar(select(func.count()).select_from(GuestClaim)) == 0
            assert (await db.get(Session, who.session_id)).revoked_at is None
            assert (await db.get(Message, UUID(turn.message.id))).conversation_id == UUID(
                guest["conversationId"]
            )
            assert (
                await db.get(AuthOperationRecord, UUID(operation_id))
            ).verified_completion is not None
        monkeypatch.setattr(claims, "load_snapshot", original)
        result = await client.post(path, json={})
        assert result.status_code == 200 and len(calls) == 1
