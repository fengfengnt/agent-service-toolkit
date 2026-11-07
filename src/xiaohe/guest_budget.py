import base64
import hashlib
import hmac
import secrets
from uuid import UUID, uuid4

from fastapi import Request, Response
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import Actor, ServiceError, TrustedSystem
from xiaohe.database import database_now
from xiaohe.identity import _actor, _cookie, _new_session, set_session_cookie
from xiaohe.models import (
    Conversation,
    GuestBudget,
    GuestBudgetCredential,
    GuestBudgetReceipt,
    GuestClaim,
    Session,
)

BUDGET_COOKIE = "__Host-XIAOHEBUDGET"
COOKIE_SECONDS = 31536000


async def load_guest_budget(db: AsyncSession, session: Session, *, lock: bool) -> GuestBudget:
    if session.budget_id is None:
        return await adopt_legacy_guest(db, session)
    query = (
        select(GuestBudget)
        .where(
            GuestBudget.id == session.budget_id,
            GuestBudget.system_id == session.system_id,
            GuestBudget.tenant_id == session.tenant_id,
        )
        .execution_options(populate_existing=True)
    )
    budget = await db.scalar(query.with_for_update() if lock else query)
    if budget is None or session.guest_id is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    return budget


async def adopt_legacy_guest(db: AsyncSession, session: Session) -> GuestBudget:
    if session.budget_id is not None:
        return await load_guest_budget(db, session, lock=True)
    if (
        session.guest_id is None
        or session.revoked_at is not None
        or session.merge_operation_id is not None
        or type(session.message_count) is not int
        or type(session.message_limit) is not int
        or not 0 <= session.message_count <= session.message_limit <= 2**53 - 1
        or session.message_limit == 0
        or await db.scalar(select(GuestClaim.id).where(GuestClaim.guest_id == session.guest_id))
    ):
        raise ServiceError("AUTH_REQUIRED", 401)
    budget = GuestBudget(
        system_id=session.system_id,
        tenant_id=session.tenant_id,
        message_count=session.message_count,
        message_limit=session.message_limit,
        current_session_id=session.id,
    )
    db.add(budget)
    await db.flush()
    session.budget_id = budget.id
    await db.flush()
    return budget


