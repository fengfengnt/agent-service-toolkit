from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import install, send_in_db, talk

from xiaohe.reception.contracts import (
    ApplicationView,
    CreateDraft,
    IntentReceipt,
    OnboardingApplicationView,
    SubmissionResult,
    SubmitReceipt,
)
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.organization import record_intent, sample_answer, start_onboarding
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.reception.submission import confirm_draft, submit_confirmed
from xiaohe.runs import acquire_run

PUBLIC = {
    "organizationName": "行业协会",
    "memberSize": "100—500",
    "coverage": "本省",
    "onlineChannels": ["公众号"],
}
PRIVATE = {"contactName": "演示联系人", "contactPhone": "总台分机"}


class Business:
    def __init__(self):
        self.services, self.onboards, self.intents = [], [], {}

    async def application(self, actor, application_id):
        assert application_id == "parent"
        return ApplicationView(
            id="parent",
            subject_id="organization-one",
            direction="organization",
            revision=3,
            public_fields=PUBLIC,
            request_fields=PRIVATE,
            files=[],
        )

    async def onboarding_application(self, actor, application_id, onboarding_id):
        assert (application_id, onboarding_id) == ("parent", "onboard-one")
        return OnboardingApplicationView(
            id=onboarding_id,
            application_id=application_id,
            revision=8,
            stage="awaiting_materials",
            public_fields=PUBLIC | {"organizationName": "入驻最新协会"},
            request_fields=PRIVATE | {"contactName": "最新联系人"},
            files=[],
        )

    async def result(self, actor, key):
        return SubmissionResult(status="not_found", receipt=None)

    async def submit(self, actor, payload):
        self.services.append(payload)
        return SubmitReceipt(
            submission_key=payload.submission_key,
            kind="service",
            application_id="parent",
            revision=1,
            status="accepted",
        )

    async def onboarding(self, actor, payload):
        self.onboards.append(payload)
        return SubmitReceipt(
            submission_key=payload.submission_key,
            kind="onboarding",
            application_id="parent",
            onboarding_id="onboard-one",
            revision=1,
            status="accepted",
        )

    async def intent(self, actor, payload):
        self.intents.setdefault(payload.client_intent_id, payload)
        return IntentReceipt(
            id="intent-one", application_id=payload.application_id, status="recorded"
        )


async def user(factory):
    _, token = await login(factory)
    initial, _, _ = await bootstrap(factory, token)
    return await actor(factory, token), UUID(initial.conversation_id)


async def confirm(factory, who, conversation, draft):
    async with factory.begin() as db:
        row = await db.get(ReceptionDraft, UUID(draft.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
        turn = await send_in_db(db, who, conversation, "确认提交")
        submission = await confirm_draft(
            db, who, draft.id, row.revision, row.revision, UUID(turn.message.id)
        )
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(turn.run.id))
    return submission, fence


@pytest.mark.pg
@pytest.mark.asyncio
async def test_intent_records_only_followup_and_is_not_an_appointment(pg_factory, job_app):
    who, conversation = await user(pg_factory)
    async with pg_factory.begin() as db:
        turn = await send_in_db(db, who, conversation, "预约方案沟通")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(turn.run.id))
    client = Business()
    first = await record_intent(pg_factory, fence, "parent", "solution_discussion", client)
    second = await record_intent(pg_factory, fence, "parent", "solution_discussion", client)
    assert first == second and len(client.intents) == 1
    assert first.status == "recorded" and not client.services and not client.onboards


@pytest.mark.pg
@pytest.mark.asyncio
async def test_new_onboarding_is_rejected_without_creating_a_second_application(pg_factory, job_app):
    from xiaohe.contracts import ServiceError
    who, conversation = await user(pg_factory)
    client = Business()
    with pytest.raises(ServiceError, match="APPLICATION_FLOW_CHANGED"):
        await start_onboarding(pg_factory, who, conversation, "parent", client=client, client_draft_id="onboard-new")
    assert not client.onboards and not client.services
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReceptionDraft)) == 0


