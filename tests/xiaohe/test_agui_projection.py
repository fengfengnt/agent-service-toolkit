import hashlib
import json
from pathlib import Path

import pytest

from xiaohe.agui_projection import ProjectionContext, project_event, project_snapshot
from xiaohe.contracts import ChatSnapshot, ServiceError


def test_machine_schema_is_extracted_from_the_single_external_contract(contract_fixture):
    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[2]
    master = root.parent / "docs/contracts/2026-11-03-xiaohe-integration-v1.md"
    source = master.read_text(encoding="utf-8")
    block = source.split("<!-- xiaohe-agui-v1-schema:start -->\n```json\n", 1)[1].split("\n```", 1)[
        0
    ]
    derived = json.loads(
        (root / "copilot-runtime/contracts/xiaohe-agui-v1.schema.json").read_text(encoding="utf-8")
    )
    comment = derived.pop("$comment")
    assert hashlib.sha256(master.read_bytes()).hexdigest() in comment
    assert derived == json.loads(block)
    Draft202012Validator.check_schema(derived)
    validator = Draft202012Validator(derived)
    for event in project_snapshot(snapshot(contract_fixture, "running"), "attempt-1"):
        validator.validate(event)


def snapshot(contract_fixture, status=None):
    value = contract_fixture("guest_snapshot")
    value["messages"] = [
        {
            "id": "fixture-M1",
            "role": "assistant",
            "createdAt": "2026-11-05T00:00:00Z",
            "parts": contract_fixture("chat_parts"),
        }
    ]
    value["cursor"] = "fixture-E1"
    value["activeRun"] = {"id": "fixture-R1", "status": status} if status else None
    return ChatSnapshot.model_validate(value)


def test_snapshot_preserves_all_six_parts_ids_time_and_watermark_without_csrf(contract_fixture):
    original = snapshot(contract_fixture)
    events = project_snapshot(original, None)
    messages = next(event for event in events if event["type"] == "MESSAGES_SNAPSHOT")
    state = next(event for event in events if event["type"] == "STATE_SNAPSHOT")["snapshot"][
        "xiaohe"
    ]
    assert messages["messages"][0]["id"] == "fixture-M1"
    assert state["messages"] == original.model_dump(mode="json", by_alias=True)["messages"]
    assert state["messages"][0]["parts"][-1]["version"] == 1
    assert state["cursor"] == original.cursor
    assert "csrf" not in state and "fixture-csrf" not in json.dumps(events)


@pytest.mark.parametrize("status", ["queued", "running", "waiting_input"])
def test_snapshot_keeps_business_waiting_separate_from_transport_finish(contract_fixture, status):
    events = project_snapshot(snapshot(contract_fixture, status), "attempt-1")
    assert events[0] == {"type": "RUN_STARTED", "threadId": "fixture-C1", "runId": "attempt-1"}
    state = next(event for event in events if event["type"] == "STATE_SNAPSHOT")["snapshot"][
        "xiaohe"
    ]
    assert state["activeRun"]["status"] == status
    assert any(event["type"] == "RUN_FINISHED" for event in events) == (status == "waiting_input")


@pytest.mark.parametrize("status", ["waiting_input", "completed", "failed", "cancelled", "expired"])
def test_run_updates_finish_only_the_transport_attempt_and_preserve_business_status(status):
    context = ProjectionContext("C1", "attempt-1", "R1")
    event = {
        "eventId": "E1",
        "conversationId": "C1",
        "runId": "R1",
        "type": "run.updated",
        "payload": {"run": {"id": "R1", "status": status}},
    }
    events = project_event(event, context)
    expected = "RUN_FINISHED" if status in ("waiting_input", "completed") else "RUN_ERROR"
    assert events[-1]["type"] == expected
    assert status in json.dumps(events, ensure_ascii=False)


def test_text_delta_uses_stable_message_id_and_rejects_raw_or_wrong_scope():
    context = ProjectionContext("C1", "attempt-1", "R1")
    event = {
        "eventId": "E1",
        "conversationId": "C1",
        "runId": "R1",
        "type": "message.delta",
        "payload": {"messageId": "M1", "delta": "你好"},
    }
    events = project_event(event, context)
    assert any(
        item["type"] == "TEXT_MESSAGE_START" and item["messageId"] == "M1" for item in events
    )
    assert any(
        item["type"] == "TEXT_MESSAGE_CONTENT" and item["delta"] == "你好" for item in events
    )
    for changed in [
        event | {"conversationId": "foreign"},
        event | {"type": "RAW", "payload": {"secret": "private-prompt"}},
    ]:
        with pytest.raises(ServiceError):
            project_event(changed, context)
