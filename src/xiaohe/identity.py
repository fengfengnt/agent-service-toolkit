import hashlib
import re
import secrets
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from uuid import uuid4

from fastapi import Request, Response
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import Actor, ChatSnapshot, ServiceError, TrustedSystem, VerifiedCompletion
from xiaohe.database import database_now
from xiaohe.models import Session, System, User

SESSION_COOKIE = "__Host-XIAOHESESSION"
CSRF_HEADER = "X-Xiaohe-CSRF"
USER_SESSION_SECONDS = 28800


def _trusted(record: System) -> TrustedSystem:
    return TrustedSystem(
        system_id=record.system_id,
        tenant_id=record.tenant_id,
        portal_client_id=record.portal_client_id,
        allowed_origin=record.allowed_origin,
    )


async def registered_system(db: AsyncSession, system: TrustedSystem) -> System:
    record = await db.scalar(
        select(System)
        .where(System.system_id == system.system_id, System.enabled.is_(True))
        .execution_options(populate_existing=True)
    )
    if record is None or _trusted(record) != system:
        raise ServiceError("FORBIDDEN", 403)
    return record


async def resolve_system(request: Request, db: AsyncSession) -> TrustedSystem:
    settings = request.app.state.xiaohe_settings
    peer = request.client.host if request.client else ""
    try:
        trusted = any(
            ip_address(peer) in ip_network(value) for value in settings.trusted_proxy_cidrs
        )
    except ValueError:
        trusted = False
    systems = request.headers.getlist("X-Xiaohe-System-Id")
    if not trusted or request.url.scheme != "https" or len(systems) != 1:
        raise ServiceError("FORBIDDEN", 403)
    record = await db.scalar(
        select(System)
        .where(System.system_id == systems[0], System.enabled.is_(True))
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise ServiceError("FORBIDDEN", 403)
    origins = request.headers.getlist("Origin")
    if (request.method not in ("GET", "HEAD", "OPTIONS") or origins) and origins != [
        record.allowed_origin
    ]:
        raise ServiceError("FORBIDDEN", 403)
    return _trusted(record)


def _cookie(request: Request, name: str = SESSION_COOKIE) -> str | None:
    values = []
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            key, separator, value = part.strip().partition("=")
            if key == name:
                values.append(value if separator else "")
    if not values:
        return None
    if len(values) != 1 or re.fullmatch(r"[A-Za-z0-9_-]{43}", values[0]) is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    return values[0]


def _actor(record: Session, system: TrustedSystem) -> Actor:
    return Actor(
        system=system, session_id=record.id, user_id=record.user_id, guest_id=record.guest_id
    )


async def _live(db: AsyncSession, record: Session) -> None:
    if record.revoked_at is not None:
        raise ServiceError("AUTH_REQUIRED", 401)
    if await database_now(db) >= record.expires_at:
        if record.guest_id is not None:
            raise ServiceError("VISITOR_EXPIRED", 410)
        raise ServiceError("AUTH_REQUIRED", 401)
    if record.user_id is not None:
        enabled = await db.scalar(
            select(User.enabled).where(
                User.user_id == record.user_id,
                User.system_id == record.system_id,
                User.tenant_id == record.tenant_id,
            )
        )
        if enabled is not True:
            raise ServiceError("AUTH_REQUIRED", 401)


async def require_session(db: AsyncSession, actor: Actor) -> Session:
    await registered_system(db, actor.system)
    record = await db.scalar(
        select(Session)
        .where(Session.id == actor.session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    if (
        record.system_id != actor.system.system_id
        or record.tenant_id != actor.system.tenant_id
        or record.user_id != actor.user_id
        or record.guest_id != actor.guest_id
    ):
        raise ServiceError("FORBIDDEN", 403)
    await _live(db, record)
    return record


async def _request_session(
    request: Request,
    system: TrustedSystem,
    db: AsyncSession,
    *,
    csrf_required: bool,
    lock: bool = True,
) -> Session:
    token = _cookie(request)
    if token is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    digest = hashlib.sha256(token.encode()).hexdigest()
    query = (
        select(Session)
        .where(Session.token_digest == digest)
        .execution_options(populate_existing=True)
    )
    record = await db.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    if record.system_id != system.system_id or record.tenant_id != system.tenant_id:
        raise ServiceError("FORBIDDEN", 403)
    await _live(db, record)
    if csrf_required and request.method not in ("GET", "HEAD", "OPTIONS"):
        values = request.headers.getlist(CSRF_HEADER)
        if (
            len(values) != 1
            or not values[0].isascii()
            or not secrets.compare_digest(values[0], record.csrf_token)
        ):
            raise ServiceError("CSRF_INVALID", 403)
    return record


async def resolve_actor(request: Request, system: TrustedSystem, db: AsyncSession) -> Actor:
    if await resolve_system(request, db) != system:
        raise ServiceError("FORBIDDEN", 403)
    return _actor(await _request_session(request, system, db, csrf_required=True), system)


def set_session_cookie(response: Response, token: SecretStr) -> None:
    # Keep an expired token recognizable; client expiry would silently create a new guest.
    response.set_cookie(
        SESSION_COOKIE,
        token.get_secret_value(),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def _new_session(
    db: AsyncSession,
    system: TrustedSystem,
    *,
    user_id: str | None = None,
    authenticated_at: datetime | None = None,
    message_limit: int = 100,
    retention_seconds: int = 14400,
    created_at: datetime | None = None,
) -> tuple[Session, SecretStr]:
    token = SecretStr(secrets.token_urlsafe(32))
    guest_id = uuid4() if user_id is None else None
    stamp = created_at or await database_now(db)
    if user_id is not None and await database_now(db) >= stamp + timedelta(
        seconds=USER_SESSION_SECONDS
    ):
        raise ServiceError("AUTH_REQUIRED", 401)
    record = Session(
        id=uuid4(),
        system_id=system.system_id,
        tenant_id=system.tenant_id,
        user_id=user_id,
        authenticated_at=authenticated_at,
        guest_id=guest_id,
        token_digest=hashlib.sha256(token.get_secret_value().encode()).hexdigest(),
        csrf_token=secrets.token_hex(32),
        toolkit_user_id=user_id or f"guest:{guest_id}",
        created_at=stamp,
        expires_at=stamp
        + timedelta(seconds=(USER_SESSION_SECONDS if user_id is not None else retention_seconds)),
        message_count=0,
        message_limit=message_limit,
        retention_seconds=retention_seconds,
    )
    db.add(record)
    await db.flush()
    return record, token


async def bind_user(
    db: AsyncSession,
    system: TrustedSystem,
    identity: VerifiedCompletion,
) -> User:
    registered = await registered_system(db, system)
    if (
        identity.issuer != registered.issuer
        or identity.portal_client_id != registered.portal_client_id
    ):
        raise ServiceError("IDENTITY_MISMATCH", 409)
    await db.execute(
        insert(User)
        .values(
            user_id=str(uuid4()),
            system_id=system.system_id,
            tenant_id=system.tenant_id,
            issuer=identity.issuer,
            sub=identity.sub,
            source_tenant_id=identity.source_tenant_id,
        )
        .on_conflict_do_nothing(constraint="uq_user_external_identity")
    )
    user = await db.scalar(
        select(User)
        .where(
            User.issuer == identity.issuer,
            User.sub == identity.sub,
            User.source_tenant_id == identity.source_tenant_id,
        )
        .with_for_update()
    )
    if user.system_id != system.system_id or user.tenant_id != system.tenant_id:
        raise ServiceError("IDENTITY_MISMATCH", 409)
    if not user.enabled:
        raise ServiceError("AUTH_REQUIRED", 401)
    return user


async def create_user_session(
    db: AsyncSession,
    system: TrustedSystem,
    identity: VerifiedCompletion,
    *,
    created_at: datetime | None = None,
) -> tuple[Session, SecretStr]:
    user = await bind_user(db, system, identity)
    return await _new_session(
        db,
        system,
        user_id=user.user_id,
        authenticated_at=identity.authenticated_at,
        created_at=created_at,
    )


async def bootstrap_session(request: Request, response: Response, db: AsyncSession) -> ChatSnapshot:
    from xiaohe.conversations import default_conversation, ensure_welcome, load_snapshot

    if request.method != "POST":
        raise ServiceError("FORBIDDEN", 403)
    system = await resolve_system(request, db)
    if _cookie(request) is None:
        settings = request.app.state.xiaohe_settings
        record, token = await _new_session(
            db,
            system,
            message_limit=settings.visitor_message_limit,
            retention_seconds=settings.visitor_retention_seconds,
        )
        set_session_cookie(response, token)
    else:
        # Reopening obtains the CSRF token; exact Origin is still mandatory for POST /session.
        record = await _request_session(request, system, db, csrf_required=False)
    actor = _actor(record, system)
    conversation = await default_conversation(db, actor)
    await ensure_welcome(db, conversation)
    return await load_snapshot(db, actor, conversation.id)


async def logout_session(request: Request, response: Response, db: AsyncSession) -> None:
    system = await resolve_system(request, db)
    token = _cookie(request)
    record = None if token is None else await db.scalar(
        select(Session)
        .where(
            Session.token_digest == hashlib.sha256(token.encode()).hexdigest(),
            Session.system_id == system.system_id,
            Session.tenant_id == system.tenant_id,
        )
        .with_for_update()
    )
    if record is not None and record.user_id is not None and (
        record.revoked_at is not None or await database_now(db) >= record.expires_at
    ):
        # An expired member cannot obtain chat CSRF; a fresh auth context only permits logout.
        from xiaohe.authentication import require_auth_context

        await require_auth_context(db, request)
    else:
        actor = await resolve_actor(request, system, db)
        record = await require_session(db, actor)
    if record.user_id is not None:
        from xiaohe.models import AuthContextRecord, AuthOperationRecord

        record.revoked_at = record.revoked_at or await database_now(db)
        operations = await db.scalars(
            select(AuthOperationRecord)
            .where(
                AuthOperationRecord.bound_user_id == record.user_id,
                AuthOperationRecord.authenticated_session_created_at == record.created_at,
            )
            .with_for_update()
        )
        for operation in operations:
            operation.cancelled_at = record.revoked_at
            context = await db.get(AuthContextRecord, operation.context_id)
            context.expires_at = min(context.expires_at, record.revoked_at)
        from xiaohe.authentication import AUTH_COOKIE

        try:
            token = _cookie(request, AUTH_COOKIE)
        except ServiceError:
            token = None
        if token is not None:
            context = await db.scalar(
                select(AuthContextRecord)
                .where(
                    AuthContextRecord.token_digest == hashlib.sha256(token.encode()).hexdigest(),
                    AuthContextRecord.system_id == system.system_id,
                    AuthContextRecord.tenant_id == system.tenant_id,
                )
                .with_for_update()
            )
            if context is not None:
                context.expires_at = min(context.expires_at, record.revoked_at)
        response.delete_cookie(AUTH_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        await db.flush()