@pytest.mark.pg
@pytest.mark.asyncio
async def test_legacy_onboarding_remains_readable_but_cannot_be_changed(pg_factory):
    from xiaohe.contracts import ServiceError
    from xiaohe.reception.store import load_reception
    who, conversation = await user(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(db, who, conversation, CreateDraft(client_draft_id="legacy", kind="service", direction="organization"))
        row = await db.get(ReceptionDraft, UUID(draft.id))
        row.kind, row.source_application_id, row.fields = "onboarding", "parent", PUBLIC | PRIVATE
    async with pg_factory.begin() as db:
        restored = await load_reception(db, who, conversation)
        assert restored.active_draft.kind == "onboarding"
        assert restored.active_draft.fields == PUBLIC | PRIVATE
        with pytest.raises(ServiceError, match="APPLICATION_FLOW_CHANGED"):
            await patch_draft(db, who, draft.id, draft.revision, {"contactName": "不可写"})


@pytest.mark.pg
@pytest.mark.asyncio
async def test_service_confirmation_does_not_create_onboarding(pg_factory, job_app):
    who, conversation = await user(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="service", kind="service", direction="organization"),
        )
        draft = await patch_draft(db, who, draft.id, 1, PUBLIC | PRIVATE)
    submission, fence = await confirm(pg_factory, who, conversation, draft)
    client = Business()
    await submit_confirmed(pg_factory, fence, submission.id, client)
    assert len(client.services) == 1 and not client.onboards
    async with pg_factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ReceptionDraft)
                .where(ReceptionDraft.kind == "onboarding")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_missing_sample_material_has_no_invented_link_or_delivery():
    answer = await sample_answer()
    assert "暂无已确认" in answer.text and "http" not in answer.text
    assert "已发送" not in answer.text


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["预约方案沟通", "观看样板演示", "索取合作手册", "开始入驻"])
async def test_guest_organization_intent_starts_collection_and_can_renew_after_defer(
    pg_factory, runtime_env, monkeypatch, intent
):
    from test_admission import visitor

    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await visitor(pg_factory)
    await talk(pg_factory, runtime_env, who, conversation, "社群组织")
    message, view = await talk(pg_factory, runtime_env, who, conversation, intent)
    assert view.invitation_state == "accepted" and not view.draft_refs
    assert any(part.type == "auth_request" for part in message.parts)
    await talk(pg_factory, runtime_env, who, conversation, "暂时不用")
    async with pg_factory() as db:
        from xiaohe.reception.store import load_reception

        assert (await load_reception(db, who, conversation)).invitation_state == "deferred"
    message, view = await talk(pg_factory, runtime_env, who, conversation, intent)
    assert view.invitation_state == "accepted" and not view.draft_refs
    assert any(part.type == "auth_request" for part in message.parts)
    assert "是否愿意" not in message.model_dump_json()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_worker_records_intent_and_routes_onboarding_to_the_unified_application(
    pg_factory, runtime_env, monkeypatch
):
    client = Business()
    await install(pg_factory, runtime_env, monkeypatch, client)
    who, conversation = await user(pg_factory)
    await talk(pg_factory, runtime_env, who, conversation, "社群组织")
    _, state = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    async with pg_factory.begin() as db:
        await patch_draft(
            db, who, state.active_draft.id, state.active_draft.revision, PUBLIC | PRIVATE
        )
    await talk(pg_factory, runtime_env, who, conversation, "查看汇总")
    await talk(pg_factory, runtime_env, who, conversation, "确认提交")
    assert len(client.services) == 1 and not client.onboards
    message, state = await talk(pg_factory, runtime_env, who, conversation, "预约方案沟通")
    assert len(client.intents) == 1 and "意向" in message.model_dump_json()
    current_id = state.active_draft.id
    message, state = await talk(pg_factory, runtime_env, who, conversation, "我要入驻")
    assert state.active_draft.id == current_id and state.active_draft.kind == "service"
    assert state.active_draft.state == "accepted"
    assert not client.onboards and len(client.services) == 1
    assert "已统一为一份申请" in message.model_dump_json()
