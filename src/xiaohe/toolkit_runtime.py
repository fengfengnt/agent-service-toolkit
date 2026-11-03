import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.types import Command as GraphCommand
from pydantic import ValidationError
from sqlalchemy import func, select, text, update

from xiaohe.contracts import (
    AgentOutcome,
    ServiceError,
    ToolkitDelta,
    ToolkitError,
    ToolkitExecution,
    ToolkitOutcome,
    WriteFence,
)
from xiaohe.graph import visible_text
from xiaohe.models import Command, Conversation, Message, RunAttempt, Session
from xiaohe.runs import TERMINAL, _authorized, _locked, reply_id, require_write
from xiaohe.settings import Settings


@dataclass
class ExecutionContext:
    command: ToolkitExecution
    input_id: UUID | None
    input_kind: str | None
    input_message_id: UUID | None
    input_text: str
    question: dict | None
    history: list
    graph_run_id: UUID
    previous_stopped: bool
    outcome: dict | None


def _fence(command):
    return WriteFence(
        run_id=command.run_id, attempt=command.attempt, ownership_epoch=command.ownership_epoch
    )


async def _prepare(db, command: ToolkitExecution) -> ExecutionContext:
    run, conversation, session = await _locked(db, command.run_id)
    await _authorized(db, run, conversation, session)
    if session is None:
        session = await db.get(Session, run.session_id)
    user_id = run.user_id or session.toolkit_user_id
    if (
        command.user_id != user_id
        or command.thread_id != run.thread_id
        or command.attempt != run.attempt
        or command.ownership_epoch != run.ownership_epoch
    ):
        raise ServiceError("EXECUTION_BINDING_INVALID", 403)
    attempt = await db.get(RunAttempt, (run.id, run.attempt))
    if run.status in TERMINAL or run.status == "waiting_input":
        return ExecutionContext(
            command, None, None, None, "", None, [], attempt.graph_run_id, True, run.outcome
        )
    await require_write(db, _fence(command))
    if attempt.started_at is not None:
        raise ServiceError("EXECUTION_ALREADY_STARTED", 409)
    pending = await db.scalar(
        select(Command)
        .where(Command.run_id == run.id, Command.completed_at.is_(None))
        .with_for_update()
    )
    if pending is None:
        raise ServiceError("INPUT_STATE_INVALID", 409)
    mode = (
        "recover"
        if pending.delivered_at is not None
        else ("resume" if pending.kind == "action" else "start")
    )
    if command.mode != mode:
        raise ServiceError("EXECUTION_MODE_INVALID", 403)
    previous = await db.scalar(
        select(RunAttempt)
        .where(RunAttempt.run_id == run.id, RunAttempt.attempt < run.attempt)
        .order_by(RunAttempt.attempt.desc())
        .limit(1)
    )
    current = await db.get(Message, pending.message_id)
    messages = (
        await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == run.conversation_id,
                Message.id != reply_id(run.id, pending.id),
            )
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    history = []
    for message in messages:
        content = visible_text(message.parts)
        if content:
            cls = HumanMessage if message.role == "user" else AIMessage
            history.append(cls(id=str(message.id), content=content))
    pending.delivered_at = pending.delivered_at or await db.scalar(select(func.clock_timestamp()))
    attempt.started_at = await db.scalar(select(func.clock_timestamp()))
    return ExecutionContext(
        command,
        pending.id,
        pending.kind,
        pending.message_id,
        visible_text(current.parts),
        pending.question,
        history,
        attempt.graph_run_id,
        previous is None or previous.graph_stopped_at is not None,
        None,
    )


def _receipt(value, command_id: str) -> bool:
    if isinstance(value, dict):
        return value.get("command_id") == command_id or any(
            _receipt(item, command_id) for item in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_receipt(item, command_id) for item in value)


