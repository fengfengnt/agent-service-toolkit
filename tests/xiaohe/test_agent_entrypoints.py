from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from test_agent_handoff import install_agents
from test_http_api import client_for, session

from xiaohe.models import Command, GuestBudget, Run, Session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_direct_specialist_entry_skips_reception_model(
    pg_factory, runtime_env, monkeypatch, role
):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "consultation"}]
    )
    async with client_for(pg_factory) as (client, _):
        snapshot = await session(client)
        path = (
            f"/ai-api/v1/agents/xiaohe-{role}/conversations/{snapshot['conversationId']}/messages"
        )
        body = {"clientMessageId": "direct", "text": "想了解你们的服务方式"}
        response = await client.post(path, json=body)
        assert response.status_code == 202
        repeat = await client.post(path, json=body)
        assert repeat.json() == response.json()
        await runtime_env.worker.execute_run(UUID(response.json()["run"]["id"]))
        current = await client.get(f"/ai-api/v1/conversations/{snapshot['conversationId']}")
        assert current.status_code == 200 and current.json()["messages"][-1]["parts"]
        async with pg_factory() as db:
            command = await db.scalar(select(Command))
            assert command.payload["entryAgent"] == "xiaohe-" + role
        assert len(model.agent_calls) == 1 and "小鹤接待" not in model.agent_calls[0]


async def test_action_cannot_retarget_pending_confirmation(pg_factory, runtime_env, monkeypatch):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "consultation"}]
    )
    async with client_for(pg_factory) as (client, _):
        snapshot = await session(client)
        prefix = f"/ai-api/v1/agents/xiaohe-buyer/conversations/{snapshot['conversationId']}"
        response = await client.post(
            prefix + "/messages", json={"clientMessageId": "first", "text": "咨询服务"}
        )
        await runtime_env.worker.execute_run(UUID(response.json()["run"]["id"]))
        async with pg_factory() as db:
            run = await db.get(Run, UUID(response.json()["run"]["id"]))
            request_id = run.pending_request["requestId"]
        body = {"clientActionId": "retarget", "requestId": request_id, "text": "就按这份资料提交吧"}
        rejected = await client.post(
            prefix.replace("xiaohe-buyer", "xiaohe-supplier") + "/actions", json=body
        )
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "ACTION_STALE"
        assert len(model.agent_calls) == 1
        accepted = await client.post(prefix + "/actions", json=body)
        assert accepted.status_code == 202
        assert (await client.post(prefix + "/actions", json=body)).json() == accepted.json()


async def test_direct_entry_preserves_scope_quota_and_active_run_rules(
    pg_factory, runtime_env, monkeypatch
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    async with client_for(pg_factory) as (client, _):
        snapshot = await session(client)
        path = f"/ai-api/v1/agents/xiaohe-buyer/conversations/{snapshot['conversationId']}/messages"
        body = {"clientMessageId": "first", "text": "咨询服务"}
        headers = dict(client.headers)
        client.headers.pop("X-Xiaohe-CSRF")
        assert (await client.post(path, json=body)).status_code == 403
        client.headers.update(headers)
        first = await client.post(path, json=body)
        assert first.status_code == 202
        assert (
            await client.post(path, json=body | {"clientMessageId": "another"})
        ).status_code == 409
        assert (
            await client.post(path.replace("xiaohe-buyer", "xiaohe-supplier"), json=body)
        ).status_code == 409
        assert (
            await client.post(path.replace("xiaohe-buyer", "unknown"), json=body)
        ).status_code == 404
        async with client_for(pg_factory) as (stranger, _):
            await session(stranger)
            assert (await stranger.post(path, json=body)).status_code == 403
        async with client_for(pg_factory) as (other, _):
            other.headers.update({"X-Xiaohe-System-Id": "other", "Origin": "https://other.example"})
            await session(other)
            assert (await other.post(path, json=body)).status_code == 403


@pytest.mark.parametrize("expired", [False, True])
async def test_direct_entry_preserves_guest_quota_and_expiry(
    pg_factory, runtime_env, monkeypatch, expired
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    async with client_for(pg_factory) as (client, _):
        snapshot = await session(client)
        async with pg_factory.begin() as db:
            values = {"expires_at": func.clock_timestamp()} if expired else {"message_count": 100}
            await db.execute(update(Session).values(**values))
            if not expired:
                await db.execute(update(GuestBudget).values(message_count=100))
        path = f"/ai-api/v1/agents/xiaohe-organization/conversations/{snapshot['conversationId']}/messages"
        response = await client.post(path, json={"clientMessageId": "blocked", "text": "咨询合作"})
        assert response.status_code == (410 if expired else 429)
        async with pg_factory() as db:
            assert await db.scalar(select(Command)) is None


async def test_other_system_can_submit_through_direct_entry_with_alternate_backend(
    pg_factory, runtime_env, monkeypatch
):
    from test_identity_sessions import completion, system

    from xiaohe.contracts import Actor
    from xiaohe.identity import SESSION_COOKIE, create_user_session
    from xiaohe.reception.contracts import CreateDraft, SubmissionResult, SubmitReceipt
    from xiaohe.reception.models import ReceptionDraft
    from xiaohe.reception.store import create_draft, patch_draft

    class Backend:
        creates = 0

        async def result(self, actor, key):
            assert actor.system_id == "other"
            return SubmissionResult(status="not_found", receipt=None)

        async def submit(self, actor, payload):
            assert actor.system_id == "other"
            self.creates += 1
            return SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="external-system",
                revision=1,
                status="accepted",
            )

    backend = Backend()
    model, _ = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [{"action": "confirm_current_summary"}],
        business_client=backend,
    )
    async with pg_factory.begin() as db:
        record, secret = await create_user_session(db, system("other"), completion("other"))
    who = Actor(system=system("other"), session_id=record.id, user_id=record.user_id, guest_id=None)
    async with client_for(pg_factory) as (client, _):
        client.headers.update(
            {"X-Xiaohe-System-Id": "other", "Origin": system("other").allowed_origin}
        )
        client.cookies.set(SESSION_COOKIE, secret.get_secret_value())
        snapshot = await session(client)
        conversation = UUID(snapshot["conversationId"])
        async with pg_factory.begin() as db:
            view = await create_draft(
                db,
                who,
                conversation,
                CreateDraft(client_draft_id="other-system", kind="service", direction="buyer"),
            )
            view = await patch_draft(
                db,
                who,
                view.id,
                view.revision,
                {
                    "companyName": "虚构外部公司",
                    "contactName": "测试联系人",
                    "contactPhone": "总台分机",
                },
            )
            row = await db.get(ReceptionDraft, UUID(view.id))
            row.step, row.state, row.summary_version = "summary", "review", row.revision
        from langchain_core.messages import AIMessage
        from test_deep_factory import final_response

        model.responses = [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="submit_lead",
                        id="native-submit",
                        args={
                            "draft_id": view.id,
                            "expected_revision": view.revision,
                            "confirmation": {"confirmed": True, "evidence": "就按这份资料提交吧"},
                        },
                    )
                ],
            ),
            final_response(wait_for_input=False),
        ]
        response = await client.post(
            f"/ai-api/v1/agents/xiaohe-buyer/conversations/{conversation}/messages",
            json={"clientMessageId": "other-submit", "text": "就按这份资料提交吧"},
        )
        assert response.status_code == 202
        await runtime_env.worker.execute_run(UUID(response.json()["run"]["id"]))
        assert backend.creates == 1 and len(model.agent_calls) == 2
