import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select, text, update

from xiaohe.authentication import bound_operation
from xiaohe.contracts import Actor, ChatMessage, ServiceError, VerifiedCompletion
from xiaohe.conversations import default_conversation, ensure_welcome, load_snapshot
from xiaohe.database import database_now
from xiaohe.events import record_event
from xiaohe.identity import _cookie, _new_session, _request_session, bind_user, set_session_cookie
from xiaohe.jobs import cancel_run_job, defer_run
from xiaohe.models import (
    Artifact,
    Command,
    Conversation,
    Event,
    EventTombstone,
    GuestClaim,
    Message,
    Run,
    RunAttempt,
    Session,
    User,
)
from xiaohe.runs import TERMINAL


async def _merge(db, claim, source, target):
    if claim.status != "pending":
        return True
    active = await db.scalar(
        select(Run)
        .where(
            Run.conversation_id == target.id,
            Run.holds_slot.is_(True),
        )
        .with_for_update()
    )
    if active is not None and active.status in ("queued", "running"):
        claim.updated_at = await database_now(db)
        return False
    if active is not None:
        active.holds_slot = False
        await db.flush()
    await db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    source_messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == source.id)
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    for message in source_messages:
        if message.client_message_id and await db.scalar(
            select(Message.id).where(
                Message.conversation_id == target.id,
                Message.client_message_id == message.client_message_id,
            )
        ):
            message.client_message_id = None
        message.conversation_id = target.id
    commands = (await db.scalars(select(Command).where(Command.conversation_id == source.id))).all()
    for command in commands:
        if await db.scalar(
            select(Command.id).where(
                Command.conversation_id == target.id,
                Command.kind == command.kind,
                Command.client_key == command.client_key,
            )
        ):
            command.client_key = (
                "claim:"
                + hashlib.sha256(
                    (str(source.id) + command.kind + command.client_key).encode()
                ).hexdigest()
            )
        command.conversation_id = target.id
    old_events = (await db.scalars(select(Event).where(Event.conversation_id == source.id))).all()
    for event in old_events:
        db.add(
            EventTombstone(
                id=event.id,
                conversation_id=source.id,
                system_id=event.system_id,
                tenant_id=event.tenant_id,
                sequence=event.sequence,
            )
        )
    await db.execute(delete(Event).where(Event.conversation_id == source.id))
    await db.execute(
        update(Run)
        .where(Run.conversation_id == source.id)
        .values(
            conversation_id=target.id,
            user_id=claim.user_id,
            guest_id=None,
            session_id=claim.target_session_id,
            ownership_epoch=target.ownership_epoch,
        )
    )
    await db.execute(
        update(RunAttempt)
        .where(RunAttempt.conversation_id == source.id)
        .values(conversation_id=target.id)
    )
    await db.execute(
        update(Artifact)
        .where(Artifact.conversation_id == source.id)
        .values(conversation_id=target.id)
    )
    target.direction = claim.source_direction
    from xiaohe.reception.store import merge_reception

    await merge_reception(db, source, target)
    claim.status, claim.updated_at = "merged", await database_now(db)
    await db.flush()
    target.merge_pending = bool(
        await db.scalar(
            select(GuestClaim.id)
            .where(
                GuestClaim.target_conversation_id == target.id,
                GuestClaim.status == "pending",
                GuestClaim.id != claim.id,
            )
            .limit(1)
        )
    )
    for message in source_messages:
        record_event(
            db,
            target,
            "message.upsert",
            {
                "message": ChatMessage(
                    id=str(message.id),
                    role=message.role,
                    created_at=message.created_at,
                    parts=message.parts,
                ).model_dump(mode="json", by_alias=True)
            },
        )
    if claim.source_run_id is not None and claim.continuation_run_id is None:
        await rebuild_after_claim(db, claim, target)
    await db.flush()
    return True


