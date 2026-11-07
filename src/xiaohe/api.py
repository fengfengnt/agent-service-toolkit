from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders

from xiaohe.admission import (
    accept_action,
    accept_agent_action,
    accept_agent_message,
    accept_message,
)
from xiaohe.contracts import ApiError, ChatAction, EmptyBody, MessageInput, ServiceError
from xiaohe.contracts_v2 import BootstrapInputV2
from xiaohe.conversations import load_snapshot
from xiaohe.events import stream_events
from xiaohe.identity import (
    bootstrap_session,
    logout_session,
    require_session,
    require_session_csrf,
    resolve_actor,
    resolve_system,
)
from xiaohe.runs import cancel_run
from xiaohe.streaming import ClosingStreamingResponse as StreamingResponse
from xiaohe.view_version import HEADER as VIEW_HEADER
from xiaohe.view_version import parse_view_version

_MESSAGES = {
    "VIEW_VERSION_UNSUPPORTED": "不支持此对话视图版本。",
    "HISTORY_CURSOR_INVALID": "历史游标无效。",
    "HISTORY_RESET_REQUIRED": "历史已更新，请重新读取对话。",
    "MESSAGE_NOT_FOUND": "未找到此消息。",
    "VISITOR_LIMIT_REACHED": "访客消息额度已用完，可继续注册或登录。",
    "VISITOR_EXPIRED": "访客会话已到期。",
    "AUTH_REQUIRED": "请重新验证登录身份。",
    "FORBIDDEN": "无权访问此内容。",
    "RUN_ACTIVE": "当前对话正在处理中。",
    "MERGE_PENDING": "正在接入历史对话，请稍后重试。",
    "ACTION_STALE": "此问题已失效，请刷新对话。",
    "AUTH_OPERATION_EXPIRED": "认证操作已到期。",
    "AUTH_NOT_READY": "认证结果暂未就绪，请稍后重试。",
    "AUTH_RATE_LIMITED": "认证请求过于频繁，请稍后重试。",
    "AUTH_REPLAY": "认证请求已使用。",
    "IDENTITY_MISMATCH": "认证身份不匹配。",
    "IDEMPOTENCY_CONFLICT": "重复请求的内容不一致。",
    "INPUT_INVALID": "请求格式不正确。",
    "SENSITIVE_INPUT_REQUIRES_CARD": "手机号和验证码请使用认证卡片。",
    "EVENT_CURSOR_INVALID": "事件游标无效。",
    "EVENT_CURSOR_EXPIRED": "事件游标已过期，请重新读取快照。",
    "CSRF_INVALID": "请求验证失败，请重新读取会话。",
    "UPSTREAM_UNAVAILABLE": "服务暂时不可用，请稍后重试。",
    "DRAFT_CONFLICT": "资料已更新，请读取当前版本后重新确认。",
    "SUBJECT_CONFLICT": "企业或组织资料存在冲突，请核对后重试。",
    "FILE_INVALID": "文件类型或内容不符合要求。",
    "FILE_TOO_LARGE": "文件超过大小限制。",
    "SUBMISSION_UNKNOWN": "正式受理结果尚未确认，请稍后重试。",
    "AGENT_NOT_FOUND": "未找到该服务。",
}


class _PrivateResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        version = None
        if scope["type"] == "http" and scope.get("path", "").startswith("/ai-api/v1/"):
            try:
                version = parse_view_version(Request(scope))
            except ServiceError:
                body = ApiError(
                    error={
                        "code": "VIEW_VERSION_UNSUPPORTED",
                        "message": _MESSAGES["VIEW_VERSION_UNSUPPORTED"],
                        "retryable": False,
                    },
                    request_id=str(uuid4()),
                )
                response = JSONResponse(
                    body.model_dump(mode="json", by_alias=True),
                    status_code=406,
                    headers={"Cache-Control": "private, no-store"},
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})["xiaohe_view_version"] = version

        async def private_send(message):
            if message["type"] == "http.response.start" and scope.get("path", "").startswith(
                "/ai-api/v1/"
            ):
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
                if version is not None:
                    MutableHeaders(scope=message)[VIEW_HEADER] = str(version)
            await send(message)

        await self.app(scope, receive, private_send)


