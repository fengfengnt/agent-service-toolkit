import re
from uuid import uuid5

from sqlalchemy import select

from xiaohe.contracts import Actor, AgentOutcome, ServiceError, TrustedSystem
from xiaohe.models import Command, Conversation, Message, System, User
from xiaohe.reception.contracts import CreateDraft, IntentReceipt, IntentRequest, SkillContext
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.skills import answer_skill
from xiaohe.reception.store import locked_flow
from xiaohe.reception.submission import actor_from_user, create_draft_from_business
from xiaohe.runs import require_write

INTENT_LABELS = {
    "solution_discussion": "预约方案沟通",
    "sample_demo": "观看样板演示",
    "cooperation_handbook": "索取合作手册",
}


def intent_from_text(text):
    return next(
        (
            kind
            for kind, label in INTENT_LABELS.items()
            if re.fullmatch(r"(?:我(?:要|想)|请帮我|请)?\s*" + label + r"[。！!\s]*", text)
        ),
        None,
    )


async def _current(db, fence):
    run = await require_write(db, fence)
    command = await db.scalar(
        select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
    )
    if command is None:
        raise ServiceError("ACTION_STALE", 409)
    message = await db.get(Message, command.message_id)
    text = "\n".join(part["text"] for part in message.parts if part["type"] == "text")
    return run, command, text


async def record_intent(factory, fence, application_id, kind, client, *, semantic_command_id=None):
    async with factory.begin() as db:
        run, command, text = await _current(db, fence)
        if run.user_id is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if semantic_command_id is not None:
            from xiaohe.agents.contracts import SemanticDecision

            stage = command.payload.get("agentStages", {}).get("xiaohe-organization", {})
            decision = SemanticDecision.model_validate(stage.get("decision", {}))
            if command.id != semantic_command_id or decision.action != "record_intent" or (
                decision.organization_intent != kind or decision.negated or decision.deferred
                or decision.conditional or decision.needs_clarification
            ):
                raise ServiceError("INPUT_INVALID", 422)
        elif run.runtime_version != "legacy" or intent_from_text(text) != kind:
            raise ServiceError("INPUT_INVALID", 422)
        cache = command.payload.get("receptionIntent")
        if cache is not None:
            if cache["applicationId"] != application_id or cache["intent"] != kind:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            return IntentReceipt.model_validate(cache["receipt"])
        actor = actor_from_user(await db.get(User, run.user_id))
        request = IntentRequest(
            client_intent_id=str(uuid5(command.id, "organization.intent:" + kind)),
            application_id=application_id,
            intent=kind,
        )
        command_id = command.id
    if client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    application = await client.application(actor, application_id)
    if application.id != application_id or application.direction != "organization":
        raise ServiceError("FORBIDDEN", 403)
    receipt = await client.intent(actor, request)
    if receipt.application_id != application_id:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    async with factory.begin() as db:
        _, command, _ = await _current(db, fence)
        if command.id != command_id:
            raise ServiceError("ACTION_STALE", 409)
        command.payload = command.payload | {
            "receptionIntent": {
                "applicationId": application_id,
                "intent": kind,
                "receipt": receipt.model_dump(mode="json", by_alias=True),
            }
        }
    return receipt


async def sample_answer():
    return await answer_skill(
        "organization.sample-recommendation", "", SkillContext(direction="organization"), None
    )


async def start_onboarding(
    factory,
    actor,
    conversation_id,
    application_id,
    *,
    client,
    client_draft_id,
    onboarding_id=None,
    fence=None,
    definition_bundle_id=None,
):
    request = CreateDraft(
        client_draft_id=client_draft_id,
        kind="onboarding",
        direction="organization",
        source_application_id=application_id,
        source_onboarding_id=onboarding_id,
    )
    return await create_draft_from_business(
        factory, actor, conversation_id, request, client, fence=fence,
        definition_bundle_id=definition_bundle_id,
    )