async def rebuild_after_claim(db, claim, target):
    source_run = await db.get(Run, claim.source_run_id, populate_existing=True)
    session = await db.get(Session, claim.target_session_id)
    user = await db.get(User, claim.user_id)
    if (
        user is None
        or not user.enabled
        or session is None
        or await database_now(db) >= session.expires_at
    ):
        return None
    source_command = await db.scalar(
        select(Command)
        .where(Command.run_id == source_run.id)
        .order_by(Command.created_at.desc(), Command.id.desc())
        .limit(1)
    )
    message = await db.get(Message, source_command.message_id)
    value = "\n".join(part["text"] for part in message.parts if part["type"] == "text")
    run = Run(
        id=uuid4(),
        conversation_id=target.id,
        system_id=claim.system_id,
        tenant_id=claim.tenant_id,
        session_id=session.id,
        user_id=user.user_id,
        guest_id=None,
        execution_expires_at=session.expires_at,
        initial_message_id=message.id,
        ownership_epoch=target.ownership_epoch,
        status="queued",
        holds_slot=True,
    )
    db.add(run)
    await db.flush()
    key = "continuation:" + str(claim.id)
    payload = {"clientMessageId": key, "text": value}
    db.add(
        Command(
            conversation_id=target.id,
            system_id=claim.system_id,
            tenant_id=claim.tenant_id,
            kind="message",
            client_key=key,
            payload_digest=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            payload=payload,
            question=claim.source_question or source_command.question,
            message_id=message.id,
            run_id=run.id,
        )
    )
    await db.flush()
    run.job_id = await defer_run(db, run.id, f"{claim.tenant_id}:{target.id}")
    claim.continuation_run_id = run.id
    record_event(
        db, target, "run.updated", {"run": {"id": str(run.id), "status": "queued"}}, run.id
    )
    return run.id


async def _claim_guest(db, operation, user_session, source_session, source, target):
    existing = await db.scalar(
        select(GuestClaim).where(GuestClaim.guest_id == source_session.guest_id).with_for_update()
    )
    if existing is not None:
        if existing.user_id != user_session.user_id or existing.operation_id != operation.id:
            raise ServiceError("FORBIDDEN", 403)
        return existing
    stamp = await database_now(db)
    expired = stamp >= source_session.expires_at or source_session.revoked_at is not None
    if not expired:
        won = await db.scalar(
            update(Session)
            .where(
                Session.id == source_session.id,
                Session.revoked_at.is_(None),
                Session.expires_at > func.clock_timestamp(),
            )
            .values(revoked_at=func.clock_timestamp())
            .returning(Session.id)
        )
        expired = won is None
    source_run = await db.scalar(
        select(Run)
        .where(Run.conversation_id == source.id, Run.status.not_in(TERMINAL))
        .with_for_update()
    )
    claim = GuestClaim(
        id=uuid4(),
        operation_id=operation.id,
        system_id=operation.system_id,
        tenant_id=operation.tenant_id,
        guest_id=source_session.guest_id,
        user_id=user_session.user_id,
        source_conversation_id=source.id,
        target_conversation_id=target.id,
        target_session_id=user_session.id,
        source_direction=source.direction,
        source_run_id=source_run.id if source_run and not expired else None,
        source_question=source_run.pending_request if source_run and not expired else None,
        status="skipped_expired" if expired else "pending",
    )
    db.add(claim)
    if not expired:
        source.ownership_epoch += 1
        target.merge_pending = True
        if source_run is not None:
            source_run.status, source_run.holds_slot, source_run.pending_request = (
                "cancelled",
                False,
                None,
            )
            source_run.ownership_epoch += 1
            await db.execute(
                update(Command)
                .where(Command.run_id == source_run.id, Command.completed_at.is_(None))
                .values(completed_at=func.clock_timestamp())
            )
            if source_run.job_id is not None:
                await cancel_run_job(db, source_run.job_id)
    await db.flush()
    return claim


