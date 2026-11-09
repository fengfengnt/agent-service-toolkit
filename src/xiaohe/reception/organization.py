import re
from uuid import uuid5

from sqlalchemy import select

from xiaohe.contracts import AgentOutcome, ServiceError
from xiaohe.models import Command, Conversation, Message, User
from xiaohe.reception.contracts import IntentReceipt, IntentRequest, SkillContext
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.skills import answer_skill
from xiaohe.reception.store import locked_flow
from xiaohe.reception.submission import actor_from_user
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
    raise ServiceError("APPLICATION_FLOW_CHANGED", 409)


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
        command_id = command.id
    if application_id is None:
        answer = "可以先登记组织合作申请，受理后在“我的进展”查看或修改。"
        if kind == "sample_demo":
            answer = (await sample_answer()).text + "\n\n" + answer
    elif starting:
        answer = "合作与入驻已统一为一份申请，无需再次提交入驻资料。请在“我的进展”查看/修改原申请；审核通过与已完成入驻会分别显示。"
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
        offered = (
            application_id is None
            and flow.active_draft_id is None
            and flow.invitation_state == "none"
        )
        if offered:
            flow.invitation_state = "offered"
            answer += "\n\n是否愿意登录并登记需求，方便后续跟进？也可以继续咨询或暂缓。"
        request_id = uuid5(command.id, "organization.input")
        flow.pending_request_id = request_id
        result = AgentOutcome(
            parts=[
                {"type": "text", "text": answer},
                *(
                    [{"type": "auth_request", "intent": "lead"}]
                    if offered and run.user_id is None
                    else []
                ),
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
