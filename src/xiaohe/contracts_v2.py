"""共享契约 §12：显式选择的有界视图，不改变 V1 DTO。"""

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from xiaohe.contracts import (
    ArtifactEvent,
    ChatMessage,
    CreateAuthOperation,
    Csrf,
    DeltaEvent,
    ErrorEvent,
    ExpiredEvent,
    Id,
    MergeState,
    MessageEvent,
    NonNegative,
    Positive,
    RunEvent,
    RunRef,
    RunStatus,
    UtcTime,
    Viewer,
    ViewerEvent,
    WireModel,
    unique_ids,
)


def integer(value: object) -> object:
    if type(value) is not int:
        raise ValueError("JSON integer required")
    return value


Version2 = Annotated[Literal[2], BeforeValidator(integer)]
PageSize = Annotated[Literal[10], BeforeValidator(integer)]


class ExpectedIdentity(WireModel):
    issuer: Id
    sub: Id
    source_tenant_id: NonNegative


class BootstrapInputV2(WireModel):
    expected_identity: ExpectedIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def omitted_not_null(self) -> Self:
        if "expected_identity" in self.model_fields_set and self.expected_identity is None:
            raise ValueError("omit expectedIdentity instead of null")
        return self


class CreateAuthOperationV2(CreateAuthOperation):
    expected_identity: ExpectedIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def restore_constraint(self) -> Self:
        supplied = "expected_identity" in self.model_fields_set
        if self.intent == "restore":
            if self.expected_identity is None:
                raise ValueError("restore requires expectedIdentity")
        elif supplied:
            raise ValueError("expectedIdentity is only permitted for restore")
        return self


class HistoryV2(WireModel):
    page_size: PageSize
    has_more: bool = Field(strict=True)
    older_cursor: Id | None
    history_epoch: NonNegative

    @model_validator(mode="after")
    def cursor_matches(self) -> Self:
        if self.has_more != (self.older_cursor is not None):
            raise ValueError("hasMore requires olderCursor and vice versa")
        return self


class SyncV2(WireModel):
    event_cursor: Id | None
    watermark_sequence: NonNegative

    @model_validator(mode="after")
    def empty_stream(self) -> Self:
        if (self.event_cursor is None) != (self.watermark_sequence == 0):
            raise ValueError("empty stream requires null cursor and zero watermark")
        return self


class ActiveInputV2(WireModel):
    run_id: Id
    message_id: Id
    request_id: Id
    kind: Literal["choice", "input_request"]


class SnapshotFieldsV2(WireModel):
    version: Version2
    viewer: Viewer
    session_expires_at: UtcTime
    conversation_id: Id
    messages: list[ChatMessage] = Field(max_length=10)
    active_run: RunRef | None
    active_input: ActiveInputV2 | None
    merge: MergeState | None
    history: HistoryV2
    sync: SyncV2

    @model_validator(mode="after")
    def consistent(self) -> Self:
        unique_ids(self.messages)
        if self.merge is not None and self.viewer.kind != "user":
            raise ValueError("merge requires user viewer")
        if self.viewer.kind == "guest" and self.session_expires_at != self.viewer.expires_at:
            raise ValueError("guest session and viewer expiry differ")
        if self.active_input is not None and (
            self.active_run is None
            or self.active_run.status != "waiting_input"
            or self.active_input.run_id != self.active_run.id
        ):
            raise ValueError("activeInput requires its waiting_input run")
        return self


class ChatSnapshotV2(SnapshotFieldsV2):
    csrf: Csrf


class PublicSnapshotV2(SnapshotFieldsV2):
    attempt_id: Id | None
    business_run_id: Id | None
    business_run_status: RunStatus | None


class HistoryPageV2(WireModel):
    version: Version2
    conversation_id: Id
    messages: list[ChatMessage] = Field(max_length=10)
    history: HistoryV2
    watermark_sequence: NonNegative

    @model_validator(mode="after")
    def unique_messages(self) -> Self:
        unique_ids(self.messages)
        return self


class MessageBaselineV2(WireModel):
    version: Version2
    conversation_id: Id
    message: ChatMessage
    history_epoch: NonNegative
    watermark_sequence: NonNegative


class EventPositionV2(WireModel):
    sequence: Positive
    history_epoch: NonNegative


class DeltaEventV2(DeltaEvent, EventPositionV2):
    pass


class MessageEventV2(MessageEvent, EventPositionV2):
    pass


class RunEventV2(RunEvent, EventPositionV2):
    pass


class ArtifactEventV2(ArtifactEvent, EventPositionV2):
    pass


class ViewerEventV2(ViewerEvent, EventPositionV2):
    pass


class ExpiredEventV2(ExpiredEvent, EventPositionV2):
    pass


class ErrorEventV2(ErrorEvent, EventPositionV2):
    pass


EventEnvelopeV2 = Annotated[
    DeltaEventV2
    | MessageEventV2
    | RunEventV2
    | ArtifactEventV2
    | ViewerEventV2
    | ExpiredEventV2
    | ErrorEventV2,
    Field(discriminator="type"),
]