def install_error_handlers(app: FastAPI):
    app.add_middleware(_PrivateResponses)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        code = error.code if error.code in _MESSAGES else "UPSTREAM_UNAVAILABLE"
        body = ApiError(
            error={"code": code, "message": _MESSAGES[code], "retryable": error.retryable},
            request_id=str(uuid4()),
        )
        return JSONResponse(
            body.model_dump(mode="json", by_alias=True),
            status_code=error.http_status if code == error.code else 503,
            headers={"Cache-Control": "private, no-store"}
            | (
                {VIEW_HEADER: str(request.state.xiaohe_view_version)}
                if hasattr(request.state, "xiaohe_view_version")
                else {}
            )
            | ({"Retry-After": str(error.retry_after)} if error.retry_after else {}),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return await service_error(request, ServiceError("INPUT_INVALID", 422))

    @app.exception_handler(Exception)
    async def unexpected(request, error):
        return await service_error(request, ServiceError("UPSTREAM_UNAVAILABLE", 503))


async def _body(request, model, *, max_bytes=65536):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > max_bytes:
            raise ServiceError("INPUT_INVALID", 422)
        raw.extend(chunk)
    result = None
    try:
        result = model.model_validate_json(raw)
    except ValidationError:
        pass
    if result is None:
        raise ServiceError("INPUT_INVALID", 422)
    return result


async def _actor(request, db):
    system = await resolve_system(request, db)
    return await resolve_actor(request, system, db)


def create_public_router(factory):
    router = APIRouter(prefix="/ai-api/v1")

    def artifacts(request):
        from xiaohe.artifacts import ArtifactService

        if not hasattr(request.app.state, "xiaohe_artifacts"):
            request.app.state.xiaohe_artifacts = ArtifactService(
                factory, request.app.state.xiaohe_settings
            )
        return request.app.state.xiaohe_artifacts

    @router.post("/session")
    async def session(request: Request, response: Response):
        version = parse_view_version(request)
        body = await _body(request, BootstrapInputV2 if version == 2 else EmptyBody)
        async with factory.begin() as db:
            snapshot = await bootstrap_session(
                request,
                response,
                db,
                view_version=version,
                expected_identity=body.expected_identity if version == 2 else None,
            )
        return snapshot

    @router.get("/conversations/{conversation_id}")
    async def snapshot(conversation_id: UUID, request: Request):
        async with factory.begin() as db:
            actor = await _actor(request, db)
            if parse_view_version(request) == 2:
                from xiaohe.history import load_snapshot_v2

                require_session_csrf(request, await require_session(db, actor))
                return await load_snapshot_v2(
                    db,
                    actor,
                    conversation_id,
                    cursor_secret=request.app.state.xiaohe_settings.auth_secret,
                )
            return await load_snapshot(db, actor, conversation_id)

    @router.get("/conversations/{conversation_id}/messages")
    async def history_page(conversation_id: UUID, request: Request, before: str):
        from xiaohe.history import load_history_page

        parse_view_version(request, v2_only=True)
        if (
            set(request.query_params) != {"before"}
            or len(request.query_params.getlist("before")) != 1
        ):
            raise ServiceError("INPUT_INVALID", 422)
        async with factory.begin() as db:
            actor = await _actor(request, db)
            require_session_csrf(request, await require_session(db, actor))
            return await load_history_page(
                db,
                actor,
                conversation_id,
                before,
                cursor_secret=request.app.state.xiaohe_settings.auth_secret,
            )

    @router.get("/conversations/{conversation_id}/messages/{message_id}")
    async def message_baseline(conversation_id: UUID, message_id: UUID, request: Request):
        from xiaohe.history import load_message_baseline

        parse_view_version(request, v2_only=True)
        if request.query_params:
            raise ServiceError("INPUT_INVALID", 422)
        async with factory.begin() as db:
            actor = await _actor(request, db)
            require_session_csrf(request, await require_session(db, actor))
            return await load_message_baseline(db, actor, conversation_id, message_id)

    @router.post("/conversations/{conversation_id}/messages", status_code=202)
    async def message(conversation_id: UUID, request: Request):
        body = await _body(request, MessageInput)
        async with factory.begin() as db:
            return await accept_message(
                db, await _actor(request, db), conversation_id, body.client_message_id, body.text
            )

    @router.post("/conversations/{conversation_id}/actions", status_code=202)
    async def action(conversation_id: UUID, request: Request):
        body = await _body(request, ChatAction)
        async with factory.begin() as db:
            return await accept_action(
                db,
                await _actor(request, db),
                conversation_id,
                body.client_action_id,
                body.request_id,
                body.option_id,
                body.text,
            )

    @router.post("/agents/{agent_id}/conversations/{conversation_id}/messages", status_code=202)
    async def agent_message(agent_id: str, conversation_id: UUID, request: Request):
        body = await _body(request, MessageInput)
        async with factory.begin() as db:
            return await accept_agent_message(
                db, await _actor(request, db), agent_id, conversation_id, body
            )

    @router.post("/agents/{agent_id}/conversations/{conversation_id}/actions", status_code=202)
    async def agent_action(agent_id: str, conversation_id: UUID, request: Request):
        body = await _body(request, ChatAction)
        async with factory.begin() as db:
            return await accept_agent_action(
                db, await _actor(request, db), agent_id, conversation_id, body
            )

    @router.get("/conversations/{conversation_id}/events")
    async def events(conversation_id: UUID, request: Request, after: str | None = None):
        version = parse_view_version(request)
        history_epoch = None
        if version == 2:
            values = request.query_params.getlist("historyEpoch")
            if (
                len(values) != 1
                or not values[0].isascii()
                or not values[0].isdigit()
                or len(values[0]) > 16
            ):
                raise ServiceError("INPUT_INVALID", 422)
            history_epoch = int(values[0])
            if history_epoch > 2**53 - 1:
                raise ServiceError("INPUT_INVALID", 422)
        last = request.headers.getlist("Last-Event-ID")
        if len(last) > 1:
            raise ServiceError("EVENT_CURSOR_INVALID", 409)
        async with factory.begin() as db:
            actor = await _actor(request, db)
        stream = await stream_events(
            factory,
            actor,
            conversation_id,
            after,
            last[0] if last else None,
            **({"view_version": 2, "history_epoch": history_epoch} if version == 2 else {}),
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: UUID, request: Request):
        await _body(request, EmptyBody)
        async with factory.begin() as db:
            return await cancel_run(db, await _actor(request, db), run_id)

    @router.post("/auth/logout", status_code=204)
    async def logout(request: Request):
        await _body(request, EmptyBody)
        response = Response(status_code=204)
        async with factory.begin() as db:
            await logout_session(request, response, db)
        return response

    @router.get("/artifacts/{artifact_id}/versions/{version}")
    async def artifact(artifact_id: UUID, version: int, request: Request):
        if version < 1:
            raise ServiceError("INPUT_INVALID", 422)
        async with factory.begin() as db:
            actor = await _actor(request, db)
        return await artifacts(request).read_artifact(actor, artifact_id, version)

    @router.get("/artifacts/{artifact_id}/versions/{version}/download")
    async def download(artifact_id: UUID, version: int, request: Request):
        if version < 1:
            raise ServiceError("INPUT_INVALID", 422)
        async with factory.begin() as db:
            actor = await _actor(request, db)
        service = artifacts(request)
        view = await service.read_artifact(actor, artifact_id, version)
        stream = await service.download_artifact(actor, artifact_id, version)
        return StreamingResponse(
            stream,
            media_type=view.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(view.title, safe=""),
            },
        )

    from xiaohe.authentication import create_auth_router

    router.include_router(create_auth_router(factory))
    return router
