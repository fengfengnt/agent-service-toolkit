from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xiaohe.contracts import Actor, InternalModel, WriteFence
from xiaohe.reception.contracts import FieldValue
from xiaohe.workflows.models import AgentId, RouteEvent


class StrictAgentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class VisibleTurn(StrictAgentModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=10000)


class ModelContext(StrictAgentModel):
    text: str = Field(max_length=10000)
    direction: Literal["unknown", "buyer", "organization", "supplier"]
    recent_turns: list[VisibleTurn] = Field(default_factory=list, max_length=20)
    draft_step: Literal["round1", "round2", "round3", "files", "summary"] | None = None
    allowed_fields: list[str] = Field(default_factory=list)
    saved_fields: dict[str, FieldValue] = Field(default_factory=dict)
    summary_visible: bool = False
    eligible_topics: list[str] = Field(default_factory=list)


class AgentFrame(InternalModel):
    model_config = ConfigDict(frozen=True)

    fence: WriteFence
    command_id: UUID
    message_id: UUID
    conversation_id: UUID
    actor: Actor
    agent_id: AgentId
    bundle_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    draft_id: UUID | None = None
    draft_revision: int | None = Field(default=None, ge=1)
    summary_version: int | None = Field(default=None, ge=1)
    pending_request_id: UUID | None = None
    invitation_state: Literal["none", "offered", "deferred", "accepted"] = "none"
    model_context: ModelContext


class FieldCandidate(StrictAgentModel):
    value: FieldValue
    evidence: str = Field(min_length=1, max_length=4000)


class SemanticDecision(StrictAgentModel):
    topics: list[str] = Field(default_factory=list, max_length=8)
    action: RouteEvent
    target_direction: Literal["unknown", "buyer", "organization", "supplier"] | None = None
    explicit_switch: bool = False
    negated: bool = False
    deferred: bool = False
    conditional: bool = False
    needs_clarification: bool = False
    candidates: dict[str, FieldCandidate] = Field(default_factory=dict, max_length=32)
    organization_intent: (
        Literal["solution_discussion", "sample_demo", "cooperation_handbook"] | None
    ) = None
    onboarding_mode: Literal["start", "supplement"] = "start"
    draft_name: str | None = Field(default=None, max_length=200)


class ConfirmationEvidence(InternalModel):
    command_id: UUID
    run_id: UUID
    message_id: UUID
    user_id: str | None
    draft_id: UUID
    revision: int
    summary_version: int
    bundle_id: str
    agent_id: AgentId
    source: Literal["semantic", "button"]

    @classmethod
    def from_frame(cls, frame: AgentFrame, *, source: Literal["semantic", "button"]):
        return cls(
            command_id=frame.command_id,
            run_id=frame.fence.run_id,
            message_id=frame.message_id,
            user_id=frame.actor.user_id,
            draft_id=frame.draft_id,
            revision=frame.draft_revision,
            summary_version=frame.summary_version,
            bundle_id=frame.bundle_id,
            agent_id=frame.agent_id,
            source=source,
        )


class AnswerDraft(StrictAgentModel):
    text: str = Field(min_length=1, max_length=4000)
    basis: Literal["general_advice", "confirmed_material", "needs_confirmation"]
    source_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def grounded_sources(self) -> Self:
        if self.basis == "confirmed_material" and not self.source_ids:
            raise ValueError("confirmed material requires a source")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate source")
        return self
