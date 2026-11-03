import hashlib
import json
import re
import unicodedata
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import (
    AcceptedTurn,
    Actor,
    ChatAction,
    ChatMessage,
    MessageInput,
    RunRef,
    ServiceError,
)
from xiaohe.conversations import load_viewer, require_conversation
from xiaohe.events import record_event
from xiaohe.identity import require_session
from xiaohe.jobs import defer_run
from xiaohe.models import Command, Message, Run, Session

_SENSITIVE = re.compile(
    r"(?<!\d)1[3-9]\d{9}(?!\d)|(?:验证码|短信码|otp)\s*[:：]?\s*\d{4,8}|^\s*\d{6}\s*$",
    re.IGNORECASE,
)


def _parse(model, value):
    result = None
    try:
        result = model.model_validate(value)
    except ValidationError:
        pass
    if result is None:
        raise ServiceError("INPUT_INVALID", 422)
    return result


def _safe_text(value: str):
    inspected = re.sub(r"(?<=\d)[\s-]+(?=\d)", "", unicodedata.normalize("NFKC", value))
    if _SENSITIVE.search(inspected):
        raise ServiceError("SENSITIVE_INPUT_REQUIRES_CARD", 422)


async def _result(db: AsyncSession, actor: Actor, command: Command) -> AcceptedTurn:
    message = await db.scalar(
        select(Message).where(
            Message.id == command.message_id,
            Message.conversation_id == command.conversation_id,
            Message.system_id == actor.system.system_id,
            Message.tenant_id == actor.system.tenant_id,
        )
    )
    run = await db.scalar(
        select(Run)
        .where(
            Run.id == command.run_id,
            Run.conversation_id == command.conversation_id,
            Run.system_id == actor.system.system_id,
            Run.tenant_id == actor.system.tenant_id,
        )
        .execution_options(populate_existing=True)
    )
    return AcceptedTurn(
        message=ChatMessage(
            id=str(message.id),
            role=message.role,
            created_at=message.created_at,
            parts=message.parts,
        ),
        run=RunRef(id=str(run.id), status=run.status),
        viewer=await load_viewer(db, actor),
    )