async def _state(graph, config, command_id):
    snapshot = await graph.aget_state(config)
    accepted = snapshot.values.get("command_id") == command_id
    if not accepted:
        checkpoint = await graph.checkpointer.aget_tuple(config)
        accepted = checkpoint is not None and any(
            channel == "__resume__" and _receipt(value, command_id)
            for _, channel, value in checkpoint.pending_writes or []
        )
    paused = any(task.interrupts for task in snapshot.tasks)
    return snapshot, accepted, paused


def _config(context, thread_id):
    command = context.command
    return {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": "", "user_id": command.user_id},
        "run_id": context.graph_run_id,
        "metadata": {
            "business_run_id": str(command.run_id),
            "attempt": command.attempt,
            "ownership_epoch": command.ownership_epoch,
        },
    }


async def _stopped(factory, context):
    async with factory.begin() as db:
        await db.execute(
            update(RunAttempt)
            .where(
                RunAttempt.run_id == context.command.run_id,
                RunAttempt.attempt == context.command.attempt,
            )
            .values(graph_stopped_at=func.clock_timestamp())
        )


async def _graph_stream(graph, value, config):
    queue = asyncio.Queue(maxsize=16)

    async def produce():
        try:
            async with aclosing(
                graph.astream(value, config, stream_mode=["messages", "updates"], durability="sync")
            ) as stream:
                async for item in stream:
                    await queue.put(item)
        except Exception as error:
            await queue.put(error)
        else:
            await queue.put(None)

    # One controlled cancellation lets LangGraph finish cleanup despite ASGI cancel scopes.
    producer = asyncio.create_task(produce())
    try:
        while (item := await queue.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        with anyio.CancelScope(shield=True):
            producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer


async def _drive(factory, context) -> AsyncIterator:
    from agents import get_agent

    if context.input_id is None:
        if context.outcome is None:
            yield ToolkitError(kind="error", code="EXECUTION_FINISHED", retryable=False)
        else:
            yield ToolkitOutcome(
                kind="outcome", outcome=AgentOutcome.model_validate(context.outcome)
            )
        return
    graph = get_agent("xiaohe")
    if graph.checkpointer is None:
        raise ServiceError("CHECKPOINT_NOT_READY", 503)
    thread_id = context.command.thread_id
    config = _config(context, thread_id)
    snapshot, accepted, paused = await _state(graph, config, str(context.input_id))
    if (
        snapshot.values.get("command_id") == str(context.input_id)
        and snapshot.values.get("outcome")
        and (not snapshot.next or snapshot.values["outcome"]["wait_for_input"])
    ):
        yield ToolkitOutcome(
            kind="outcome", outcome=AgentOutcome.model_validate(snapshot.values["outcome"])
        )
        return
    engine = factory.kw["bind"]
    async with engine.connect() as guard, guard.begin():
        acquired = await guard.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "xiaohe.thread:" + thread_id},
        )
        if not acquired or (not context.previous_stopped and snapshot.next and not paused):
            thread_id = str(uuid4())
            async with factory.begin() as db:
                run = await require_write(db, _fence(context.command))
                run.thread_id = thread_id
                conversation = await db.get(Conversation, run.conversation_id)
                conversation.thread_id = thread_id
                attempt = await db.get(RunAttempt, (run.id, run.attempt))
                attempt.thread_id = thread_id
            await guard.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "xiaohe.thread:" + thread_id},
            )
            config = _config(context, thread_id)
            snapshot, accepted, paused = await _state(graph, config, str(context.input_id))
        async with factory.begin() as db:
            await require_write(db, _fence(context.command))
        output_id = str(reply_id(context.command.run_id, context.input_id))
        if accepted:
            value = None
        elif context.input_kind == "action":
            if not paused:
                if context.question is None:
                    raise ServiceError("INVALID_RESUME", 409)
                await graph.aupdate_state(
                    config,
                    {
                        "messages": context.history,
                        "run_id": str(context.command.run_id),
                        "command_id": "",
                        "output_message_id": output_id,
                        "direction": "unknown",
                        "outcome": {
                            "parts": [context.question],
                            "wait_for_input": True,
                            "artifact_version_ids": [],
                        },
                        "pending_request": context.question,
                    },
                    as_node="respond",
                )
                async for _ in graph.astream(None, config, durability="sync"):
                    pass
                snapshot = await graph.aget_state(config)
            interrupts = [item for task in snapshot.tasks for item in task.interrupts]
            if len(interrupts) != 1:
                raise ServiceError("INVALID_RESUME", 409)
            approved = {
                "run_id": str(context.command.run_id),
                "command_id": str(context.input_id),
                "message_id": str(context.input_message_id),
                "text": context.input_text,
                "output_message_id": output_id,
            }
            value = GraphCommand(
                resume={interrupts[0].id: approved}, update={"messages": context.history}
            )
        else:
            value = {
                "messages": context.history,
                "run_id": str(context.command.run_id),
                "command_id": str(context.input_id),
                "output_message_id": output_id,
                "direction": "unknown",
                "outcome": None,
                "pending_request": None,
            }
        stream = _graph_stream(graph, value, config)
        try:
            async for kind, item in stream:
                if kind == "messages":
                    chunk, metadata = item
                    if isinstance(chunk, AIMessageChunk) and "xiaohe_public" in metadata.get(
                        "tags", []
                    ):
                        content = (
                            chunk.content
                            if isinstance(chunk.content, str)
                            else "".join(
                                block.get("text", "")
                                for block in chunk.content
                                if isinstance(block, dict) and block.get("type") == "text"
                            )
                        )
                        if content:
                            yield ToolkitDelta(
                                kind="delta", message_id=UUID(output_id), delta=content
                            )
        finally:
            await stream.aclose()
        snapshot = await graph.aget_state(config)
        if snapshot.values.get("command_id") != str(context.input_id) or not snapshot.values.get(
            "outcome"
        ):
            raise ServiceError("EXECUTION_PROTOCOL_ERROR", 502)
        yield ToolkitOutcome(
            kind="outcome", outcome=AgentOutcome.model_validate(snapshot.values["outcome"])
        )