async def complete_auth_operation(factory, request, response, operation_id):
    async with factory.begin() as db:
        operation, _, system = await bound_operation(db, request, operation_id, lock=False)
        if operation.verified_completion is None:
            raise ServiceError("AUTH_NOT_READY", 409, True, retry_after=1)
        current = None
        candidate = None
        if _cookie(request):
            candidate = await db.scalar(
                select(Session).where(
                    Session.token_digest == hashlib.sha256(_cookie(request).encode()).hexdigest()
                )
            )
            if operation.bound_user_id and candidate and candidate.user_id is not None:
                candidate = await _request_session(
                    request, system, db, csrf_required=False, lock=False
                )
                if (
                    candidate.user_id == operation.bound_user_id
                    and candidate.created_at == operation.authenticated_session_created_at
                ):
                    current = candidate
        session_ids = ([operation.source_session_id] if operation.source_session_id else []) + (
            [candidate.id] if candidate else []
        )
        if session_ids:
            await db.scalars(
                select(Session)
                .where(Session.id.in_(session_ids))
                .order_by(Session.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        source_session = None
        if operation.source_session_id:
            source_session = await db.get(Session, operation.source_session_id)
        identity = VerifiedCompletion.model_validate(operation.verified_completion)
        user = await bind_user(db, system, identity)
        operation = await db.get(type(operation), operation.id, populate_existing=True)
        if candidate and candidate.user_id is not None:
            if (candidate.system_id, candidate.tenant_id) != (system.system_id, system.tenant_id):
                raise ServiceError("FORBIDDEN", 403)
            live = candidate.revoked_at is None and await database_now(db) < candidate.expires_at
            enabled = await db.scalar(select(User.enabled).where(User.user_id == candidate.user_id))
            original_user = source_session.user_id if source_session else None
            if live and enabled and candidate.user_id not in (user.user_id, original_user):
                raise ServiceError("IDENTITY_MISMATCH", 409)
        anchor = operation.authenticated_session_created_at or await database_now(db)
        if await database_now(db) >= anchor + timedelta(hours=8):
            raise ServiceError("AUTH_REQUIRED", 401)
        if current is not None and current.created_at == anchor:
            user_session, token = current, None
        else:
            user_session, token = await _new_session(
                db,
                system,
                user_id=user.user_id,
                authenticated_at=identity.authenticated_at,
                created_at=anchor,
            )
        actor = Actor(
            system=system, session_id=user_session.id, user_id=user_session.user_id, guest_id=None
        )
        target = await default_conversation(db, actor)
        source = None
        if source_session is not None and source_session.guest_id is not None:
            source = await db.scalar(
                select(Conversation).where(Conversation.guest_id == source_session.guest_id)
            )
        conversation_ids = [target.id] + ([source.id] if source else [])
        await db.scalars(
            select(Conversation)
            .where(Conversation.id.in_(conversation_ids))
            .order_by(Conversation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        operation, _, _ = await bound_operation(db, request, operation_id)
        operation.authenticated_session_created_at = anchor
        operation.stage = "COMPLETED"
        claim = None
        if source is not None:
            claim = await _claim_guest(db, operation, user_session, source_session, source, target)
            user_session.merge_operation_id = operation.id
            if claim.status == "pending":
                await _merge(db, claim, source, target)
        await db.flush()
        await bound_operation(db, request, operation_id, lock=False)
        operation.bound_user_id = user_session.user_id
        await ensure_welcome(db, target)
        snapshot = await load_snapshot(db, actor, target.id)
        if claim is not None and claim.status == "pending":
            from xiaohe.jobs import defer_claim

            claim.job_id = await defer_claim(db, claim)
            response.status_code = 202
            response.headers["Retry-After"] = "1"
        if token is not None:
            set_session_cookie(response, token)
        return snapshot


async def finish_pending_claim(factory, claim_id):
    async with factory.begin() as db:
        claim = await db.get(GuestClaim, claim_id)
        if claim is None or claim.status != "pending":
            return None
        await db.scalar(select(Session).where(Session.guest_id == claim.guest_id).with_for_update())
        user = await db.get(User, claim.user_id)
        if user is None or not user.enabled:
            return None
        conversations = (
            await db.scalars(
                select(Conversation)
                .where(
                    Conversation.id.in_(
                        [claim.source_conversation_id, claim.target_conversation_id]
                    )
                )
                .order_by(Conversation.id)
                .with_for_update()
            )
        ).all()
        claim = await db.scalar(
            select(GuestClaim)
            .where(GuestClaim.id == claim_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        by_id = {conversation.id: conversation for conversation in conversations}
        return await _merge(
            db, claim, by_id[claim.source_conversation_id], by_id[claim.target_conversation_id]
        )
