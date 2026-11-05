from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from xiaohe.contracts import Id as BaseId
from xiaohe.contracts import Positive, WireModel

Id = Annotated[BaseId, Field(max_length=200)]

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


class PatchDraft(WireModel):
    expected_revision: Positive
    fields: dict[str, FieldValue]

    @model_validator(mode="after")
    def known_fields(self) -> Self:
        from xiaohe.reception.fields import ROUNDS

        if self.fields.keys() - {
            key for rounds in ROUNDS.values() for group in rounds for key in group
        }:
            raise ValueError("unknown draft field")
        return self


class FileRef(WireModel):
    id: Id
    category: FileCategory
    filename: Annotated[BaseId, Field(max_length=255)]
    media_type: MediaType
    bytes: Annotated[int, Field(strict=True, ge=1, le=33554432)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    status: Literal["pending", "ready", "failed"]

    @model_validator(mode="after")
    def ready_digest(self) -> Self:
        if self.status == "ready" and self.sha256 is None:
            raise ValueError("ready file requires digest")
        return self


class InitFile(WireModel):
    client_upload_id: Id
    category: FileCategory
    filename: Annotated[BaseId, Field(max_length=255)]
    media_type: MediaType
    bytes: Positive
    expected_revision: Positive

    @model_validator(mode="after")
    def bounded_size(self) -> Self:
        from xiaohe.contracts import ServiceError

        if self.bytes > 33554432:
            raise ServiceError("FILE_TOO_LARGE", 413)
        return self


class UploadView(WireModel):
    file: FileRef
    upload_path: str


class DetachFile(WireModel):
    expected_revision: Positive


class OperationalDownload(WireModel):
    application_id: Id
    owner_ai_user_id: Id


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


class SkillContext(WireModel):
    direction: Direction
    fields: dict[str, FieldValue] = Field(default_factory=dict, repr=False)


class KnowledgeRequest(WireModel):
    direction: Direction
    skill_id: Id
    query: Annotated[str, Field(max_length=200)]


class KnowledgeHit(WireModel):
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    source: Annotated[Id, Field(max_length=200)]
    version: Annotated[Id, Field(max_length=100)]
    conditions: Annotated[str, Field(max_length=1000)]


class SkillAnswer(WireModel):
    text: str
    sources: list[KnowledgeHit] = Field(default_factory=list)


class ReceptionActor(WireModel):
    ai_user_id: Id
    system_id: Id
    issuer: Id
    sub: Id
    source_tenant_id: Id


class ApplicationView(WireModel):
    id: Id
    subject_id: Id
    direction: Direction
    revision: Positive
    public_fields: dict[str, FieldValue]
    request_fields: dict[str, FieldValue]
    files: list[FileRef]

    @model_validator(mode="after")
    def valid_profile(self) -> Self:
        _check_formal_fields(self.direction, self.public_fields, self.request_fields, self.files)
        return self


def _check_formal_fields(direction, public, request, files):
    from xiaohe.contracts import ServiceError
    from xiaohe.reception.fields import PUBLIC_FIELDS, missing_required, validate_fields

    if public.keys() - set(PUBLIC_FIELDS[direction]) or request.keys() & set(
        PUBLIC_FIELDS[direction]
    ):
        raise ValueError("invalid public/request field partition")
    try:
        fields = validate_fields(direction, public | request)
    except ServiceError:
        raise ValueError("invalid formal fields") from None
    if missing_required(direction, fields) or any(file.status != "ready" for file in files):
        raise ValueError("incomplete formal submission")


class Submission(WireModel):
    submission_key: Id
    draft_id: Id
    draft_revision: Positive
    operation: Literal["create", "update"]
    application_id: Id | None = None
    expected_application_revision: Positive | None = None
    subject_id: Id | None = None
    subject_kind: Literal["company", "organization"]
    direction: Direction
    public_fields: dict[str, FieldValue]
    request_fields: dict[str, FieldValue]
    files: list[FileRef]

    @model_validator(mode="after")
    def valid_submission(self) -> Self:
        _check_formal_fields(self.direction, self.public_fields, self.request_fields, self.files)
        if (self.direction == "organization") != (self.subject_kind == "organization"):
            raise ValueError("subject kind does not match direction")
        if self.operation == "update" and not (
            self.application_id and self.expected_application_revision
        ):
            raise ValueError("update requires current application revision")
        if self.operation == "create" and (
            self.application_id is not None or self.expected_application_revision is not None
        ):
            raise ValueError("create cannot carry update target")
        return self


class SubmitReceipt(WireModel):
    submission_key: Id
    kind: DraftKind
    application_id: Id
    onboarding_id: Id | None = None
    revision: Positive
    status: Literal["accepted", "pending", "unknown", "failed"]

    @model_validator(mode="after")
    def linked_receipt(self) -> Self:
        if self.kind == "onboarding" and self.status == "accepted" and not self.onboarding_id:
            raise ValueError("onboarding receipt requires onboardingId")
        return self


class SubmissionResult(WireModel):
    status: Literal["not_found", "pending", "accepted"]
    receipt: SubmitReceipt | None

    @model_validator(mode="after")
    def accepted_result(self) -> Self:
        if (self.status == "accepted") != (self.receipt is not None):
            raise ValueError("result and receipt do not match")
        if self.receipt is not None and self.receipt.status != "accepted":
            raise ValueError("result requires accepted receipt")
        return self


class SubjectView(WireModel):
    id: Id
    kind: Literal["company", "organization"]
    name: Annotated[Id, Field(max_length=200)]
    public_fields: dict[str, FieldValue]

    @model_validator(mode="after")
    def valid_subject(self) -> Self:
        from xiaohe.reception.fields import PUBLIC_FIELDS, validate_fields

        allowed = (
            set(PUBLIC_FIELDS["organization"])
            if self.kind == "organization"
            else set(PUBLIC_FIELDS["buyer"]) | set(PUBLIC_FIELDS["supplier"])
        )
        if self.public_fields.keys() - allowed:
            raise ValueError("unknown subject profile field")
        for key, value in self.public_fields.items():
            direction = (
                "organization"
                if self.kind == "organization"
                else "supplier"
                if key == "companyType"
                else "buyer"
            )
            validate_fields(direction, {key: value})
        name_key = "organizationName" if self.kind == "organization" else "companyName"
        if self.public_fields.get(name_key) != self.name:
            raise ValueError("subject name does not match profile")
        return self


class SubjectPage(WireModel):
    items: list[SubjectView]
    next_cursor: Id | None
