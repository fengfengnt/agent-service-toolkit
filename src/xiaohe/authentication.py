import hashlib
import hmac
import ipaddress
import json
import math
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from xiaohe.auth_bridge import SsoBridgeClient
from xiaohe.contracts import (
    AgreementAccept,
    AuthContext,
    AuthOperation,
    CaptchaCheck,
    CreateAuthOperation,
    EmptyBody,
    PictureChallenge,
    ServiceError,
    SmsChallenge,
    SmsSend,
    SmsVerify,
    UtcTime,
    VerifiedCompletion,
)
from xiaohe.database import database_now
from xiaohe.identity import _cookie, _request_session, registered_system, resolve_system
from xiaohe.models import AuthContextRecord, AuthOperationRecord, Session

AUTH_COOKIE = "__Host-XIAOHEAUTH"
AUTH_CSRF = "X-Xiaohe-Auth-CSRF"
AUTH_WINDOW = 1200


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _sms_result(operation, stamp):
    result = operation.sms_result
    return SmsChallenge(
        challenge_id=result["challengeId"],
        expires_at=result["expiresAt"],
        retry_after_seconds=max(
            0, math.ceil((operation.sms_started_at + timedelta(seconds=60) - stamp).total_seconds())
        ),
    )


async def auth_system(request, db):
    system = await resolve_system(request, db)
    if not request.app.state.xiaohe_settings.sso_bridge_enabled:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    return system


async def require_auth_context(db, request, *, csrf=True):
    system = await auth_system(request, db)
    token = _cookie(request, AUTH_COOKIE)
    context = (
        None
        if token is None
        else await db.scalar(
            select(AuthContextRecord).where(
                AuthContextRecord.token_digest == _digest(token),
                AuthContextRecord.system_id == system.system_id,
                AuthContextRecord.tenant_id == system.tenant_id,
                AuthContextRecord.origin == system.allowed_origin,
            )
        )
    )
    if context is None or await database_now(db) >= context.expires_at:
        raise ServiceError("CSRF_INVALID", 403)
    supplied = request.headers.getlist(AUTH_CSRF)
    if csrf and (
        len(supplied) != 1
        or not supplied[0].isascii()
        or not secrets.compare_digest(supplied[0], context.csrf_token)
    ):
        raise ServiceError("CSRF_INVALID", 403)
    return context, system


async def ensure_auth_context(factory, request, response):
    async with factory.begin() as db:
        system = await auth_system(request, db)
        token = _cookie(request, AUTH_COOKIE)
        context = (
            None
            if token is None
            else await db.scalar(
                select(AuthContextRecord).where(
                    AuthContextRecord.token_digest == _digest(token),
                    AuthContextRecord.system_id == system.system_id,
                    AuthContextRecord.tenant_id == system.tenant_id,
                    AuthContextRecord.origin == system.allowed_origin,
                )
            )
        )
        stamp = await database_now(db)
        if context is None or stamp >= context.expires_at:
            token = secrets.token_urlsafe(32)
            context = AuthContextRecord(
                system_id=system.system_id,
                tenant_id=system.tenant_id,
                origin=system.allowed_origin,
                token_digest=_digest(token),
                csrf_token=secrets.token_hex(32),
                created_at=stamp,
                expires_at=stamp + timedelta(seconds=AUTH_WINDOW),
            )
            db.add(context)
            response.set_cookie(
                AUTH_COOKIE, token, secure=True, httponly=True, samesite="lax", path="/"
            )
        return AuthContext(
            csrf={"headerName": AUTH_CSRF, "token": context.csrf_token},
            expires_at=context.expires_at,
        )


def _visitor_headers(request):
    values = request.headers.getlist("X-Portal-Client-IP")
    agents = request.headers.getlist("User-Agent")
    try:
        if len(values) != 1 or len(agents) > 1:
            raise ValueError()
        ip = str(ipaddress.ip_address(values[0]))
        ua = agents[0] if agents else ""
        if len(ua) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in ua):
            raise ValueError()
    except ValueError:
        raise ServiceError("FORBIDDEN", 403) from None
    return ip, ua


