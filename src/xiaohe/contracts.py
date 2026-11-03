from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic.alias_generators import to_camel


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def utc(value: datetime) -> datetime:
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("UTC timestamp required")
    return value


def iso_or_datetime(value: object) -> object:
    if not isinstance(value, datetime) and not (isinstance(value, str) and "T" in value):
        raise ValueError("ISO timestamp string required")
    return value


Id = Annotated[str, StringConstraints(strict=True, min_length=1), AfterValidator(nonblank)]
InputId = Annotated[Id, StringConstraints(max_length=128)]
TextInput = Annotated[Id, StringConstraints(max_length=10000)]
NonNegative = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
Positive = Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
UtcTime = Annotated[AwareDatetime, BeforeValidator(iso_or_datetime), AfterValidator(utc)]
# JSON is the explicit wire boundary; repr and Python dumps retain redacted SecretStr.
Sensitive = Annotated[
    SecretStr,
    PlainSerializer(lambda value: value.get_secret_value(), return_type=str, when_used="json"),
]
RunStatus = Literal[
    "queued", "running", "waiting_input", "completed", "failed", "cancelled", "expired"
]
ArtifactStatus = Literal["pending", "ready", "failed"]
AuthIntent = Literal["lead", "register", "login", "restore"]
AuthStage = Literal[
    "PHONE_REQUIRED",
    "AGREEMENT_REQUIRED",
    "BROWSER_COMPLETION_REQUIRED",
    "OIDC_REQUIRED",
    "COMPLETED",
    "FAILED",
    "EXPIRED",
]
Direction = str


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


class GuestViewer(WireModel):
    kind: Literal["guest"]
    guest_id: Id
    system_id: Id
    tenant_id: NonNegative
    message_count: NonNegative
    message_limit: Positive
    retention_seconds: Positive
    expires_at: UtcTime


class UserViewer(WireModel):
    kind: Literal["user"]
    user_id: Id
    system_id: Id
    tenant_id: NonNegative
    display_name: str = Field(strict=True)


Viewer = Annotated[GuestViewer | UserViewer, Field(discriminator="kind")]


class TextPart(WireModel):
    type: Literal["text"]
    text: str = Field(strict=True)


class Option(WireModel):
    id: InputId
    label: str = Field(strict=True)


def unique_ids(items: list) -> None:
    if len({item.id for item in items}) != len(items):
        raise ValueError("duplicate identifiers")


class ChoicePart(WireModel):
    type: Literal["choice"]
    request_id: InputId
    prompt: str = Field(strict=True)
    options: list[Option]

    @model_validator(mode="after")
    def unique_options(self) -> Self:
        unique_ids(self.options)
        return self


class Suggestion(WireModel):
    id: Id
    text: str = Field(strict=True)


class SuggestionsPart(WireModel):
    type: Literal["suggestions"]
    items: list[Suggestion]

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        unique_ids(self.items)
        return self


class InputRequestPart(WireModel):
    type: Literal["input_request"]
    request_id: InputId
    prompt: str = Field(strict=True)


class AuthRequestPart(WireModel):
    type: Literal["auth_request"]
    intent: Literal["lead", "register", "login"]


class ArtifactRef(WireModel):
    type: Literal["artifact_ref"]
    artifact_id: Id
    version: Positive
    title: str = Field(strict=True)
    media_type: Id
    status: ArtifactStatus


ChatPart = Annotated[
    TextPart | ChoicePart | SuggestionsPart | InputRequestPart | AuthRequestPart | ArtifactRef,
    Field(discriminator="type"),
]


class ChatMessage(WireModel):
    id: Id
    role: Literal["user", "assistant"]
    created_at: UtcTime
    parts: list[ChatPart]


class RunRef(WireModel):
    id: Id
    status: RunStatus


class MergeState(WireModel):
    operation_id: Id
    status: Literal["MERGE_PENDING", "MERGED", "SKIPPED_EXPIRED"]


class Csrf(WireModel):
    header_name: Id
    token: Id = Field(repr=False)


class ChatSnapshot(WireModel):
    viewer: Viewer
    csrf: Csrf
    conversation_id: Id
    messages: list[ChatMessage]
    active_run: RunRef | None
    cursor: Id | None
    merge: MergeState | None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        unique_ids(self.messages)
        if self.merge is not None and self.viewer.kind != "user":
            raise ValueError("merge requires user viewer")
        return self


