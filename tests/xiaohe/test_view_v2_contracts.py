import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import Response

from xiaohe.contracts import CreateAuthOperation, ServiceError
from xiaohe.contracts_v2 import (
    BootstrapInputV2,
    ChatSnapshotV2,
    CreateAuthOperationV2,
    EventEnvelopeV2,
    HistoryPageV2,
    MessageBaselineV2,
    PublicSnapshotV2,
)
from xiaohe.view_version import parse_view_version, set_view_version


def snapshot(contract_fixture):
    value = contract_fixture("guest_snapshot")
    value.pop("cursor")
    return dict(
        value,
        version=2,
        sessionExpiresAt=value["viewer"]["expiresAt"],
        activeInput=None,
        history=dict(pageSize=10, hasMore=False, olderCursor=None, historyEpoch=0),
        sync=dict(eventCursor=None, watermarkSequence=0),
    )


def test_v2_contract_rejects_unknown_fields_and_invalid_watermarks(contract_fixture):
    value = snapshot(contract_fixture)
    assert ChatSnapshotV2.model_validate(value).model_dump(mode="json", by_alias=True) == value
    for path, bad in [
        (("version",), 1),
        (("version",), "2"),
        (("version",), 2.0),
        (("extra",), True),
        (("history", "pageSize"), 11),
        (("history", "pageSize"), 10.0),
        (("history", "hasMore"), 1),
        (("history", "olderCursor"), "cursor"),
        (("history", "hasMore"), True),
        (("history", "historyEpoch"), -1),
        (("sync", "watermarkSequence"), "1"),
        (("sync", "watermarkSequence"), 1.5),
        (("sync", "watermarkSequence"), True),
        (("sync", "watermarkSequence"), 2**53),
        (("sync", "watermarkSequence"), 1),
        (("sync", "eventCursor"), "E1"),
    ]:
        candidate = copy.deepcopy(value)
        target = candidate
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad
        with pytest.raises(ValidationError):
            ChatSnapshotV2.model_validate(candidate)
    value["messages"] = [
        dict(
            id=f"M{i}",
            role="assistant",
            createdAt="2026-11-07T00:00:00Z",
            parts=contract_fixture("chat_parts"),
        )
        for i in range(11)
    ]
    with pytest.raises(ValidationError):
        ChatSnapshotV2.model_validate(value)
    value["messages"].pop()
    ChatSnapshotV2.model_validate(value)
    value["messages"][0]["parts"][0]["private"] = "reject"
    with pytest.raises(ValidationError):
        ChatSnapshotV2.model_validate(value)


def test_v2_page_baseline_and_events_are_strict(contract_fixture):
    message = dict(
        id="M1",
        role="assistant",
        createdAt="2026-11-07T00:00:00Z",
        parts=contract_fixture("chat_parts"),
    )
    value = snapshot(contract_fixture)
    page = dict(
        version=2,
        conversationId="C1",
        messages=[message],
        history=value["history"],
        watermarkSequence=1,
    )
    HistoryPageV2.model_validate(page)
    MessageBaselineV2.model_validate(
        dict(version=2, conversationId="C1", message=message, historyEpoch=0, watermarkSequence=1)
    )
    public = {k: v for k, v in value.items() if k != "csrf"}
    public.update(attemptId=None, businessRunId=None, businessRunStatus=None)
    PublicSnapshotV2.model_validate(public)
    with pytest.raises(ValidationError):
        PublicSnapshotV2.model_validate(dict(public, csrf=value["csrf"]))
    event = dict(
        eventId="E1",
        conversationId="C1",
        runId=None,
        type="message.upsert",
        payload={"message": message},
        sequence=1,
        historyEpoch=0,
    )
    adapter = TypeAdapter(EventEnvelopeV2)
    adapter.validate_python(event)
    for bad in (0, -1, "1", 1.5, True, 2**53):
        with pytest.raises(ValidationError):
            adapter.validate_python(dict(event, sequence=bad))
    with pytest.raises(ValidationError):
        adapter.validate_python(dict(event, unknown=True))


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], 1),
        (["1"], 1),
        (["2"], 2),
        ([""], None),
        (["3"], None),
        (["2", "2"], None),
        (["1,2"], None),
    ],
)
def test_view_header_is_explicit(values, expected):
    request = Request(
        {"type": "http", "headers": [(b"x-xiaohe-view-version", v.encode()) for v in values]}
    )
    if expected is None:
        with pytest.raises(ServiceError) as error:
            parse_view_version(request)
        assert (error.value.code, error.value.http_status) == ("VIEW_VERSION_UNSUPPORTED", 406)
    else:
        assert parse_view_version(request) == expected
    if values != ["2"]:
        with pytest.raises(ServiceError):
            parse_view_version(request, v2_only=True)
    response = Response()
    set_view_version(response, 2)
    assert response.headers["X-Xiaohe-View-Version"] == "2"