async def charge_guest_message(
    db: AsyncSession, session: Session, *, kind: str, client_key: str, payload_digest: str
) -> GuestBudget:
    budget = await load_guest_budget(db, session, lock=True)
    key_digest = hashlib.sha256(client_key.encode()).hexdigest()
    receipt = await db.get(GuestBudgetReceipt, (budget.id, kind, key_digest))
    if receipt is not None:
        # Commands remain the response source; this minimal ledger must never recreate old input.
        raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
    count = await db.scalar(
        update(GuestBudget)
        .where(
            GuestBudget.id == budget.id,
            GuestBudget.message_count < GuestBudget.message_limit,
        )
        .values(message_count=GuestBudget.message_count + 1)
        .returning(GuestBudget.message_count)
        .execution_options(synchronize_session=False)
    )
    if count is None:
        raise ServiceError("VISITOR_LIMIT_REACHED", 429)
    changed = await db.scalar(
        update(Session)
        .where(
            Session.id == session.id,
            Session.revoked_at.is_(None),
            Session.expires_at > func.clock_timestamp(),
        )
        .values(
            message_count=count,
            expires_at=func.clock_timestamp()
            + Session.retention_seconds * text("interval '1 second'"),
        )
        .returning(Session.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        from xiaohe.identity import _live

        await db.refresh(session)
        await _live(db, session)
        raise ServiceError("AUTH_REQUIRED", 401)
    db.add(
        GuestBudgetReceipt(
            budget_id=budget.id,
            kind=kind,
            client_key_digest=key_digest,
            payload_digest=payload_digest,
            session_id=session.id,
            system_id=session.system_id,
            tenant_id=session.tenant_id,
        )
    )
    await db.flush()
    await db.refresh(budget)
    return budget


def _episode_token(request: Request, session_id: UUID) -> SecretStr:
    # Concurrent reopeners must recover the same new cookie without storing its plaintext.
    secret = request.app.state.xiaohe_settings.auth_secret.get_secret_value().encode()
    digest = hmac.digest(secret, b"xiaohe.guest-episode.v2\0" + session_id.bytes, "sha256")
    return SecretStr(base64.urlsafe_b64encode(digest).decode().rstrip("="))


class _PointerChanged(Exception):
    pass


async def ensure_budget_credential(db, request, response, budget):
    token = _cookie(request, BUDGET_COOKIE)
    if token is not None:
        credential = await db.get(
            GuestBudgetCredential,
            hashlib.sha256(token.encode()).hexdigest(),
            populate_existing=True,
        )
        if (
            credential is None
            or credential.revoked_at is not None
            or (credential.budget_id, credential.system_id, credential.tenant_id)
            != (budget.id, budget.system_id, budget.tenant_id)
        ):
            raise ServiceError("AUTH_REQUIRED", 401)
    else:
        token = secrets.token_urlsafe(32)
        db.add(
            GuestBudgetCredential(
                token_digest=hashlib.sha256(token.encode()).hexdigest(),
                budget_id=budget.id,
                system_id=budget.system_id,
                tenant_id=budget.tenant_id,
            )
        )
        await db.flush()
    response.set_cookie(
        BUDGET_COOKIE,
        token,
        max_age=COOKIE_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def bootstrap_guest_episode(
    db: AsyncSession, request: Request, response: Response, system: TrustedSystem
) -> Actor:
    from xiaohe.identity import SESSION_COOKIE

    content_token = _cookie(request, SESSION_COOKIE)
    budget_token = _cookie(request, BUDGET_COOKIE)
    for _ in range(3):
        try:
            async with db.begin_nested():
                return await _bootstrap(db, request, response, system, content_token, budget_token)
        except _PointerChanged:
            # Rolling back the savepoint releases locks before following a new session pointer.
            continue
    raise ServiceError("AUTH_NOT_READY", 503, True, retry_after=1)


async def _bootstrap(db, request, response, system, content_token, budget_token):
    incoming = None
    if content_token is not None:
        incoming = await db.scalar(
            select(Session)
            .where(Session.token_digest == hashlib.sha256(content_token.encode()).hexdigest())
            .execution_options(populate_existing=True)
        )
        if incoming is None or incoming.guest_id is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if (incoming.system_id, incoming.tenant_id) != (system.system_id, system.tenant_id):
            raise ServiceError("FORBIDDEN", 403)
    budget = None
    if budget_token is not None:
        credential = await db.get(
            GuestBudgetCredential,
            hashlib.sha256(budget_token.encode()).hexdigest(),
            populate_existing=True,
        )
        if credential is None or credential.revoked_at is not None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if (credential.system_id, credential.tenant_id) != (system.system_id, system.tenant_id):
            raise ServiceError("FORBIDDEN", 403)
        budget = await db.get(GuestBudget, credential.budget_id, populate_existing=True)
        if budget is None or (incoming is not None and incoming.budget_id != budget.id):
            raise ServiceError("AUTH_REQUIRED", 401)
    elif incoming is not None:
        if incoming.revoked_at is not None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if incoming.budget_id is not None:
            budget = await db.get(GuestBudget, incoming.budget_id, populate_existing=True)
            if budget is None:
                raise ServiceError("AUTH_REQUIRED", 401)

    pointer = budget.current_session_id if budget is not None else None
    session_ids = {
        value for value in (pointer, incoming.id if incoming is not None else None) if value
    }
    locked = {
        row.id: row
        for row in await db.scalars(
            select(Session)
            .where(Session.id.in_(session_ids))
            .order_by(Session.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    # All existing session/conversation locks precede the budget lock.
    guest_ids = [row.guest_id for row in locked.values() if row.guest_id is not None]
    list(
        await db.scalars(
            select(Conversation)
            .where(Conversation.guest_id.in_(guest_ids))
            .order_by(Conversation.id)
            .with_for_update()
        )
    )
    if budget is not None:
        budget = await db.scalar(
            select(GuestBudget)
            .where(GuestBudget.id == budget.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if budget.current_session_id != pointer:
            raise _PointerChanged()
        # A concurrent credential revocation must not authorize a new episode.
        if budget_token is not None:
            await db.refresh(credential)
            if credential.revoked_at is not None:
                raise ServiceError("AUTH_REQUIRED", 401)
    if incoming is not None:
        incoming = locked[incoming.id]
        if budget is None:
            if incoming.budget_id is not None:
                raise _PointerChanged()
            budget = await adopt_legacy_guest(db, incoming)
            pointer = incoming.id
        elif incoming.budget_id != budget.id:
            raise ServiceError("AUTH_REQUIRED", 401)
    if budget is None:
        settings = request.app.state.xiaohe_settings
        budget = GuestBudget(
            system_id=system.system_id,
            tenant_id=system.tenant_id,
            message_count=0,
            message_limit=settings.visitor_message_limit,
        )
        db.add(budget)
        await db.flush()

    current = locked.get(pointer)
    if current is not None and (current.guest_id is None or current.budget_id != budget.id):
        raise ServiceError("AUTH_REQUIRED", 401)
    now = await database_now(db)
    if current is not None and current.revoked_at is None and now < current.expires_at:
        if incoming is None or incoming.revoked_at is not None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if incoming.id == current.id:
            token = SecretStr(content_token)
        else:
            if now < incoming.expires_at:
                raise ServiceError("AUTH_REQUIRED", 401)
            token = _episode_token(request, current.id)
            if (
                hashlib.sha256(token.get_secret_value().encode()).hexdigest()
                != current.token_digest
            ):
                raise ServiceError("AUTH_REQUIRED", 401)
    else:
        if budget.message_count >= budget.message_limit:
            raise ServiceError("VISITOR_LIMIT_REACHED", 429)
        settings = request.app.state.xiaohe_settings
        session_id = uuid4()
        token = _episode_token(request, session_id)
        current, _ = await _new_session(
            db,
            system,
            message_limit=budget.message_limit,
            retention_seconds=settings.visitor_retention_seconds,
            session_id=session_id,
            token=token,
        )
        current.budget_id = budget.id
        current.message_count = budget.message_count
        budget.current_session_id = current.id
        await db.flush()
    set_session_cookie(response, token, guest=True)
    await ensure_budget_credential(db, request, response, budget)
    return _actor(current, system)
