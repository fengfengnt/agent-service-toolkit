"""在当前授权与 Conversation 锁内读取有界历史；分页游标不授予权限。"""

import base64
import hashlib
import hmac
import json
import re
from typing import Annotated
from uuid import UUID

from pydantic import Field, SecretStr
from sqlalchemy import String, cast, exists, select, tuple_

from xiaohe.contracts import Actor, ChatMessage, NonNegative, ServiceError, UtcTime, WireModel
from xiaohe.contracts_v2 import ChatSnapshotV2, HistoryPageV2, MessageBaselineV2
from xiaohe.conversations import load_merge_state, load_viewer, require_conversation
from xiaohe.identity import CSRF_HEADER, require_session
from xiaohe.models import Event, Message, Run

Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class HistoryCursor(WireModel):
    scope_digest: Digest
    principal_digest: Digest
    conversation_id: UUID
    history_epoch: NonNegative
    ceiling_sequence: NonNegative
    before_created_at: UtcTime
    before_id: UUID


def _key(secret: SecretStr) -> bytes:
    return hmac.digest(secret.get_secret_value().encode(), b"xiaohe.history-cursor.v2", "sha256")


def _base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def encode_history_cursor(payload: HistoryCursor, secret: SecretStr) -> str:
    raw = payload.model_dump_json(by_alias=True).encode()
    token = _base64(raw) + "." + _base64(hmac.digest(_key(secret), raw, "sha256"))
    if len(token) > 2048:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    return token


def decode_history_cursor(token: str, secret: SecretStr) -> HistoryCursor:
    try:
        if (
            not isinstance(token, str)
            or len(token) > 2048
            or re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) is None
        ):
            raise ValueError()
        raw, signature = [
            base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
            for part in token.split(".")
        ]
        if not hmac.compare_digest(hmac.digest(_key(secret), raw, "sha256"), signature):
            raise ValueError()
        return HistoryCursor.model_validate_json(raw)
    except ValueError:
        raise ServiceError("HISTORY_CURSOR_INVALID", 409) from None


def _digests(actor: Actor) -> tuple[str, str]:
    scope = json.dumps([actor.system.system_id, actor.system.tenant_id], separators=(",", ":"))
    principal = json.dumps(
        ["user", actor.user_id] if actor.user_id is not None else ["guest", str(actor.guest_id)],
        separators=(",", ":"),
    )
    return hashlib.sha256(scope.encode()).hexdigest(), hashlib.sha256(
        principal.encode()
    ).hexdigest()


def _position(conversation) -> tuple[int, int]:
    values = conversation.history_epoch, conversation.event_sequence
    if any(type(value) is not int or not 0 <= value <= 2**53 - 1 for value in values):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    return values


def _message(row: Message) -> ChatMessage:
    return ChatMessage(id=str(row.id), role=row.role, created_at=row.created_at, parts=row.parts)


async def _page(db, actor, conversation, ceiling, before, secret):
    epoch, watermark = _position(conversation)
    query = select(Message).where(
        Message.conversation_id == conversation.id,
        Message.system_id == actor.system.system_id,
        Message.tenant_id == actor.system.tenant_id,
        Message.membership_sequence <= ceiling,
    )
    if before is not None:
        query = query.where(
            tuple_(Message.created_at, Message.id)
            < tuple_(before.before_created_at, before.before_id)
        )
    rows = list(
        await db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(11))
    )
    has_more = len(rows) > 10
    rows = rows[:10]
    cursor = None
    if has_more:
        scope, principal = _digests(actor)
        cursor = encode_history_cursor(
            HistoryCursor(
                scope_digest=scope,
                principal_digest=principal,
                conversation_id=conversation.id,
                history_epoch=epoch,
                ceiling_sequence=ceiling,
                before_created_at=rows[-1].created_at,
                before_id=rows[-1].id,
            ),
            secret,
        )
    return HistoryPageV2(
        version=2,
        conversation_id=str(conversation.id),
        messages=[_message(row) for row in reversed(rows)],
        watermark_sequence=watermark,
        history=dict(pageSize=10, hasMore=has_more, olderCursor=cursor, historyEpoch=epoch),
    )