def test_expected_identity_is_only_a_required_restore_constraint():
    identity = dict(issuer="https://sso.example", sub="fixture-user", sourceTenantId=165)
    oidc = dict(
        clientId="portal",
        redirectUri="https://portal.example/callback",
        state="s",
        nonce="n",
        codeChallenge="challenge",
        codeChallengeMethod="S256",
    )
    body = dict(
        intent="restore",
        oidc=oidc,
        browserCompletionChallenge="challenge",
        browserCompletionChallengeMethod="S256",
    )
    CreateAuthOperationV2.model_validate(dict(body, expectedIdentity=identity))
    for candidate in (
        body,
        dict(body, expectedIdentity=None),
        dict(body, intent="login", expectedIdentity=identity),
    ):
        with pytest.raises(ValidationError):
            CreateAuthOperationV2.model_validate(candidate)
    CreateAuthOperationV2.model_validate(dict(body, intent="login"))
    with pytest.raises(ValidationError):
        CreateAuthOperation.model_validate(dict(body, expectedIdentity=identity))
    assert BootstrapInputV2.model_validate({}).expected_identity is None
    BootstrapInputV2.model_validate({"expectedIdentity": identity})
    with pytest.raises(ValidationError):
        BootstrapInputV2.model_validate({"expectedIdentity": None})


def test_v2_machine_block_mirror_and_fixed_fixtures():
    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[2]
    master = root.parent / "docs/contracts/2026-11-03-xiaohe-integration-v1.md"
    mirror = root / "docs/contracts/2026-11-03-xiaohe-integration-v1.md"
    if not master.exists():
        master = mirror
    assert master.read_bytes() == mirror.read_bytes()
    source = master.read_text(encoding="utf-8")
    block = source.split("<!-- xiaohe-agui-v2-schema:start -->\n```json\n", 1)[1].split("\n```", 1)[
        0
    ]
    derived = json.loads(
        (root / "copilot-runtime/contracts/xiaohe-agui-v2.schema.json").read_text("utf-8")
    )
    assert hashlib.sha256(master.read_bytes()).hexdigest() in derived.pop("$comment")
    assert derived == json.loads(block)
    Draft202012Validator.check_schema(derived)
    fixtures = json.loads((root / "tests/xiaohe/contracts/view_v2.json").read_text("utf-8"))
    models = {
        "snapshot": ChatSnapshotV2,
        "page": HistoryPageV2,
        "baseline": MessageBaselineV2,
        "public": PublicSnapshotV2,
        "bootstrap": BootstrapInputV2,
        "restore": CreateAuthOperationV2,
        "event": TypeAdapter(EventEnvelopeV2),
    }
    for name, value in fixtures.items():
        model = models.get(name)
        if model is not None:
            (
                model.validate_python(value)
                if isinstance(model, TypeAdapter)
                else model.model_validate(value)
            )
        Draft202012Validator(derived).validate(value)