async def run_organization_step(factory, fence, client):
    async with factory.begin() as db:
        run, command, text = await _current(db, fence)
        conversation = await db.get(Conversation, run.conversation_id)
        if conversation.direction != "organization":
            return None
        kind = intent_from_text(text)
        starting = bool(
            re.fullmatch(
                r"(?:我要|我想|请帮我)?(?:开始入驻|提交入驻资料|补充入驻资料|入驻)[。！!\s]*", text
            )
        )
        if kind is None and not starting:
            return None
        if cached := command.payload.get("organizationOutcome"):
            return AgentOutcome.model_validate(cached)
        flow = await locked_flow(db, conversation)
        draft = await db.get(ReceptionDraft, flow.active_draft_id) if flow.active_draft_id else None
        if (
            starting
            and draft is not None
            and draft.kind == "onboarding"
            and draft.state == "submitting"
        ):
            return None
        formal = (
            await db.scalar(
                select(ReceptionSubmission)
                .where(
                    ReceptionSubmission.draft_id == draft.id,
                    ReceptionSubmission.status == "accepted",
                )
                .order_by(ReceptionSubmission.draft_revision.desc())
                .limit(1)
            )
            if draft
            else None
        )
        application_id = (
            formal.receipt["applicationId"]
            if formal
            else draft.source_application_id
            if draft
            else None
        )
        onboarding_id = (
            formal.receipt.get("onboardingId")
            if formal
            else draft.source_onboarding_id
            if draft
            else None
        )
        resume_id, choose = None, False
        if starting and application_id:
            pending = (
                await db.scalars(
                    select(ReceptionDraft).where(
                        ReceptionDraft.conversation_id == conversation.id,
                        ReceptionDraft.kind == "onboarding",
                        ReceptionDraft.source_application_id == application_id,
                        ReceptionDraft.state.in_(["collecting", "review"]),
                    )
                )
            ).all()
            if (
                draft is not None
                and draft.kind == "onboarding"
                and draft.state in ("collecting", "review")
            ):
                resume_id = draft.id
            elif len(pending) == 1:
                resume_id = pending[0].id
            elif len(pending) > 1:
                choose = True
        if starting and "补充" in text and not onboarding_id and application_id:
            latest = await db.scalar(
                select(ReceptionSubmission)
                .where(
                    ReceptionSubmission.conversation_id == conversation.id,
                    ReceptionSubmission.status == "accepted",
                    ReceptionSubmission.receipt["applicationId"].astext == application_id,
                    ReceptionSubmission.receipt["kind"].astext == "onboarding",
                )
                .order_by(ReceptionSubmission.draft_revision.desc())
                .limit(1)
            )
            onboarding_id = latest.receipt.get("onboardingId") if latest else None
        command_id, conversation_id = command.id, conversation.id
        actor = None
        if run.user_id is not None:
            system = await db.get(System, run.system_id)
            actor = Actor(
                system=TrustedSystem(
                    system_id=system.system_id,
                    tenant_id=system.tenant_id,
                    portal_client_id=system.portal_client_id,
                    allowed_origin=system.allowed_origin,
                ),
                session_id=run.session_id,
                user_id=run.user_id,
                guest_id=None,
            )
    if application_id is None:
        answer = (
            "可以先登记组织需求，受理后再办理相关意向或独立入驻。是否愿意登记？也可以继续咨询。"
        )
        if kind == "sample_demo":
            answer = (await sample_answer()).text + "\n\n" + answer
    elif starting:
        if actor is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if choose:
            answer = "该组织有多份入驻草稿，请先选择要继续的一份。"
        elif resume_id is not None:
            answer = "将继续已保存的入驻草稿，请核对当前资料；最后确认汇总后再正式提交。"
        elif "补充" in text and not onboarding_id:
            answer = "请在“我的进展”选择已有入驻申请，再继续补充资料。"
        else:
            await start_onboarding(
                factory,
                actor,
                conversation_id,
                application_id,
                client=client,
                client_draft_id=str(uuid5(command_id, "organization.onboarding")),
                onboarding_id=onboarding_id,
                fence=fence,
            )
            answer = "已准备独立的入驻草稿，并复用已有组织资料。请核对和补充，最后确认当前汇总后再正式提交入驻申请。"
    else:
        await record_intent(factory, fence, application_id, kind, client)
        answer = "已记录" + INTENT_LABELS[kind] + "意向，具体安排待生态经理确认。"
        if kind in ("sample_demo", "cooperation_handbook"):
            answer += "当前暂无已确认可发送的素材，具体提供安排待生态经理确认。"
    async with factory.begin() as db:
        run, command, _ = await _current(db, fence)
        if command.id != command_id:
            raise ServiceError("ACTION_STALE", 409)
        conversation = await db.get(Conversation, run.conversation_id)
        flow = await locked_flow(db, conversation)
        if choose:
            flow.active_draft_id = None
        elif resume_id is not None:
            flow.active_draft_id = resume_id
        if application_id is None and flow.active_draft_id is None:
            flow.invitation_state = "offered"
        request_id = uuid5(command.id, "organization.input")
        flow.pending_request_id = request_id
        result = AgentOutcome(
            parts=[
                {"type": "text", "text": answer},
                {
                    "type": "input_request",
                    "requestId": str(request_id),
                    "prompt": "您可以继续咨询，或核对当前资料。",
                },
            ],
            wait_for_input=True,
            artifact_version_ids=[],
        )
        command.payload = command.payload | {
            "organizationOutcome": result.model_dump(mode="json", by_alias=True)
        }
        return result
