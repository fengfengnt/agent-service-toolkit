from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders

from xiaohe.admission import accept_action, accept_message
from xiaohe.contracts import ApiError, ChatAction, EmptyBody, MessageInput, ServiceError
from xiaohe.conversations import load_snapshot
from xiaohe.events import stream_events
from xiaohe.identity import bootstrap_session, logout_session, resolve_actor, resolve_system
from xiaohe.runs import cancel_run

_MESSAGES = {
    "VISITOR_LIMIT_REACHED": "访客消息额度已用完，可继续注册或登录。",
    "VISITOR_EXPIRED": "访客会话已到期。",
    "AUTH_REQUIRED": "请重新验证登录身份。",
    "FORBIDDEN": "无权访问此内容。",
    "RUN_ACTIVE": "当前对话正在处理中。",
    "MERGE_PENDING": "正在接入历史对话，请稍后重试。",
    "ACTION_STALE": "此问题已失效，请刷新对话。",
    "AUTH_OPERATION_EXPIRED": "认证操作已到期。",
    "AUTH_REPLAY": "认证请求已使用。",
    "IDENTITY_MISMATCH": "认证身份不匹配。",
    "IDEMPOTENCY_CONFLICT": "重复请求的内容不一致。",
    "INPUT_INVALID": "请求格式不正确。",
    "SENSITIVE_INPUT_REQUIRES_CARD": "手机号和验证码请使用认证卡片。",
    "EVENT_CURSOR_INVALID": "事件游标无效。",
    "EVENT_CURSOR_EXPIRED": "事件游标已过期，请重新读取快照。",
    "CSRF_INVALID": "请求验证失败，请重新读取会话。",
    "UPSTREAM_UNAVAILABLE": "服务暂时不可用，请稍后重试。",
}


class _PrivateResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def private_send(message):
            if message["type"] == "http.response.start" and scope.get("path", "").startswith(
                "/ai-api/v1/"
            ):
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
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
            headers={"Cache-Control": "private, no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return await service_error(request, ServiceError("INPUT_INVALID", 422))

    @app.exception_handler(Exception)
    async def unexpected(request, error):
        return await service_error(request, ServiceError("UPSTREAM_UNAVAILABLE", 503))


async def _body(request, model):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
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
        await _body(request, EmptyBody)
        async with factory.begin() as db:
            snapshot = await bootstrap_session(request, response, db)
        return snapshot

    @router.get("/conversations/{conversation_id}")
    async def snapshot(conversation_id: UUID, request: Request):
        async with factory.begin() as db:
            return await load_snapshot(db, await _actor(request, db), conversation_id)

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

    @router.get("/conversations/{conversation_id}/events")
    async def events(conversation_id: UUID, request: Request, after: str | None = None):
        last = request.headers.getlist("Last-Event-ID")
        if len(last) > 1:
            raise ServiceError("EVENT_CURSOR_INVALID", 409)
        async with factory.begin() as db:
            actor = await _actor(request, db)
        stream = await stream_events(
            factory, actor, conversation_id, after, last[0] if last else None
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

    return router
