import hashlib
import json

from sqlalchemy import select

from xiaohe.admission import _accept, _result, accept_action
from xiaohe.contracts import ServiceError
from xiaohe.database import database_now
from xiaohe.lead_skills.store import resolve_draft_policy
from xiaohe.models import Command, Conversation, Run
from xiaohe.reception.contracts import ReceptionAction
from xiaohe.reception.fields import missing_required
from xiaohe.reception.store import flow_record, require_draft

LABELS = {"buyer": "采购商", "organization": "社群组织", "supplier": "供应商"}


COLLECTION_NOTICE = "开始填写需求资料"


ACTION_TEXT = {
    "save_round": "保存本轮资料",
    "skip_optional": "跳过选填，查看汇总",
    "show_summary": "查看资料汇总",
    "confirm": "确认提交当前汇总",
    "resume": "继续所选草稿",
}


async def apply_reception_control(db, actor, conversation_id, request):
    flow = await flow_record(db, actor, conversation_id)
    if request.action == "switch_direction":
        return await _switch_partner(db, actor, conversation_id, flow, request)
    if request.action == "start_collection" and actor.user_id is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    existing = await db.scalar(
        select(Command).where(
            Command.conversation_id == conversation_id,
            Command.client_key == request.client_action_id,
        )
    )
    if existing is not None:
        if existing.payload.get("receptionControl") != request.action:
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
        return await _result(db, actor, existing)
    conversation = await db.get(Conversation, conversation_id)
    if (
        conversation.direction not in LABELS
        or (
            request.action == "accept_invitation"
            and flow.invitation_state not in ("none", "offered", "deferred")
        )
        or (
            request.action == "start_collection"
            and flow.invitation_state != "accepted"
            and flow.active_draft_id is None
        )
    ):
        raise ServiceError("ACTION_STALE", 409)
    waiting = (
        await db.scalar(
            select(Run).where(
                Run.conversation_id == conversation_id,
                Run.status == "waiting_input",
                Run.pending_request["requestId"].astext == str(flow.pending_request_id),
            )
        )
        if flow.pending_request_id
        else None
    )
    kind = "action" if waiting is not None else "message"
    payload = {
        "receptionControl": request.action,
        "clientActionId" if waiting else "clientMessageId": request.client_action_id,
    }
    if waiting is not None:
        payload["requestId"] = str(flow.pending_request_id)
    return await _accept(
        db,
        actor,
        conversation_id,
        kind,
        request.client_action_id,
        payload,
        system_notice="开始注册或登录" if request.action == "accept_invitation" else "采集信息",
    )


async def _switch_partner(db, actor, conversation_id, flow, request):
    from xiaohe.admission import _agent_entry
    from xiaohe.agents.state import _select_direction
    from xiaohe.runs import cancel_run

    conversation = await db.get(Conversation, conversation_id)
    agent_id = "xiaohe-" + request.direction
    _agent_entry(db, agent_id)
    existing = await db.scalar(
        select(Command).where(
            Command.conversation_id == conversation_id,
            Command.client_key == request.client_action_id,
        )
    )
    if existing is not None:
        if (existing.payload.get("receptionControl"), existing.payload.get("direction")) != (
            request.action,
            request.direction,
        ):
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
        return await _result(db, actor, existing)
    if conversation.merge_pending:
        raise ServiceError("MERGE_PENDING", 409)
    payload = {
        "clientMessageId": request.client_action_id,
        "receptionControl": request.action,
        "direction": request.direction,
        "entryAgent": agent_id,
    }
    if conversation.direction == request.direction:
        previous = await db.scalar(
            select(Command)
            .where(
                Command.conversation_id == conversation_id,
                Command.payload["receptionControl"].astext == "switch_direction",
                Command.payload["direction"].astext == request.direction,
            )
            .order_by(Command.created_at.desc(), Command.id.desc())
            .limit(1)
        )
        if previous is not None:
            # 合并开场也记录新键，迟到重放只能返回回执，不能再次切换方向。
            receipt = Command(
                conversation_id=conversation_id,
                system_id=actor.system.system_id,
                tenant_id=actor.system.tenant_id,
                kind="message",
                client_key=request.client_action_id,
                payload_digest=hashlib.sha256(
                    json.dumps(
                        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                payload=payload | {"receptionReceiptFor": str(previous.id)},
                message_id=previous.message_id,
                run_id=previous.run_id,
                completed_at=await database_now(db),
            )
            db.add(receipt)
            await db.flush()
            return await _result(db, actor, receipt)
    runs = (
        await db.scalars(
            select(Run)
            .where(
                Run.conversation_id == conversation_id,
                Run.status.in_(["queued", "running", "waiting_input"]),
            )
            .order_by(Run.created_at, Run.id)
        )
    ).all()
    for run in runs:
        await cancel_run(db, actor, run.id)
    flow.pending_request_id = None
    await _select_direction(db, conversation, flow, agent_id)
    flow.invitation_state = "accepted" if flow.active_draft_id else "none"
    return await _accept(
        db,
        actor,
        conversation_id,
        "message",
        request.client_action_id,
        payload,
        system_notice="已转接" + LABELS[request.direction] + " Agent",
    )


async def apply_reception_action(db, actor, draft_id, request: ReceptionAction):
    row = await require_draft(db, actor, draft_id)
    if row.kind == "onboarding":
        raise ServiceError("APPLICATION_FLOW_CHANGED", 409)
    flow = await flow_record(db, actor, row.conversation_id)
    conversation = await db.get(Conversation, row.conversation_id)
    if request.action == "resume" and row.direction != conversation.direction:
        raise ServiceError("ACTION_STALE", 409)
    metadata = {
        "draftId": str(row.id),
        **request.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    existing = await db.scalar(
        select(Command).where(
            Command.conversation_id == row.conversation_id,
            Command.kind == "action",
            Command.client_key == request.client_action_id,
        )
    )
    if existing is not None:
        if existing.payload.get("reception") != metadata:
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
        return await accept_action(
            db,
            actor,
            row.conversation_id,
            request.client_action_id,
            request.request_id,
            None,
            ACTION_TEXT[request.action],
        )
    if row.revision != request.expected_revision:
        raise ServiceError("DRAFT_CONFLICT", 409)
    if str(flow.pending_request_id) != request.request_id or (
        request.action != "resume" and flow.active_draft_id != row.id
    ):
        raise ServiceError("ACTION_STALE", 409)
    if request.action == "confirm":
        if actor.user_id is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if (
            row.state != "review"
            or row.summary_version != request.summary_version
            or row.summary_version != row.revision
        ):
            raise ServiceError("DRAFT_CONFLICT", 409)
        if missing_required(row.direction, row.fields, policy=await resolve_draft_policy(db, row)):
            raise ServiceError("INPUT_INVALID", 422)
    accepted = await accept_action(
        db,
        actor,
        row.conversation_id,
        request.client_action_id,
        request.request_id,
        None,
        ACTION_TEXT[request.action],
    )
    command = await db.scalar(
        select(Command).where(
            Command.conversation_id == row.conversation_id,
            Command.client_key == request.client_action_id,
            Command.kind == "action",
        )
    )
    command.payload = command.payload | {"reception": metadata}
    return accepted
