from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from xiaohe.contracts import Id, Positive, WireModel

Direction = Literal["buyer", "organization", "supplier"]
DraftKind = Literal["service", "onboarding"]
DraftStep = Literal["round1", "round2", "round3", "files", "summary"]
DraftState = Literal["collecting", "review", "submitting", "accepted"]
FieldValue = str | list[str] | None
FileCategory = Literal["business_license", "product_manual", "certificate", "case"]
MediaType = Literal[
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]


class CreateDraft(WireModel):
    client_draft_id: Id
    direction: Direction
    kind: DraftKind
    subject_id: Id | None = None
    source_application_id: Id | None = None
    source_onboarding_id: Id | None = None

    @model_validator(mode="after")
    def linked_onboarding(self) -> Self:
        if self.kind == "onboarding" and (
            self.direction != "organization" or not self.source_application_id
        ):
            raise ValueError("onboarding requires organization and sourceApplicationId")
        if self.source_onboarding_id and self.kind != "onboarding":
            raise ValueError("sourceOnboardingId requires onboarding")
        return self


class FileRef(WireModel):
    id: Id
    category: FileCategory
    filename: Annotated[Id, Field(max_length=255)]
    media_type: MediaType
    bytes: Annotated[int, Field(strict=True, ge=1, le=33554432)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    status: Literal["pending", "ready", "failed"]

    @model_validator(mode="after")
    def ready_digest(self) -> Self:
        if self.status == "ready" and self.sha256 is None:
            raise ValueError("ready file requires digest")
        return self


class DraftView(WireModel):
    id: Id
    kind: DraftKind
    direction: Direction
    source_application_id: Id | None = None
    source_onboarding_id: Id | None = None
    subject_id: Id | None = None
    revision: Positive
    step: DraftStep
    state: DraftState
    fields: dict[str, FieldValue]
    missing_required: list[str]
    files: list[FileRef]
    summary_version: Positive | None = None


class DraftRef(WireModel):
    id: Id
    kind: DraftKind
    direction: Direction
    subject_name: str
    summary: str
    step: DraftStep
    revision: Positive
    state: DraftState


class ReceptionView(WireModel):
    version: Literal[1] = 1
    conversation_id: Id
    direction: Direction | None
    invitation_state: Literal["none", "offered", "deferred", "accepted"]
    pending_request_id: Id | None
    draft_refs: list[DraftRef]
    active_draft: DraftView | None


class ReceptionAction(WireModel):
    client_action_id: Id
    request_id: Id
    expected_revision: Positive
    action: Literal["save_round", "skip_optional", "show_summary", "confirm", "resume"]
    summary_version: Positive | None = None

    @model_validator(mode="after")
    def current_confirmation(self) -> Self:
        if self.action == "confirm" and self.summary_version is None:
            raise ValueError("confirmation requires summaryVersion")
        return self
