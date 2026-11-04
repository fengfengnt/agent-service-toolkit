"""增量AG-UI业务出口；沿用原授权、幂等受理和持久事件。"""

import json
from contextlib import aclosing
from typing import Literal
from uuid import UUID

import anyio
from ag_ui.core import RunAgentInput
from fastapi import APIRouter, Request
from pydantic import model_validator
from sqlalchemy import select

from xiaohe.admission import _parse, accept_action, accept_message
from xiaohe.agui_projection import (
    ProjectionContext,
    _finish,
    error_event,
    project_event,
    project_run_status,
    project_snapshot,
)
from xiaohe.api import _actor, _body
from xiaohe.contracts import ChatAction, InputId, RunRef, ServiceError, WireModel
from xiaohe.conversations import load_snapshot, require_conversation
from xiaohe.events import stream_events
from xiaohe.models import Command, Run
from xiaohe.runs import cancel_run
from xiaohe.streaming import ClosingStreamingResponse


class ConversationInput(WireModel):
    thread_id: UUID
    attempt_id: InputId | None = None

    @model_validator(mode="after")
    def no_null_attempt(self):
        if "attempt_id" in self.model_fields_set and self.attempt_id is None:
            raise ValueError("attemptId must be omitted or a nonblank string")
        return self


class MessageControl(WireModel):
    kind: Literal["message"] = "message"


class ActionControl(WireModel):
    kind: Literal["action"]
    request_id: InputId
    option_id: InputId | None = None
    text: str | None = None


def _uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("INPUT_INVALID", 422) from None


def _control(input):
    forwarded = input.forwarded_props
    if forwarded is None:
        forwarded = {}
    if not isinstance(forwarded, dict):
        raise ServiceError("INPUT_INVALID", 422)
    control = forwarded.get("xiaohe", {})
    if not isinstance(control, dict):
        raise ServiceError("INPUT_INVALID", 422)
    return control


async def _selected(db, actor, body):
    conversation = await require_conversation(db, actor, body.thread_id)
    snapshot = await load_snapshot(db, actor, body.thread_id)
    query = select(Command).where(Command.conversation_id == body.thread_id)
    if body.attempt_id is not None:
        rows = (await db.scalars(query.where(Command.client_key == body.attempt_id).limit(2))).all()
        if len(rows) != 1:
            raise ServiceError("FORBIDDEN", 403)
        command = rows[0]
    elif snapshot.active_run:
        command = await db.scalar(
            query.where(Command.run_id == UUID(snapshot.active_run.id))
            .order_by(Command.created_at.desc(), Command.id.desc())
            .limit(1)
        )
    else:
        command = None
    run = await db.get(Run, command.run_id) if command else None
    return conversation, snapshot, command, run


def _run_ref(run):
    return (
        RunRef(id=str(run.id), status=run.status).model_dump(mode="json", by_alias=True)
        if run
        else None
    )


def _frame(event):
    return (
        "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n"
    ).encode()


async def _check_scope(factory, actor, conversation_id, epoch):
    async with factory.begin() as db:
        current = await require_conversation(db, actor, conversation_id)
        if current.ownership_epoch != epoch:
            raise ServiceError("FORBIDDEN", 403)


