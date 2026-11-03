import copy
import json
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from xiaohe.contracts import (
    ApiError,
    ArtifactView,
    AuthActionBody,
    AuthOperation,
    AuthStage,
    BoundCompletion,
    BrowserCompletion,
    ChatAction,
    ChatPart,
    ChatSnapshot,
    CreateAuthOperation,
    EventEnvelope,
    MessageAccepted,
    MessageInput,
    PictureChallenge,
    RunStatus,
    SmsChallenge,
    TrustedSystem,
    VerifiedCompletion,
)
from xiaohe.settings import Settings


def test_shared_dto_aliases_and_sensitive_fields(contract_fixture):
    for name in ("guest_snapshot", "user_snapshot"):
        value = contract_fixture(name)
        assert ChatSnapshot.model_validate(value).model_dump(mode="json", by_alias=True) == value
    value = contract_fixture("accepted_turn")
    assert MessageAccepted.model_validate(value).model_dump(mode="json", by_alias=True) == value
    for value in contract_fixture("chat_parts"):
        dto = TypeAdapter(ChatPart).validate_python(value)
        assert dto.model_dump(mode="json", by_alias=True) == value
    for value in contract_fixture("errors"):
        assert ApiError.model_validate(value).model_dump(mode="json", by_alias=True) == value
    for value in (
        {"type": "auth_request", "intent": "restore"},
        {"type": "text", "text": "hi", "mobile": "fixture-phone"},
        {"type": "verify", "code": "fixture-otp"},
    ):
        with pytest.raises(ValidationError):
            TypeAdapter(ChatPart).validate_python(value)


def test_all_run_and_auth_stages(contract_fixture):
    assert set(get_args(RunStatus)) == {
        "queued",
        "running",
        "waiting_input",
        "completed",
        "failed",
        "cancelled",
        "expired",
    }
    stages = contract_fixture("auth_stages")
    assert {value["stage"] for value in stages} == set(get_args(AuthStage))
    for value in stages:
        assert AuthOperation.model_validate(value).model_dump(mode="json", by_alias=True) == value


@pytest.mark.parametrize("status", [None, "MERGE_PENDING", "MERGED", "SKIPPED_EXPIRED"])
def test_merge_states_and_null(contract_fixture, status):
    value = contract_fixture("user_snapshot")
    value["merge"] = None if status is None else {"operationId": "op", "status": status}
    result = ChatSnapshot.model_validate(value)
    assert result.model_dump(mode="json", by_alias=True)["merge"] == value["merge"]
    if status:
        value["viewer"] = contract_fixture("guest_snapshot")["viewer"]
        with pytest.raises(ValidationError):
            ChatSnapshot.model_validate(value)


@pytest.mark.parametrize(
    "text",
    ["", " \t\n", "😀" * 10001, None, 123],
    ids=["empty", "blank", "too-long", "null", "number"],
)
def test_message_invalid_text(text):
    with pytest.raises(ValidationError):
        MessageInput(clientMessageId="key", text=text)


def test_unicode_codepoints_and_untrimmed_text():
    value = " 😀" + "字" * 9997 + " "
    assert len(value) == 10000
    assert MessageInput(clientMessageId="😀" * 128, text=value).text == value
    with pytest.raises(ValidationError):
        MessageInput(clientMessageId="😀" * 129, text="ok")


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"optionId": "x", "text": "x"},
        {"optionId": None},
        {"text": None},
        {"optionId": " "},
        {"text": ""},
        {"optionId": "x", "code": "fixture-otp"},
        {"optionId": "x", "user_id": "attacker"},
    ],
)
def test_action_requires_exactly_one_nonnull_value(extra):
    with pytest.raises(ValidationError):
        ChatAction(clientActionId="key", requestId="q", **extra)


@pytest.mark.parametrize("extra", [{"optionId": "intro"}, {"text": " 😀 "}])
def test_action_preserves_omission(extra):
    value = {"clientActionId": "key", "requestId": "q", **extra}
    assert ChatAction.model_validate(value).model_dump(mode="json", by_alias=True) == value