class MessageInput(WireModel):
    client_message_id: InputId
    text: TextInput


class ChatAction(WireModel):
    client_action_id: InputId
    request_id: InputId
    option_id: InputId | None = Field(default=None, exclude_if=lambda value: value is None)
    text: TextInput | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def exactly_one(self) -> Self:
        supplied = self.model_fields_set & {"option_id", "text"}
        if len(supplied) != 1 or getattr(self, next(iter(supplied))) is None:
            raise ValueError("supply exactly one nonnull optionId or text")
        return self


class MessageAccepted(WireModel):
    message: ChatMessage
    run: RunRef
    viewer: Viewer


AcceptedTurn = MessageAccepted


class ErrorDetail(WireModel):
    code: Id
    message: str = Field(strict=True)
    retryable: bool = Field(strict=True)


class ApiError(WireModel):
    error: ErrorDetail
    request_id: Id


class MessageDelta(WireModel):
    message_id: Id
    delta: str = Field(strict=True)


class MessageUpsert(WireModel):
    message: ChatMessage


class RunUpdated(WireModel):
    run: RunRef


class ArtifactUpdated(WireModel):
    artifact_id: Id
    version: Positive
    status: ArtifactStatus


class ViewerUpdated(WireModel):
    viewer: Viewer


class ViewerExpired(WireModel):
    code: Literal["VISITOR_EXPIRED"]


class EventBase(WireModel):
    event_id: Id
    conversation_id: Id
    run_id: Id | None


class DeltaEvent(EventBase):
    type: Literal["message.delta"]
    run_id: Id
    payload: MessageDelta


class MessageEvent(EventBase):
    type: Literal["message.upsert"]
    payload: MessageUpsert


class RunEvent(EventBase):
    type: Literal["run.updated"]
    run_id: Id
    payload: RunUpdated

    @model_validator(mode="after")
    def matching_run(self) -> Self:
        if self.run_id != self.payload.run.id:
            raise ValueError("run identifiers differ")
        return self


class ArtifactEvent(EventBase):
    type: Literal["artifact.updated"]
    payload: ArtifactUpdated


class ViewerEvent(EventBase):
    type: Literal["viewer.updated"]
    payload: ViewerUpdated


class ExpiredEvent(EventBase):
    type: Literal["viewer.expired"]
    payload: ViewerExpired


class ErrorEvent(EventBase):
    type: Literal["error"]
    payload: ApiError


EventEnvelope = Annotated[
    DeltaEvent | MessageEvent | RunEvent | ArtifactEvent | ViewerEvent | ExpiredEvent | ErrorEvent,
    Field(discriminator="type"),
]
ChatEvent = EventEnvelope


class OidcBinding(WireModel):
    client_id: Id
    redirect_uri: Id
    state: Id = Field(repr=False)
    nonce: Id = Field(repr=False)
    code_challenge: Id
    code_challenge_method: Literal["S256"]


class BrowserCompletion(WireModel):
    method: Literal["GET", "POST"]
    action: Id
    fields: dict[str, Sensitive] = Field(repr=False)

    @model_validator(mode="after")
    def browser_owns_verifier(self) -> Self:
        if "browserVerifier" in self.fields:
            raise ValueError("browserVerifier belongs to original browser")
        return self


class Agreement(WireModel):
    version: Id
    url: Id


class AuthOperation(WireModel):
    operation_id: Id
    stage: AuthStage
    expires_at: UtcTime
    agreement: Agreement | None = Field(default=None, exclude_if=lambda value: value is None)
    completion: BrowserCompletion | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def omitted_not_null(self) -> Self:
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set & {"agreement", "completion"}
        ):
            raise ValueError("omit optional auth fields instead of null")
        return self


class CreateAuthOperation(WireModel):
    intent: AuthIntent
    oidc: OidcBinding
    browser_completion_challenge: Id
    browser_completion_challenge_method: Literal["S256"]


class PictureChallenge(WireModel):
    token: Sensitive
    secret_key: Sensitive
    image_base64: Sensitive
    piece_image_base64: Sensitive


class SmsChallenge(WireModel):
    challenge_id: Id
    retry_after_seconds: NonNegative
    expires_at: UtcTime


class CaptchaCheck(WireModel):
    token: Sensitive
    point_json: Sensitive


class SmsSend(WireModel):
    mobile: Sensitive
    captcha_verification: Sensitive


