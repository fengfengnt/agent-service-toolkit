"""只投影已授权的公开业务事实，不暴露graph或认证状态。"""

from copy import deepcopy
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

from xiaohe.api import _MESSAGES
from xiaohe.contracts import ChatSnapshot, EventEnvelope, ServiceError

_EVENT = TypeAdapter(EventEnvelope)
_FINISHED = {"completed", "waiting_input"}
_FAILED = {"failed", "cancelled", "expired"}


def error_event(code):
    code = code if code in _MESSAGES else "UPSTREAM_UNAVAILABLE"
    return {"type": "RUN_ERROR", "code": code, "message": _MESSAGES[code]}


def _messages(public):
    return [
        {
            "id": message["id"],
            "role": message["role"],
            "content": "".join(part["text"] for part in message["parts"] if part["type"] == "text"),
        }
        for message in public["messages"]
    ]


def _state(public):
    return {"type": "STATE_SNAPSHOT", "snapshot": {"xiaohe": deepcopy(public)}}


@dataclass
class ProjectionContext:
    conversation_id: str
    attempt_id: str | None
    business_run_id: str | None
    public: dict | None = None
    opened_messages: set[str] = field(default_factory=set)
    finished: bool = False


def project_snapshot(snapshot: ChatSnapshot, attempt_id: str | None) -> list[dict]:
    public = snapshot.model_dump(mode="json", by_alias=True, exclude={"csrf"})
    public.update(
        version=1,
        attemptId=attempt_id,
        businessRunId=snapshot.active_run.id if snapshot.active_run else None,
        businessRunStatus=snapshot.active_run.status if snapshot.active_run else None,
    )
    events = []
    if attempt_id is not None:
        events.append(
            {"type": "RUN_STARTED", "threadId": snapshot.conversation_id, "runId": attempt_id}
        )
    events.extend([{"type": "MESSAGES_SNAPSHOT", "messages": _messages(public)}, _state(public)])
    if snapshot.active_run and snapshot.active_run.status == "waiting_input" and attempt_id:
        events.append(
            {"type": "RUN_FINISHED", "threadId": snapshot.conversation_id, "runId": attempt_id}
        )
    return events


def _finish(context, status):
    events = [
        {"type": "TEXT_MESSAGE_END", "messageId": key} for key in sorted(context.opened_messages)
    ]
    context.opened_messages.clear()
    context.finished = True
    if status in _FINISHED:
        events.append(
            {
                "type": "RUN_FINISHED",
                "threadId": context.conversation_id,
                "runId": context.attempt_id,
            }
        )
    else:
        events.append(
            error_event("VISITOR_EXPIRED" if status == "expired" else "UPSTREAM_UNAVAILABLE")
        )
    return events


def project_run_status(run: dict, context: ProjectionContext) -> list[dict]:
    events = []
    if context.public is not None:
        public = context.public
        if public["activeRun"] is None or public["activeRun"]["id"] == run["id"]:
            public["activeRun"] = (
                run if run["status"] in ("queued", "running", "waiting_input") else None
            )
        if run["id"] == context.business_run_id:
            public["businessRunStatus"] = run["status"]
        events.append(_state(public))
    if run["id"] == context.business_run_id and run["status"] in _FINISHED | _FAILED:
        events.extend(_finish(context, run["status"]))
    return events


def project_event(event: dict, context: ProjectionContext) -> list[dict]:
    try:
        value = _EVENT.validate_python(event).model_dump(mode="json", by_alias=True)
    except ValidationError:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
    if value["conversationId"] != context.conversation_id:
        raise ServiceError("FORBIDDEN", 403)
    if context.finished:
        return []
    kind, payload = value["type"], value["payload"]
    events = [{"type": "CUSTOM", "name": "xiaohe.v1.event", "value": value}]
    public = context.public
    if public is not None:
        public["cursor"] = value["eventId"]
    if kind == "message.upsert":
        message = payload["message"]
        if message["id"] in context.opened_messages:
            events.append({"type": "TEXT_MESSAGE_END", "messageId": message["id"]})
            context.opened_messages.remove(message["id"])
        if public is not None:
            existing = next(
                (
                    index
                    for index, item in enumerate(public["messages"])
                    if item["id"] == message["id"]
                ),
                None,
            )
            if existing is None:
                public["messages"].append(message)
            else:
                public["messages"][existing] = message
            events.extend(
                [{"type": "MESSAGES_SNAPSHOT", "messages": _messages(public)}, _state(public)]
            )
    elif kind == "message.delta":
        message_id = payload["messageId"]
        if public is not None:
            message = next(
                (
                    item
                    for item in public["messages"]
                    if item["id"] == message_id and item["role"] == "assistant"
                ),
                None,
            )
            if message is None:
                raise ServiceError("EVENT_CURSOR_EXPIRED", 409)
            part = next(
                (item for item in reversed(message["parts"]) if item["type"] == "text"), None
            )
            if part is None:
                message["parts"].append({"type": "text", "text": payload["delta"]})
            else:
                part["text"] += payload["delta"]
        if message_id not in context.opened_messages:
            context.opened_messages.add(message_id)
            events.append(
                {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"}
            )
        events.append(
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": payload["delta"]}
        )
    elif kind == "run.updated":
        events.extend(project_run_status(payload["run"], context))
    elif kind == "viewer.updated":
        if public is not None:
            public["viewer"] = payload["viewer"]
            events.append(_state(public))
    elif kind == "artifact.updated":
        if public is not None:
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
