"""有界 SDK 窗口；门户历史只消费受权基线与 V2 CUSTOM。"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter, ValidationError

from xiaohe.agui_projection import (
    ProjectionContext,
    _messages,
    _state,
    error_event,
    project_run_status,
)
from xiaohe.contracts import ServiceError
from xiaohe.contracts_v2 import ChatSnapshotV2, EventEnvelopeV2, MessageBaselineV2, PublicSnapshotV2

_EVENT = TypeAdapter(EventEnvelopeV2)


@dataclass
class ProjectionContextV2(ProjectionContext):
    history_epoch: int = 0
    baseline: MessageBaselineV2 | None = None
    pending_input: dict | None = None


def project_snapshot_v2(snapshot: ChatSnapshotV2, attempt_id: str | None) -> list[dict]:
    public = snapshot.model_dump(mode="json", by_alias=True, exclude={"csrf"})
    public.update(
        attemptId=attempt_id,
        businessRunId=snapshot.active_run.id if snapshot.active_run else None,
        businessRunStatus=snapshot.active_run.status if snapshot.active_run else None,
    )
    PublicSnapshotV2.model_validate(public)
    events = (
        [{"type": "RUN_STARTED", "threadId": snapshot.conversation_id, "runId": attempt_id}]
        if attempt_id is not None
        else []
    )
    events.extend(
        [
            {"type": "MESSAGES_SNAPSHOT", "messages": _messages(public)},
            _state(public),
            {
                "type": "CUSTOM",
                "name": "xiaohe.v2.ready",
                "value": {
                    "conversationId": snapshot.conversation_id,
                    "historyEpoch": snapshot.history.history_epoch,
                    "watermarkSequence": snapshot.sync.watermark_sequence,
                },
            },
        ]
    )
    if snapshot.active_run and snapshot.active_run.status == "waiting_input" and attempt_id:
        events.append(
            {"type": "RUN_FINISHED", "threadId": snapshot.conversation_id, "runId": attempt_id}
        )
    return events


def project_message_baseline_v2(
    baseline: MessageBaselineV2, context: ProjectionContextV2
) -> list[dict]:
    if baseline.conversation_id != context.conversation_id:
        raise ServiceError("FORBIDDEN", 403)
    if baseline.history_epoch != context.history_epoch:
        raise ServiceError("HISTORY_RESET_REQUIRED", 409)
    if baseline.message.role != "assistant":
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    context.baseline = baseline
    return [
        {
            "type": "CUSTOM",
            "name": "xiaohe.v2.message-baseline",
            "value": baseline.model_dump(mode="json", by_alias=True),
        }
    ]


def _input_locator(message, run_id):
    questions = [part for part in message["parts"] if part["type"] in ("choice", "input_request")]
    if len(questions) != 1:
        return None
    return dict(
        runId=run_id,
        messageId=message["id"],
        requestId=questions[0]["requestId"],
        kind=questions[0]["type"],
    )


def project_run_status_v2(run: dict, context: ProjectionContextV2) -> list[dict]:
    if (
        context.public is not None
        and run["id"] == context.business_run_id
        and run["status"] == "waiting_input"
    ):
        if context.pending_input is not None:
            context.public["activeInput"] = context.pending_input
        elif context.baseline is not None:
            context.public["activeInput"] = _input_locator(
                context.baseline.message.model_dump(mode="json", by_alias=True), run["id"]
            )
    if (
        context.public is not None
        and context.public["activeRun"] is not None
        and (context.public["activeRun"]["id"] == run["id"] and run["status"] != "waiting_input")
    ):
        context.public["activeInput"] = None
    return project_run_status(run, context)


def _window(context):
    public = context.public
    public["messages"].sort(
        key=lambda message: (datetime.fromisoformat(message["createdAt"]), message["id"])
    )
    public["messages"] = public["messages"][-10:]
    ids = {message["id"] for message in public["messages"]}
    dropped = context.opened_messages - ids
    result = [{"type": "TEXT_MESSAGE_END", "messageId": key} for key in sorted(dropped)]
    context.opened_messages.difference_update(dropped)
    PublicSnapshotV2.model_validate(public)
    return result + [{"type": "MESSAGES_SNAPSHOT", "messages": _messages(public)}, _state(public)]


def project_event_v2(event: dict, context: ProjectionContextV2) -> list[dict]:
    try:
        value = _EVENT.validate_python(event).model_dump(mode="json", by_alias=True)
    except ValidationError:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
    if value["conversationId"] != context.conversation_id:
        raise ServiceError("FORBIDDEN", 403)
    if value["historyEpoch"] != context.history_epoch:
        raise ServiceError("HISTORY_RESET_REQUIRED", 409)
    if context.finished:
        return []
    public = context.public
    events = [{"type": "CUSTOM", "name": "xiaohe.v2.event", "value": value}]
    if public is not None:
        public["sync"] = dict(eventCursor=value["eventId"], watermarkSequence=value["sequence"])
    kind, payload = value["type"], value["payload"]
    if kind == "message.upsert":
        message = deepcopy(payload["message"])
        if context.baseline is not None and message["id"] == context.baseline.message.id:
            if value["sequence"] <= context.baseline.watermark_sequence:
                return events
            context.baseline = MessageBaselineV2.model_validate(
                context.baseline.model_dump(mode="json", by_alias=True)
                | {"message": message, "watermarkSequence": value["sequence"]}
            )
        if value["runId"] == context.business_run_id:
            context.pending_input = _input_locator(message, value["runId"])
        if message["id"] in context.opened_messages:
            context.opened_messages.remove(message["id"])
            events.append({"type": "TEXT_MESSAGE_END", "messageId": message["id"]})
        if public is not None:
            public["messages"] = [m for m in public["messages"] if m["id"] != message["id"]] + [
                message
            ]
            events.extend(_window(context))
    elif kind == "message.delta":
        message_id = payload["messageId"]
        baseline = context.baseline
        if (
            baseline is not None
            and baseline.message.id == message_id
            and value["sequence"] <= baseline.watermark_sequence
        ):
            return events
        message = (
            next(
                (
                    m
                    for m in public["messages"]
                    if m["id"] == message_id and m["role"] == "assistant"
                ),
                None,
            )
            if public
            else None
        )
        in_window = message is not None
        if message is None:
            if baseline is None or baseline.message.id != message_id:
                raise ServiceError("EVENT_CURSOR_EXPIRED", 409)
            message = baseline.message.model_dump(mode="json", by_alias=True)
        part = next((p for p in reversed(message["parts"]) if p["type"] == "text"), None)
        if part is None:
            message["parts"].append({"type": "text", "text": payload["delta"]})
        else:
            part["text"] += payload["delta"]
        if not in_window:
            context.baseline = MessageBaselineV2.model_validate(
                baseline.model_dump(mode="json", by_alias=True)
                | {"message": message, "watermarkSequence": value["sequence"]}
            )
            return events
        if message_id not in context.opened_messages:
            context.opened_messages.add(message_id)
            events.append(
                {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"}
            )
        events.append(
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": payload["delta"]}
        )
    elif kind == "run.updated":
        events.extend(project_run_status_v2(payload["run"], context))
    elif kind == "viewer.updated" and public is not None:
        public["viewer"] = payload["viewer"]
        if payload["viewer"]["kind"] == "guest":
            public["sessionExpiresAt"] = payload["viewer"]["expiresAt"]
        events.append(_state(public))
    elif kind == "artifact.updated" and public is not None:
        for message in public["messages"]:
            for part in message["parts"]:
                if part["type"] == "artifact_ref" and (part["artifactId"], part["version"]) == (
                    payload["artifactId"],
                    payload["version"],
                ):
                    part["status"] = payload["status"]
        events.append(_state(public))
    elif kind in ("viewer.expired", "error"):
        context.finished = True
        events.append(
            error_event("VISITOR_EXPIRED" if kind == "viewer.expired" else payload["error"]["code"])
        )
    return events
