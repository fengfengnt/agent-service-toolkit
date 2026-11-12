import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_admission import visitor
from test_agent_handoff import install_agents
from test_http_api import client_for, session
from test_identity_sessions import system
from test_reception_submission import confirmed

from xiaohe.admission import accept_action, accept_agent_action, accept_message
from xiaohe.contracts import Actor, ChatAction, ServiceError
from xiaohe.models import Command, Conversation, GuestBudget, Message, Run
from xiaohe.reception.api import create_reception_router
from xiaohe.reception.contracts import (
    CreateDraft,
    ReceptionControl,
    SubmissionResult,
    SubmitReceipt,
)
from xiaohe.reception.flow import apply_reception_control
from xiaohe.reception.models import ReceptionDraft, ReceptionFlow, ReceptionSubmission
from xiaohe.reception.store import create_draft
from xiaohe.reception.submission import submit_confirmed
from xiaohe.runs import acquire_run, cancel_run, require_write

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def control(direction, key="partner"):
    return ReceptionControl.model_validate(
        {"clientActionId": key, "action": "switch_direction", "direction": direction}
    )


async def switch(factory, who, conversation, direction, key="partner"):
    async with factory.begin() as db:
        return await apply_reception_control(db, who, conversation, control(direction, key))


@pytest.mark.parametrize("direction", ["organization", "supplier"])
@pytest.mark.parametrize("status", ["queued", "running", "waiting_input"])
async def test_partner_entry_switches_and_preserves_drafts(
    pg_factory, runtime_env, monkeypatch, direction, status
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        target = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="target", direction=direction, kind="service"),
        )
        original = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="original", direction="buyer", kind="service"),
        )
        accepted = await accept_message(db, who, conversation, "old", "原方向问题")
    fence = None
    if status == "running":
        async with pg_factory.begin() as db:
            fence = await acquire_run(db, UUID(accepted.run.id))
    elif status == "waiting_input":
        async with pg_factory.begin() as db:
            run = await db.get(Run, UUID(accepted.run.id))
            run.status = status
            run.pending_request = {
                "type": "input_request",
                "requestId": "old-question",
                "prompt": "确认原资料",
            }
            (await db.get(ReceptionFlow, conversation)).pending_request_id = uuid4()
    async with pg_factory() as db:
        old_epoch = (await db.get(Run, UUID(accepted.run.id))).ownership_epoch
    result = await switch(pg_factory, who, conversation, direction)
    assert result.message.role == "assistant" and result.message.parts[0].type == "system_notice"
    assert result.viewer.message_count == 1
    async with pg_factory() as db:
        run = await db.get(Run, UUID(accepted.run.id))
        assert run.status == "cancelled" and run.ownership_epoch > old_epoch
        assert (await db.get(Conversation, conversation)).direction == direction
        flow = await db.get(ReceptionFlow, conversation)
        assert str(flow.active_draft_id) == target.id and flow.pending_request_id is None
        for draft in (target, original):
            row = await db.get(ReceptionDraft, UUID(draft.id))
            assert (row.fields, row.revision, row.state, row.step) == (
                {},
                1,
                "collecting",
                "round1",
            )
        command = await db.scalar(select(Command).where(Command.client_key == "partner"))
        assert command.payload["entryAgent"] == "xiaohe-" + direction
        assert (
            await db.scalar(select(func.count()).select_from(Message).where(Message.role == "user"))
            == 1
        )
    if fence:
        with pytest.raises(ServiceError, match="WRITE_FENCE_STALE"):
            async with pg_factory.begin() as db:
                await require_write(db, fence)
    if status == "waiting_input":
        with pytest.raises(ServiceError, match="ACTION_STALE"):
            async with pg_factory.begin() as db:
                await accept_action(
                    db, who, conversation, "stale", "old-question", None, "确认提交"
                )


async def test_partner_entry_is_idempotent(pg_factory, runtime_env, monkeypatch):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    who, conversation = await visitor(pg_factory)
    first = await switch(pg_factory, who, conversation, "organization", "one")
    assert (await switch(pg_factory, who, conversation, "organization", "one")) == first
    assert (
        await switch(pg_factory, who, conversation, "organization", "duplicate")
    ).run.id == first.run.id
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        await switch(pg_factory, who, conversation, "supplier", "one")
    second = await switch(pg_factory, who, conversation, "supplier", "two")
    await switch(pg_factory, who, conversation, "organization", "one")
    await switch(pg_factory, who, conversation, "organization", "duplicate")
    async with pg_factory() as db:
        assert (await db.get(Conversation, conversation)).direction == "supplier"
        assert await db.scalar(select(func.count()).select_from(Run)) == 2
        assert (await db.get(Run, UUID(second.run.id))).status == "queued"
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        await switch(pg_factory, who, conversation, "supplier", "duplicate")