class SmsVerify(WireModel):
    mobile: Sensitive
    challenge_id: Id
    code: Sensitive


class AgreementAccept(WireModel):
    accepted: Annotated[bool, Field(strict=True)]
    version: Id

    @model_validator(mode="after")
    def consent(self) -> Self:
        if self.accepted is not True:
            raise ValueError("explicit consent required")
        return self


class EmptyBody(WireModel):
    pass


# Internal operation selector, never an additional public HTTP body field.
class CaptchaAction(WireModel):
    action: Literal["captcha"]
    body: EmptyBody


class CheckAction(WireModel):
    action: Literal["captcha/check"]
    body: CaptchaCheck


class SmsAction(WireModel):
    action: Literal["sms"]
    body: SmsSend


class VerifyAction(WireModel):
    action: Literal["verify"]
    body: SmsVerify


class AgreementAction(WireModel):
    action: Literal["agreement"]
    body: AgreementAccept


class CancelAction(WireModel):
    action: Literal["cancel"]
    body: EmptyBody


AuthAction = Annotated[
    CaptchaAction | CheckAction | SmsAction | VerifyAction | AgreementAction | CancelAction,
    Field(discriminator="action"),
]


class AuthActionBody(RootModel[AuthAction]):
    model_config = ConfigDict(hide_input_in_errors=True)


class ArtifactPreview(WireModel):
    format: Literal["markdown", "text", "download_only"]
    text: str | None = Field(default=None, strict=True, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def omitted_not_null(self) -> Self:
        if "text" in self.model_fields_set and self.text is None:
            raise ValueError("omit optional preview text instead of null")
        return self


class ArtifactView(WireModel):
    artifact_id: Id
    version: Positive
    title: str = Field(strict=True)
    media_type: Id
    status: ArtifactStatus
    preview: ArtifactPreview
    download_path: Id | None


class VerifiedCompletion(WireModel):
    issuer: Id
    sub: Id
    source_tenant_id: NonNegative
    authenticated_at: UtcTime
    portal_client_id: Id
    transaction_id: Id


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TrustedSystem(InternalModel):
    system_id: Id
    tenant_id: NonNegative
    portal_client_id: Id
    allowed_origin: Id


class BoundCompletion(InternalModel):
    identity: VerifiedCompletion
    system: TrustedSystem


class Actor(InternalModel):
    system: TrustedSystem
    session_id: UUID
    guest_id: UUID | None
    user_id: Id | None

    @model_validator(mode="after")
    def one_subject(self) -> Self:
        if (self.guest_id is None) == (self.user_id is None):
            raise ValueError("exactly one actor subject required")
        return self


class WriteFence(InternalModel):
    run_id: UUID
    attempt: Positive
    ownership_epoch: NonNegative


class RunInput(InternalModel):
    message_id: UUID
    action_request_id: UUID | None


class ReceptionContext(InternalModel):
    recent_messages: list[ChatMessage]
    direction: Direction
    pending_request: dict | None
    confirmed_facts: dict


class RoutingAnswer(InternalModel):
    direction: Literal["unknown", "buyer", "organization", "supplier"]
    intent: Literal["reception", "professional", "clarify", "off_topic"]
    answer: Annotated[str, Field(strict=True, min_length=1, max_length=4000)]


class ReceptionDecision(RoutingAnswer):
    target_direction: Literal["unknown", "buyer", "organization", "supplier"]
    auth_intent: Literal["lead", "register", "login"] | None


class AgentOutcome(InternalModel):
    parts: list[ChatPart]
    wait_for_input: bool = Field(strict=True)
    artifact_version_ids: list[UUID]


class ToolkitExecution(WriteFence):
    user_id: Id
    thread_id: Id
    mode: Literal["start", "recover", "resume"]


class ToolkitDelta(InternalModel):
    kind: Literal["delta"]
    message_id: UUID
    delta: str = Field(strict=True)


class ToolkitOutcome(InternalModel):
    kind: Literal["outcome"]
    outcome: AgentOutcome


class ToolkitError(InternalModel):
    kind: Literal["error"]
    code: Id
    retryable: bool = Field(strict=True)


ToolkitEvent = Annotated[ToolkitDelta | ToolkitOutcome | ToolkitError, Field(discriminator="kind")]


class ServiceError(Exception):
    def __init__(self, code: str, http_status: int, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