def test_event_discriminator_payload_and_opaque_watermark(contract_fixture):
    events = contract_fixture("events")
    assert {value["type"] for value in events} == {
        "message.delta",
        "message.upsert",
        "run.updated",
        "artifact.updated",
        "viewer.updated",
        "viewer.expired",
        "error",
    }
    for value in events:
        assert (
            TypeAdapter(EventEnvelope).validate_python(value).model_dump(mode="json", by_alias=True)
            == value
        )
        wrong = {**value, "payload": {"unknown": "fixture"}}
        with pytest.raises(ValidationError):
            TypeAdapter(EventEnvelope).validate_python(wrong)
    delta = copy.deepcopy(events[0])
    delta["runId"] = None
    with pytest.raises(ValidationError):
        TypeAdapter(EventEnvelope).validate_python(delta)
    run = next(copy.deepcopy(x) for x in events if x["type"] == "run.updated")
    run["runId"] = "foreign-run"
    with pytest.raises(ValidationError):
        TypeAdapter(EventEnvelope).validate_python(run)
    snapshot = contract_fixture("guest_snapshot")
    snapshot["cursor"] = events[-1]["eventId"]
    assert ChatSnapshot.model_validate(snapshot).cursor == events[-1]["eventId"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenantId", True),
        ("tenantId", 2**53),
        ("tenantId", -1),
        ("messageCount", -1),
        ("messageLimit", 0),
        ("retentionSeconds", 0),
        ("expiresAt", "2026-11-03T12:00:00+08:00"),
        ("expiresAt", 1793664000),
        ("expiresAt", 1.5),
        ("expiresAt", "1793664000"),
    ],
)
def test_snapshot_rejects_invalid_scalars(contract_fixture, field, value):
    snapshot = contract_fixture("guest_snapshot")
    snapshot["viewer"][field] = value
    with pytest.raises(ValidationError):
        ChatSnapshot.model_validate(snapshot)


def test_unique_ids_and_unknown_fields(contract_fixture):
    value = contract_fixture("accepted_turn")
    snapshot = contract_fixture("guest_snapshot")
    snapshot["messages"] = [value["message"], value["message"]]
    with pytest.raises(ValidationError):
        ChatSnapshot.model_validate(snapshot)
    for part in contract_fixture("chat_parts"):
        if part["type"] in ("choice", "suggestions"):
            key = "options" if part["type"] == "choice" else "items"
            part[key] *= 2
            with pytest.raises(ValidationError):
                TypeAdapter(ChatPart).validate_python(part)
    snapshot = contract_fixture("guest_snapshot")
    snapshot["checkpoint_ns"] = "foreign"
    with pytest.raises(ValidationError):
        ChatSnapshot.model_validate(snapshot)


def test_verified_completion_has_six_fields_and_local_binding(contract_fixture):
    value = contract_fixture("verified_completion")
    identity = VerifiedCompletion.model_validate(value)
    assert identity.model_dump(mode="json", by_alias=True) == value
    assert len(value) == 6 and identity.sub == "member:1:fixture-opaque-sub"
    system = TrustedSystem(
        system_id="portal",
        tenant_id=1,
        portal_client_id="app_localportal",
        allowed_origin="https://portal.lvh.me:9444",
    )
    assert BoundCompletion(identity=identity, system=system).identity == identity
    for key in ("userId", "authorizationId", "session_valid_until"):
        with pytest.raises(ValidationError):
            VerifiedCompletion.model_validate({**value, key: "forged"})


def test_internal_datetime_remains_supported(contract_fixture):
    value = contract_fixture("verified_completion")
    stamp = datetime(2026, 11, 3, tzinfo=UTC)
    identity = VerifiedCompletion.model_validate({**value, "authenticatedAt": stamp})
    assert identity.authenticated_at == stamp
    assert identity.model_dump(mode="json", by_alias=True) == value


@pytest.mark.parametrize("stamp", [0, 1.5, "0"])
def test_json_timestamps_are_iso_strings(contract_fixture, stamp):
    value = contract_fixture("verified_completion")
    value["authenticatedAt"] = stamp
    with pytest.raises(ValidationError):
        VerifiedCompletion.model_validate_json(json.dumps(value))