def create_agui_router(factory) -> APIRouter:
    router = APIRouter(prefix="/ai-api/v1/agui")

    @router.post("/run", status_code=202)
    async def run(request: Request):
        input = await _body(request, RunAgentInput)
        conversation_id = _uuid(input.thread_id)
        if input.resume is not None or input.parent_run_id is not None:
            raise ServiceError("INPUT_INVALID", 422)
        control = _control(input)
        from pydantic import TypeAdapter, ValidationError

        try:
            attempt_id = TypeAdapter(InputId).validate_python(input.run_id)
            action = (
                ActionControl.model_validate(control) if control.get("kind") == "action" else None
            )
            if action is None:
                MessageControl.model_validate(control)
            elif ("option_id" in action.model_fields_set and action.option_id is None) or (
                "text" in action.model_fields_set and action.text is None
            ):
                raise ValueError("null action")
        except (ValidationError, ValueError):
            raise ServiceError("INPUT_INVALID", 422) from None
        async with factory.begin() as db:
            actor = await _actor(request, db)
            await require_conversation(db, actor, conversation_id)
            kind = "action" if action else "message"
            conflicting = await db.scalar(
                select(Command.id).where(
                    Command.conversation_id == conversation_id,
                    Command.client_key == attempt_id,
                    Command.kind != kind,
                )
            )
            if conflicting is not None:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
            if action:
                value = _parse(
                    ChatAction,
                    {
                        "clientActionId": attempt_id,
                        "requestId": action.request_id,
                        **({"optionId": action.option_id} if action.option_id is not None else {}),
                        **({"text": action.text} if action.text is not None else {}),
                    },
                )
                accepted = await accept_action(
                    db,
                    actor,
                    conversation_id,
                    attempt_id,
                    value.request_id,
                    value.option_id,
                    value.text,
                )
            else:
                if (
                    not input.messages
                    or input.messages[-1].role != "user"
                    or not isinstance(input.messages[-1].content, str)
                ):
                    raise ServiceError("INPUT_INVALID", 422)
                accepted = await accept_message(
                    db, actor, conversation_id, attempt_id, input.messages[-1].content
                )
        return accepted.model_dump(mode="json", by_alias=True) | {
            "attemptId": attempt_id,
            "businessRunId": accepted.run.id,
        }

    @router.post("/status")
    async def status(request: Request):
        body = await _body(request, ConversationInput)
        async with factory.begin() as db:
            _, snapshot, command, current = await _selected(db, await _actor(request, db), body)
            return {
                "conversationId": snapshot.conversation_id,
                "activeRun": snapshot.active_run,
                "run": _run_ref(current),
                "attemptId": command.client_key if command else None,
                "businessRunId": str(current.id) if current else None,
                "isRunning": current is not None and current.status in ("queued", "running"),
                "cursor": snapshot.cursor,
            }

    @router.post("/stop")
    async def stop(request: Request):
        body = await _body(request, ConversationInput)
        async with factory.begin() as db:
            actor = await _actor(request, db)
            _, snapshot, command, current = await _selected(db, actor, body)
            if (
                current is None
                or snapshot.active_run is None
                or snapshot.active_run.id != str(current.id)
            ):
                return {"stopped": False, "run": _run_ref(current)}
            latest = await db.scalar(
                select(Command)
                .where(Command.run_id == current.id)
                .order_by(Command.created_at.desc(), Command.id.desc())
                .limit(1)
            )
            if body.attempt_id is not None and (latest is None or latest.id != command.id):
                return {"stopped": False, "run": _run_ref(current)}
            return {"stopped": True, "run": await cancel_run(db, actor, current.id)}

    @router.post("/connect")
    async def connect(request: Request):
        body = await _body(request, ConversationInput)
        async with factory.begin() as db:
            actor = await _actor(request, db)
            conversation, snapshot, command, current = await _selected(db, actor, body)
            epoch = conversation.ownership_epoch
            attempt = command.client_key if command else None
            current_status = current.status if current else None
            business_id = str(current.id) if current else None
            latest_id = (
                await db.scalar(
                    select(Command.id)
                    .where(Command.run_id == current.id)
                    .order_by(Command.created_at.desc(), Command.id.desc())
                    .limit(1)
                )
                if current
                else None
            )
            finished_old_attempt = (
                command is not None and command.completed_at is not None and latest_id != command.id
            )
        initial = [
            event
            for event in project_snapshot(snapshot, attempt)
            if event["type"] != "RUN_FINISHED"
        ]
        public = next(
            event["snapshot"]["xiaohe"] for event in initial if event["type"] == "STATE_SNAPSHOT"
        )
        public["businessRunId"] = business_id
        public["businessRunStatus"] = current_status
        context = ProjectionContext(snapshot.conversation_id, attempt, business_id, public)
        source = (
            await stream_events(factory, actor, body.thread_id, snapshot.cursor)
            if current_status in ("queued", "running") and not finished_old_attempt
            else None
        )

        async def generate():
            try:
                for event in initial:
                    await _check_scope(factory, actor, body.thread_id, epoch)
                    yield _frame(event)
                if finished_old_attempt:
                    for event in _finish(context, "completed"):
                        await _check_scope(factory, actor, body.thread_id, epoch)
                        yield _frame(event)
                    return
                if current_status in (
                    "waiting_input",
                    "completed",
                    "failed",
                    "cancelled",
                    "expired",
                ):
                    for event in project_run_status(
                        {"id": business_id, "status": current_status}, context
                    ):
                        await _check_scope(factory, actor, body.thread_id, epoch)
                        yield _frame(event)
                if source is None:
                    return
                async with aclosing(source):
                    async for frame in source:
                        if frame.startswith(b":"):
                            await _check_scope(factory, actor, body.thread_id, epoch)
                            yield frame
                            continue
                        payload = next(
                            line[6:]
                            for line in frame.decode().splitlines()
                            if line.startswith("data: ")
                        )
                        for event in project_event(json.loads(payload), context):
                            await _check_scope(factory, actor, body.thread_id, epoch)
                            yield _frame(event)
                        if context.finished:
                            return
                await _check_scope(factory, actor, body.thread_id, epoch)
                yield _frame(error_event("EVENT_CURSOR_EXPIRED"))
            except ServiceError as error:
                yield _frame(error_event(error.code))
            except Exception:
                yield _frame(error_event("UPSTREAM_UNAVAILABLE"))
            finally:
                if source is not None:
                    with anyio.CancelScope(shield=True):
                        await source.aclose()

        return ClosingStreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
        )

    return router