@pytest.mark.parametrize("direction", ["organization", "supplier"])
async def test_partner_entry_greets_without_model_or_submission(
    pg_factory, runtime_env, monkeypatch, direction
):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "consultation"}]
    )
    who, conversation = await visitor(pg_factory)
    accepted = await switch(pg_factory, who, conversation, direction)
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    assert model.requests == []
    async with pg_factory() as db:
        run = await db.get(Run, UUID(accepted.run.id))
        assert run.status == "waiting_input"
        question = run.pending_request["requestId"]
        assert await db.scalar(select(func.count()).select_from(ReceptionDraft)) == 0
        command = await db.scalar(select(Command).where(Command.run_id == run.id))
        assert command.payload["deepExecution"]["active"] == "xiaohe-" + direction
        answer = await db.scalar(
            select(Message)
            .where(Message.id != UUID(accepted.message.id))
            .order_by(Message.created_at.desc())
        )
        assert all(part["type"] != "auth_request" for part in answer.parts)
    await switch(pg_factory, who, conversation, direction, "same-direction")
    async with pg_factory.begin() as db:
        following = await accept_agent_action(
            db,
            who,
            "xiaohe-" + direction,
            conversation,
            ChatAction(client_action_id="follow", request_id=question, text="想了解合作方式"),
        )
    await runtime_env.worker.execute_run(UUID(following.run.id))
    assert len(model.agent_calls) == 1 and "小鹤接待" not in model.agent_calls[0]


async def test_partner_entry_rejects_without_mutation(pg_factory, runtime_env, monkeypatch):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        initial = await session(client)
        path = f"/ai-api/v1/reception/conversations/{initial['conversationId']}/controls"
        body = {"clientActionId": "click", "action": "switch_direction", "direction": "supplier"}
        saved = dict(client.headers)
        client.headers.pop("X-Xiaohe-CSRF")
        assert (await client.post(path, json=body)).status_code == 403
        client.headers.update(saved)
        async with client_for(pg_factory) as (stranger, other):
            other.include_router(create_reception_router(pg_factory))
            await session(stranger)
            assert (await stranger.post(path, json=body)).status_code == 403
        async with pg_factory.begin() as db:
            row = await db.get(Conversation, UUID(initial["conversationId"]))
            row.merge_pending = True
        assert (await client.post(path, json=body)).json()["error"]["code"] == "MERGE_PENDING"
        async with pg_factory() as db:
            assert (
                await db.get(Conversation, UUID(initial["conversationId"]))
            ).direction == "unknown"
            assert await db.scalar(select(func.count()).select_from(Command)) == 0


async def test_partner_entry_does_not_bypass_guest_quota(pg_factory, runtime_env, monkeypatch):
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        initial = await session(client)
        async with pg_factory.begin() as db:
            budget = await db.scalar(select(GuestBudget))
            budget.message_count = budget.message_limit
        path = f"/ai-api/v1/reception/conversations/{initial['conversationId']}/controls"
        response = await client.post(
            path,
            json={"clientActionId": "limit", "action": "switch_direction", "direction": "supplier"},
        )
        assert response.status_code == 202
        actual = await client.post(
            f"/ai-api/v1/conversations/{initial['conversationId']}/messages",
            json={"clientMessageId": "real", "text": "合作费用"},
        )
        assert actual.json()["error"]["code"] == "VISITOR_LIMIT_REACHED"


async def test_switch_keeps_submission_receipt(pg_factory, runtime_env, monkeypatch):
    fence, submission = await confirmed(pg_factory)
    await install_agents(pg_factory, runtime_env, monkeypatch, [])
    async with pg_factory() as db:
        run = await db.get(Run, fence.run_id)
        who = Actor(system=system(), session_id=run.session_id, user_id=run.user_id, guest_id=None)
        conversation = run.conversation_id
    entered, released = asyncio.Event(), asyncio.Event()

    class InFlightClient:
        receipt = None
        creates = 0

        async def result(self, actor, key):
            assert key == submission.submission_key
            return SubmissionResult(
                status="accepted" if self.receipt else "not_found", receipt=self.receipt
            )

        async def submit(self, actor, payload):
            self.creates += 1
            self.receipt = SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="same-application",
                revision=1,
                status="accepted",
            )
            entered.set()
            await released.wait()
            return self.receipt

    client = InFlightClient()
    inflight = asyncio.create_task(submit_confirmed(pg_factory, fence, submission.id, client))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        entry = await switch(pg_factory, who, conversation, "supplier")
    finally:
        released.set()
    with pytest.raises(ServiceError, match="WRITE_FENCE_STALE"):
        await inflight
    async with pg_factory.begin() as db:
        saved = await db.get(ReceptionSubmission, submission.id)
        assert saved.submission_key == submission.submission_key and saved.status == "pending"
        await cancel_run(db, who, UUID(entry.run.id))
        accepted = await accept_message(db, who, conversation, "reconcile", "查询原提交结果")
    async with pg_factory.begin() as db:
        current_fence = await acquire_run(db, UUID(accepted.run.id))
    recovered = await submit_confirmed(pg_factory, current_fence, submission.id, client)
    assert recovered.application_id == "same-application" and client.creates == 1