async def _accept(
    db: AsyncSession, actor: Actor, conversation_id: UUID, kind: str, key: str, payload: dict
) -> AcceptedTurn:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    async with db.begin_nested():
        conversation = await require_conversation(db, actor, conversation_id)
        session = await require_session(db, actor)
        existing = await db.scalar(
            select(Command)
            .where(
                Command.conversation_id == conversation.id,
                Command.kind == kind,
                Command.client_key == key,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.payload_digest != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            return await _result(db, actor, existing)
        if conversation.merge_pending:
            raise ServiceError("MERGE_PENDING", 409)
        if actor.guest_id is not None and session.message_count >= session.message_limit:
            raise ServiceError("VISITOR_LIMIT_REACHED", 429)
        active = await db.scalar(
            select(Run)
            .where(Run.conversation_id == conversation.id, Run.holds_slot.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        request_id = None
        question = None
        if kind == "message":
            if active is not None:
                raise ServiceError("RUN_ACTIVE", 409)
            visible = payload["text"]
            run = None
        else:
            request_id = payload["requestId"]
            run = await db.scalar(
                select(Run)
                .where(
                    Run.conversation_id == conversation.id,
                    Run.status == "waiting_input",
                    Run.pending_request["requestId"].astext == request_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None:
                raise ServiceError("ACTION_STALE", 409)
            if active is not None and active.id != run.id:
                raise ServiceError("RUN_ACTIVE", 409)
            consumed = await db.scalar(
                select(Command.id).where(Command.run_id == run.id, Command.request_id == request_id)
            )
            if consumed is not None:
                raise ServiceError("ACTION_STALE", 409)
            question = run.pending_request
            if question.get("type") == "choice" and "optionId" in payload:
                options = [
                    x for x in question.get("options", []) if x.get("id") == payload["optionId"]
                ]
                if len(options) != 1:
                    raise ServiceError("ACTION_STALE", 409)
                visible = options[0]["label"] or payload["optionId"]
            elif question.get("type") == "input_request" and "text" in payload:
                visible = payload["text"]
            else:
                raise ServiceError("ACTION_STALE", 409)
        _safe_text(visible)
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            system_id=actor.system.system_id,
            tenant_id=actor.system.tenant_id,
            role="user",
            parts=[{"type": "text", "text": visible}],
            client_message_id=key if kind == "message" else None,
        )
        db.add(message)
        await db.flush()
        if run is None:
            run = Run(
                id=uuid4(),
                conversation_id=conversation.id,
                system_id=actor.system.system_id,
                tenant_id=actor.system.tenant_id,
                session_id=actor.session_id,
                user_id=actor.user_id,
                guest_id=actor.guest_id,
                execution_expires_at=session.expires_at,
                initial_message_id=message.id,
                status="queued",
                holds_slot=True,
                ownership_epoch=conversation.ownership_epoch,
            )
            db.add(run)
        else:
            run.status, run.holds_slot, run.pending_request = "queued", True, None
            run.updated_at = func.clock_timestamp()
            run.user_id, run.guest_id, run.session_id = (
                actor.user_id,
                actor.guest_id,
                actor.session_id,
            )
            run.ownership_epoch = conversation.ownership_epoch
        command = Command(
            conversation_id=conversation.id,
            system_id=actor.system.system_id,
            tenant_id=actor.system.tenant_id,
            kind=kind,
            client_key=key,
            payload_digest=digest,
            payload=payload,
            question=question,
            message_id=message.id,
            run_id=run.id,
            request_id=request_id,
        )
        await db.flush()
        db.add(command)
        await db.flush()
        run.job_id = await defer_run(db, run.id, f"{actor.system.tenant_id}:{conversation.id}")
        if actor.guest_id is not None:
            changed = await db.scalar(
                update(Session)
                .where(
                    Session.id == actor.session_id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > func.clock_timestamp(),
                    Session.message_count < Session.message_limit,
                )
                .values(
                    message_count=Session.message_count + 1,
                    expires_at=func.clock_timestamp()
                    + Session.retention_seconds * text("interval '1 second'"),
                )
                .returning(Session.id)
                .execution_options(synchronize_session=False)
            )
            if changed is None:
                await require_session(db, actor)
                raise ServiceError("VISITOR_LIMIT_REACHED", 429)
        result = await _result(db, actor, command)
        run.execution_expires_at = session.expires_at
        record_event(
            db,
            conversation,
            "message.upsert",
            {"message": result.message.model_dump(mode="json", by_alias=True)},
            run.id,
        )
        record_event(
            db,
            conversation,
            "run.updated",
            {"run": result.run.model_dump(mode="json", by_alias=True)},
            run.id,
        )
        record_event(
            db,
            conversation,
            "viewer.updated",
            {"viewer": result.viewer.model_dump(mode="json", by_alias=True)},
            run.id,
        )
        await db.flush()
        await require_session(db, actor)
        return result


async def accept_message(
    db: AsyncSession, actor: Actor, conversation_id: UUID, client_message_id: str, text: str
) -> AcceptedTurn:
    body = _parse(MessageInput, {"clientMessageId": client_message_id, "text": text})
    _safe_text(body.text)
    return await _accept(
        db,
        actor,
        conversation_id,
        "message",
        body.client_message_id,
        body.model_dump(mode="json", by_alias=True),
    )


async def accept_action(
    db: AsyncSession,
    actor: Actor,
    conversation_id: UUID,
    client_action_id: str,
    request_id: str,
    option_id: str | None,
    text: str | None,
) -> AcceptedTurn:
    value = {"clientActionId": client_action_id, "requestId": request_id}
    if option_id is not None:
        value["optionId"] = option_id
    if text is not None:
        value["text"] = text
    body = _parse(ChatAction, value)
    if body.text is not None:
        _safe_text(body.text)
    return await _accept(
        db,
        actor,
        conversation_id,
        "action",
        body.client_action_id,
        body.model_dump(mode="json", by_alias=True),
    )