async def bound_operation(db, request, operation_id, *, lock=True, allow_cancelled=False):
    context, system = await require_auth_context(db, request)
    operation = await db.get(AuthOperationRecord, operation_id)
    if operation is None or (operation.system_id, operation.tenant_id) != (
        system.system_id,
        system.tenant_id,
    ):
        raise ServiceError("FORBIDDEN", 403)
    account_authorized = False
    if operation.bound_user_id is not None and _cookie(request) is not None:
        current = await db.scalar(
            select(Session).where(Session.token_digest == _digest(_cookie(request)))
        )
        if current is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        if current.user_id is not None:
            session = await _request_session(request, system, db, csrf_required=False, lock=False)
            if session.user_id != operation.bound_user_id:
                raise ServiceError("FORBIDDEN", 403)
            account_authorized = True
    if context.id != operation.context_id and not account_authorized:
        raise ServiceError("FORBIDDEN", 403)
    if lock:
        operation = await db.scalar(
            select(AuthOperationRecord)
            .where(AuthOperationRecord.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if operation.cancelled_at is not None and not allow_cancelled:
        raise ServiceError("AUTH_REPLAY", 409)
    if not account_authorized and await database_now(db) >= operation.expires_at:
        raise ServiceError("AUTH_OPERATION_EXPIRED", 410)
    return operation, context, system


def _view(operation, center):
    try:
        if str(UUID(center["transactionId"])) != operation.center_transaction_id:
            raise ValueError()
        stage = center["stage"]
        completion = None
        agreement = None
        if stage in ("CREATED", "SMS_SENT"):
            public_stage = "PHONE_REQUIRED"
            if operation.intent == "restore" and stage == "CREATED":
                public_stage = "BROWSER_COMPLETION_REQUIRED"
                completion = {
                    "method": "POST",
                    "action": operation.oidc["issuer"] + "/sso/bridge/browser-completion",
                    "fields": {"transactionId": operation.center_transaction_id},
                }
        elif stage == "AGREEMENT_REQUIRED":
            public_stage = "AGREEMENT_REQUIRED"
            path = center["agreementPath"]
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or path.startswith("//")
                or urlsplit(path).netloc
            ):
                raise ValueError()
            agreement = {
                "version": center["agreementVersion"],
                "url": operation.oidc["issuer"] + path,
            }
        elif stage == "COMPLETION_READY":
            public_stage = "BROWSER_COMPLETION_REQUIRED"
            credential = center.get("credential")
            if not isinstance(credential, str) or not credential:
                raise ValueError()
            completion = {
                "method": "POST",
                "action": operation.oidc["issuer"] + "/sso/bridge/browser-completion",
                "fields": {
                    "transactionId": operation.center_transaction_id,
                    "credential": credential,
                },
            }
        elif stage in ("BROWSER_PENDING", "AUTHORIZING", "EXCHANGED"):
            public_stage = "OIDC_REQUIRED"
        else:
            raise ValueError()
        values = {
            "operationId": str(operation.id),
            "stage": public_stage,
            "expiresAt": min(operation.expires_at, operation.center_expires_at),
        }
        if stage == "COMPLETION_READY":
            values["expiresAt"] = min(
                values["expiresAt"],
                TypeAdapter(UtcTime).validate_python(center["credentialExpiresAt"]),
            )
        if agreement:
            values["agreement"] = agreement
        if completion:
            values["completion"] = completion
        result = AuthOperation.model_validate(values)
    except (KeyError, ValueError, TypeError):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
    operation.stage = public_stage
    return result


class AuthService:
    def __init__(self, factory, client):
        self.factory, self.client = factory, client

    async def create(self, request, body):
        config = request.app.state.xiaohe_settings
        async with self.factory.begin() as db:
            context, system = await require_auth_context(db, request)
            registered = await registered_system(db, system)
            if (
                body.oidc.client_id != system.portal_client_id
                or body.oidc.redirect_uri != config.sso_bridge_redirect_uri
                or registered.issuer != config.sso_bridge_base_url
            ):
                raise ServiceError("IDENTITY_MISMATCH", 409)
            ip, ua = _visitor_headers(request)
            payload = body.model_dump(mode="json", by_alias=True)
            digest = _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            token = _cookie(request)
            source = (
                None
                if token is None
                else await db.scalar(
                    select(Session).where(
                        Session.token_digest == _digest(token),
                        Session.system_id == system.system_id,
                        Session.tenant_id == system.tenant_id,
                        Session.revoked_at.is_(None),
                    )
                )
            )
            stamp = await database_now(db)
            await db.execute(
                insert(AuthOperationRecord)
                .values(
                    id=uuid4(),
                    context_id=context.id,
                    system_id=system.system_id,
                    tenant_id=system.tenant_id,
                    source_session_id=source.id if source else None,
                    intent=body.intent,
                    oidc=body.oidc.model_dump(mode="json", by_alias=True)
                    | {"issuer": registered.issuer},
                    browser_challenge=body.browser_completion_challenge,
                    request_digest=digest,
                    client_ip=ip,
                    user_agent=ua,
                    created_at=stamp,
                    expires_at=stamp + timedelta(seconds=AUTH_WINDOW),
                )
                .on_conflict_do_nothing(index_elements=["context_id", "request_digest"])
            )
            operation_id = await db.scalar(
                select(AuthOperationRecord.id).where(
                    AuthOperationRecord.context_id == context.id,
                    AuthOperationRecord.request_digest == digest,
                )
            )
        # Commit the stable operation key before any network side effect.
        async with self.factory.begin() as db:
            operation, context, _ = await bound_operation(db, request, operation_id)
            oidc = operation.oidc
            center = await self.client.request(
                "POST",
                "/transactions",
                body={
                    "portalClientId": oidc["clientId"],
                    "redirectUri": oidc["redirectUri"],
                    "origin": context.origin,
                    "state": oidc["state"],
                    "nonce": oidc["nonce"],
                    "codeChallenge": oidc["codeChallenge"],
                    "codeChallengeMethod": "S256",
                    "browserVerifierChallenge": operation.browser_challenge,
                },
                key=str(operation.id),
                client_ip=operation.client_ip,
                user_agent=operation.user_agent,
            )
            try:
                transaction_id = str(UUID(center["transactionId"]))
                expires = TypeAdapter(UtcTime).validate_python(center["expiresAt"])
                if operation.center_transaction_id not in (None, transaction_id):
                    raise ValueError()
                if expires > operation.created_at + timedelta(seconds=AUTH_WINDOW):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
            operation.center_transaction_id = transaction_id
            operation.center_expires_at = (
                min(operation.center_expires_at, expires)
                if operation.center_expires_at
                else expires
            )
            context.expires_at = max(context.expires_at, expires + timedelta(seconds=600))
            return _view(operation, center)

    async def verify_completion(self, request, operation_id):
        async with self.factory.begin() as db:
            operation, context, system = await bound_operation(db, request, operation_id)
            if operation.verified_completion is None:
                if operation.center_transaction_id is None:
                    raise ServiceError("AUTH_NOT_READY", 409, True, retry_after=1)
                result = await self.client.claim(
                    system,
                    operation.center_transaction_id,
                    client_ip=operation.client_ip,
                    user_agent=operation.user_agent,
                )
                operation.verified_completion = result.model_dump(mode="json", by_alias=True)
            return VerifiedCompletion.model_validate(operation.verified_completion)

    async def send_sms(self, request, operation_id, body):
        key_material = request.app.state.xiaohe_settings.auth_secret.get_secret_value().encode()

        def digest(value):
            return hmac.new(
                key_material, (str(operation_id) + value).encode(), hashlib.sha256
            ).hexdigest()

        fingerprint = digest(json.dumps(body.model_dump(mode="json"), sort_keys=True))
        phone_digest = digest("mobile:" + body.mobile.get_secret_value())
        async with self.factory.begin() as db:
            operation, _, _ = await bound_operation(db, request, operation_id)
            if operation.verified_completion or operation.intent == "restore":
                raise ServiceError("AUTH_REPLAY", 409)
            if fingerprint in operation.retired_sms_digests:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            stamp = await database_now(db)
            result = operation.sms_result or {}
            ready = "challengeId" in result
            if operation.sms_key and result.get("phoneDigest") != phone_digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            if ready and operation.sms_request_digest == fingerprint:
                return _sms_result(operation, stamp)
            if operation.sms_request_digest and operation.sms_request_digest != fingerprint:
                operation.retired_sms_digests = [
                    *operation.retired_sms_digests,
                    operation.sms_request_digest,
                ]
            operation.sms_request_digest = fingerprint
            if not operation.sms_key or (
                ready and stamp >= operation.sms_started_at + timedelta(seconds=60)
            ):
                operation.sms_key = str(uuid4())
                operation.sms_started_at = stamp
                operation.sms_result = {"phoneDigest": phone_digest}
                ready = False
            if ready:
                return _sms_result(operation, stamp)
            send_key = operation.sms_key
        async with self.factory.begin() as db:
            operation, _, _ = await bound_operation(db, request, operation_id)
            if operation.sms_request_digest != fingerprint or operation.sms_key != send_key:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            if "challengeId" in operation.sms_result:
                return _sms_result(operation, await database_now(db))
            if (
                operation.center_transaction_id is None
                or await database_now(db) >= operation.center_expires_at
            ):
                raise ServiceError("AUTH_OPERATION_EXPIRED", 410)
            center = await self.client.request(
                "POST",
                f"/transactions/{operation.center_transaction_id}/sms",
                body=body.model_dump(mode="json", by_alias=True),
                key=send_key,
                client_ip=operation.client_ip,
                user_agent=operation.user_agent,
            )
            _view(operation, center)
            expires = min(
                operation.center_expires_at, operation.sms_started_at + timedelta(seconds=300)
            )
            result = SmsChallenge(
                challenge_id=center["challengeId"], retry_after_seconds=60, expires_at=expires
            )
            operation.sms_result = result.model_dump(mode="json", by_alias=True) | {
                "phoneDigest": phone_digest
            }
            return _sms_result(operation, await database_now(db))

    async def advance(self, request, operation_id, action, body):
        if action == "sms":
            return await self.send_sms(request, operation_id, body)
        async with self.factory.begin() as db:
            operation, _, _ = await bound_operation(
                db, request, operation_id, allow_cancelled=action == "cancel"
            )
            if action == "cancel" and operation.cancelled_at:
                return None
            if operation.verified_completion and action != "cancel":
                raise ServiceError("AUTH_REPLAY", 409)
            if action == "cancel" and operation.bound_user_id:
                return None
            if operation.center_transaction_id is None:
                raise ServiceError("AUTH_NOT_READY", 409, True, retry_after=1)
            path = f"/transactions/{operation.center_transaction_id}/{action}"
            payload = body.model_dump(mode="json", by_alias=True)
            if action in ("captcha", "captcha/check"):
                if action == "captcha/check":
                    payload["captchaType"] = "blockPuzzle"
                center = await self.client.request(
                    "GET" if action == "captcha" else "POST",
                    path,
                    body=None if action == "captcha" else payload,
                    params={"captchaType": "blockPuzzle"} if action == "captcha" else None,
                    client_ip=operation.client_ip,
                    user_agent=operation.user_agent,
                )
                if not isinstance(center, dict) or center.get("repCode") != "0000":
                    raise ServiceError("INPUT_INVALID", 422)
                if action == "captcha/check":
                    return {"valid": True}
                data = center.get("repData", {})
                try:
                    return PictureChallenge(
                        token=data["token"],
                        secret_key=data["secretKey"],
                        image_base64=data["originalImageBase64"],
                        piece_image_base64=data["jigsawImageBase64"],
                    )
                except (KeyError, ValueError, TypeError):
                    raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
            center = await self.client.request(
                "POST",
                path,
                body=payload,
                client_ip=operation.client_ip,
                user_agent=operation.user_agent,
            )
            if action == "cancel":
                if operation.bound_user_id is None:
                    operation.cancelled_at = await database_now(db)
                return None
            return _view(operation, center)


def create_auth_router(factory):
    from xiaohe.api import _body

    router = APIRouter()

    def service(request):
        if not hasattr(request.app.state, "xiaohe_sso"):
            request.app.state.xiaohe_sso = SsoBridgeClient(request.app.state.xiaohe_settings)
        return AuthService(factory, request.app.state.xiaohe_sso)

    @router.post("/auth/context")
    async def context(request: Request, response: Response):
        await _body(request, EmptyBody)
        return await ensure_auth_context(factory, request, response)

    @router.post("/auth/operations", status_code=201)
    async def create(request: Request):
        return await service(request).create(request, await _body(request, CreateAuthOperation))

    @router.post("/auth/operations/{operation_id}/complete")
    async def complete(operation_id: UUID, request: Request, response: Response):
        await _body(request, EmptyBody)
        await service(request).verify_completion(request, operation_id)
        from xiaohe.claim import complete_auth_operation

        return await complete_auth_operation(factory, request, response, operation_id)

    @router.post("/auth/operations/{operation_id}/{action:path}")
    async def advance(operation_id: UUID, action: str, request: Request):
        models = {
            "captcha": EmptyBody,
            "captcha/check": CaptchaCheck,
            "sms": SmsSend,
            "verify": SmsVerify,
            "agreement": AgreementAccept,
            "cancel": EmptyBody,
        }
        if action not in models:
            raise ServiceError("INPUT_INVALID", 422)
        result = await service(request).advance(
            request, operation_id, action, await _body(request, models[action])
        )
        return Response(status_code=204) if result is None else result

    return router