async def load_history_page(
    db, actor: Actor, conversation_id: UUID, before: str, *, cursor_secret: SecretStr
) -> HistoryPageV2:
    conversation = await require_conversation(db, actor, conversation_id)
    epoch, _ = _position(conversation)
    cursor = decode_history_cursor(before, cursor_secret)
    if (cursor.scope_digest, cursor.principal_digest) != _digests(
        actor
    ) or cursor.conversation_id != conversation.id:
        raise ServiceError("FORBIDDEN", 403)
    if cursor.history_epoch != epoch:
        raise ServiceError("HISTORY_RESET_REQUIRED", 409)
    if cursor.ceiling_sequence > conversation.event_sequence:
        raise ServiceError("HISTORY_CURSOR_INVALID", 409)
    return await _page(db, actor, conversation, cursor.ceiling_sequence, cursor, cursor_secret)


async def load_message_baseline(
    db, actor: Actor, conversation_id: UUID, message_id: UUID
) -> MessageBaselineV2:
    conversation = await require_conversation(db, actor, conversation_id)
    epoch, watermark = _position(conversation)
    message = await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation.id,
            Message.system_id == actor.system.system_id,
            Message.tenant_id == actor.system.tenant_id,
        )
    )
    if message is None:
        raise ServiceError("MESSAGE_NOT_FOUND", 404)
    return MessageBaselineV2(
        version=2,
        conversation_id=str(conversation.id),
        message=_message(message),
        history_epoch=epoch,
        watermark_sequence=watermark,
    )


async def _active_input(db, active):
    if active is None or active.status != "waiting_input" or not active.pending_request:
        return None
    part = active.pending_request
    if part.get("type") not in ("choice", "input_request") or not part.get("requestId"):
        return None
    linked = exists(
        select(Event.id).where(
            Event.conversation_id == active.conversation_id,
            Event.run_id == active.id,
            Event.event_type == "message.upsert",
            Event.payload["message"]["id"].astext == cast(Message.id, String),
        )
    )
    message_id = await db.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == active.conversation_id,
            Message.parts.contains([{"type": part["type"], "requestId": part["requestId"]}]),
            linked,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message_id is None:
        return None
    return dict(
        runId=str(active.id),
        messageId=str(message_id),
        requestId=part["requestId"],
        kind=part["type"],
    )


async def load_snapshot_v2(
    db, actor: Actor, conversation_id: UUID, *, cursor_secret: SecretStr
) -> ChatSnapshotV2:
    conversation = await require_conversation(db, actor, conversation_id)
    _, watermark = _position(conversation)
    page = await _page(db, actor, conversation, watermark, None, cursor_secret)
    session = await require_session(db, actor)
    viewer = await load_viewer(db, actor)
    active = await db.scalar(
        select(Run)
        .where(
            Run.conversation_id == conversation.id,
            Run.status.in_(("queued", "running", "waiting_input")),
        )
        .order_by(Run.holds_slot.desc(), Run.updated_at.desc(), Run.id)
        .limit(1)
    )
    event_cursor = await db.scalar(
        select(Event.id)
        .where(Event.conversation_id == conversation.id)
        .order_by(Event.sequence.desc())
        .limit(1)
    )
    return ChatSnapshotV2(
        version=2,
        viewer=viewer,
        csrf={"headerName": CSRF_HEADER, "token": session.csrf_token},
        session_expires_at=session.expires_at,
        conversation_id=str(conversation.id),
        messages=page.messages,
        active_run={"id": str(active.id), "status": active.status} if active else None,
        active_input=await _active_input(db, active),
        merge=await load_merge_state(db, actor, conversation, session),
        history=page.history,
        sync={
            "eventCursor": str(event_cursor) if event_cursor else None,
            "watermarkSequence": watermark,
        },
    )