def test_auth_request_secrets_are_redacted_and_wire_values_survive():
    bodies = [
        ("captcha/check", {"token": "fixture-token", "pointJson": "fixture-point"}),
        ("sms", {"mobile": "fixture-phone", "captchaVerification": "fixture-proof"}),
        ("verify", {"mobile": "fixture-phone", "challengeId": "ch", "code": "fixture-otp"}),
        ("agreement", {"accepted": True, "version": "v1"}),
        ("captcha", {}),
        ("cancel", {}),
    ]
    for action, body in bodies:
        dto = TypeAdapter(AuthActionBody).validate_python({"action": action, "body": body})
        for value in body.values():
            if isinstance(value, str) and value.startswith("fixture-"):
                assert value not in repr(dto)
        assert json.loads(dto.model_dump_json(by_alias=True))["body"] == body
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(AuthActionBody).validate_python(
            {"action": "verify", "body": {**bodies[2][1], "userId": "fixture-private"}}
        )
    assert "fixture-private" not in str(exc.value)
    completion = BrowserCompletion(
        method="POST",
        action="https://sso.lvh.me:8443/sso/bridge/browser-completion",
        fields={"credential": "fixture-credential"},
    )
    assert "fixture-credential" not in repr(completion)
    assert completion.model_dump(mode="json")["fields"]["credential"] == "fixture-credential"
    with pytest.raises(ValidationError):
        BrowserCompletion(
            method="POST",
            action="https://sso.lvh.me:8443/sso/bridge/browser-completion",
            fields={"browserVerifier": "forbidden"},
        )


def test_remaining_wire_dtos():
    binding = {
        "clientId": "app_localportal",
        "redirectUri": "https://portal.lvh.me:9444/portal/auth/callback",
        "state": "state",
        "nonce": "nonce",
        "codeChallenge": "challenge",
        "codeChallengeMethod": "S256",
    }
    dto = CreateAuthOperation(
        intent="restore",
        oidc=binding,
        browserCompletionChallenge="independent",
        browserCompletionChallengeMethod="S256",
    )
    assert dto.oidc.code_challenge != dto.browser_completion_challenge
    picture = PictureChallenge(
        token="fixture-token",
        secretKey="fixture-secret",
        imageBase64="image",
        pieceImageBase64="piece",
    )
    assert "fixture-secret" not in repr(picture)
    assert picture.model_dump(mode="json", by_alias=True)["secretKey"] == "fixture-secret"
    assert (
        SmsChallenge(
            challengeId="id", retryAfterSeconds=60, expiresAt="2026-11-03T00:02:00Z"
        ).retry_after_seconds
        == 60
    )
    artifact = ArtifactView(
        artifactId="a",
        version=1,
        title="模拟",
        mediaType="text/plain",
        status="ready",
        preview={"format": "text", "text": "模拟"},
        downloadPath="/ai-api/v1/artifacts/a/versions/1/download",
    )
    assert artifact.version == 1


@pytest.mark.parametrize(
    "preview", [{"format": "download_only"}, {"format": "download_only", "text": None}]
)
def test_download_only_preview_omits_text_and_rejects_explicit_null(preview):
    value = {
        "artifactId": "a",
        "version": 1,
        "title": "模拟",
        "mediaType": "text/plain",
        "status": "ready",
        "preview": preview,
        "downloadPath": None,
    }
    if "text" in preview:
        with pytest.raises(ValidationError):
            ArtifactView.model_validate(value)
    else:
        assert ArtifactView.model_validate(value).model_dump(mode="json", by_alias=True) == value


def test_settings_require_server_secret_and_controlled_origin(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("TOOLKIT_BASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    for secret in ("", " ", "bad\r\nheader"):
        with pytest.raises(ValidationError):
            Settings(toolkit_base_url="http://127.0.0.1:18090", auth_secret=secret)
    for url in (
        "http://user:secret@localhost",
        "https://host/path",
        "https://host/?secret=x",
        "https://host/#secret",
        "file:///tmp/test",
    ):
        with pytest.raises(ValidationError):
            Settings(toolkit_base_url=url, auth_secret="fixture-auth")
    monkeypatch.setenv("AUTH_SECRET", "fixture-auth")
    monkeypatch.setenv("TOOLKIT_BASE_URL", "http://127.0.0.1:18090")
    settings = Settings(_env_file=None)
    assert settings.visitor_message_limit == 100 and settings.visitor_retention_seconds == 14400
    assert "fixture-auth" not in repr(settings)
    monkeypatch.setenv("VISITOR_MESSAGE_LIMIT", "2")
    monkeypatch.setenv("VISITOR_RETENTION_SECONDS", "60")
    assert Settings().visitor_message_limit == 2
    assert Settings().visitor_retention_seconds == 60