def create_execution_router(factory, settings: Settings) -> APIRouter:
    from core.settings import settings as upstream
    from service.service import verify_bearer

    if not upstream.AUTH_SECRET or not secrets.compare_digest(
        upstream.AUTH_SECRET.get_secret_value(), settings.auth_secret.get_secret_value()
    ):
        raise ValueError("toolkit AUTH_SECRET must be nonempty and match business AUTH_SECRET")
    router = APIRouter(dependencies=[Depends(verify_bearer)])

    @router.post("/internal/xiaohe/runs/{run_id}/stream")
    async def execute(run_id: UUID, request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise HTTPException(413, detail="INPUT_INVALID")
        command = None
        try:
            command = ToolkitExecution.model_validate_json(raw)
        except ValidationError:
            pass
        if command is None:
            raise HTTPException(422, detail="INPUT_INVALID")
        if command.run_id != run_id:
            raise HTTPException(403, detail="EXECUTION_BINDING_INVALID")
        try:
            async with factory.begin() as db:
                context = await _prepare(db, command)
        except ServiceError as error:
            raise HTTPException(error.http_status, detail=error.code) from None

        async def body():
            try:
                async with aclosing(_drive(factory, context)) as execution:
                    async for event in execution:
                        if event.kind in ("outcome", "error"):
                            await _stopped(factory, context)
                        yield event.model_dump_json(by_alias=True) + "\n"
            except ServiceError as error:
                yield (
                    ToolkitError(
                        kind="error", code=error.code, retryable=error.retryable
                    ).model_dump_json()
                    + "\n"
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                yield (
                    ToolkitError(
                        kind="error", code="UPSTREAM_UNAVAILABLE", retryable=False
                    ).model_dump_json()
                    + "\n"
                )
            finally:
                try:
                    await _stopped(factory, context)
                except Exception:
                    pass

        return StreamingResponse(body(), media_type="application/x-ndjson")

    return router
