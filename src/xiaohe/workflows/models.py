import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from xiaohe.reception.fields import ALLOWED_FIELDS, FIELD_LABELS

AgentId = Literal["xiaohe-reception", "xiaohe-buyer", "xiaohe-organization", "xiaohe-supplier"]
AGENT_IDS: tuple[AgentId, ...] = (
    "xiaohe-reception",
    "xiaohe-buyer",
    "xiaohe-organization",
    "xiaohe-supplier",
)
NodeType = Literal["understand", "decide", "operation", "answer", "handoff", "result"]
OperationName = Literal[
    "collect_details",
    "save_round",
    "show_summary",
    "confirm_submission",
    "request_auth",
    "new_draft",
    "resume_draft",
    "query_progress",
    "record_organization_intent",
    "start_onboarding",
]
RouteEvent = Literal[
    "consultation",
    "provide_details",
    "consultation_with_details",
    "accept_invitation",
    "defer_invitation",
    "save_round",
    "skip_optional",
    "show_summary",
    "confirm_current_summary",
    "resume_draft",
    "new_need",
    "record_intent",
    "start_onboarding",
    "query_progress",
    "switch_direction",
    "unclear",
    "off_topic",
]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class WorkflowValidationError(ValueError):
    pass


class DefinitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RoundDefinition(DefinitionModel):
    id: Literal["round1", "round2", "round3", "files", "summary"]
    name: Label
    fields: list[str] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def supported_fields(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or set(values) - FIELD_LABELS.keys():
            raise ValueError("unsupported or duplicate field")
        return values

    @model_validator(mode="after")
    def collection_round_has_fields(self) -> Self:
        if self.id in ("round1", "round2", "round3") and not self.fields:
            raise ValueError("collection round requires fields")
        if self.id in ("files", "summary") and self.fields:
            raise ValueError("files and summary do not introduce fields")
        return self


class NodeBase(DefinitionModel):
    id: Identifier
    name: Label


class UnderstandNode(NodeBase):
    type: Literal["understand"]
    prompt: Identifier
    next: Identifier


class DecideNode(NodeBase):
    type: Literal["decide"]
    routes: dict[RouteEvent, Identifier] = Field(min_length=1)
    fallback: Identifier


class OperationNode(NodeBase):
    type: Literal["operation"]
    operation: OperationName
    next: Identifier


class AnswerNode(NodeBase):
    type: Literal["answer"]
    prompt: Identifier
    next: Identifier


class HandoffNode(NodeBase):
    type: Literal["handoff"]
    targets: list[AgentId] = Field(min_length=1)


class ResultNode(NodeBase):
    type: Literal["result"]
    wait_for_input: bool


NodeDefinition = Annotated[
    UnderstandNode | DecideNode | OperationNode | AnswerNode | HandoffNode | ResultNode,
    Field(discriminator="type"),
]


class WorkflowDefinition(DefinitionModel):
    schema_version: Literal[1]
    agent_id: AgentId
    name: Label
    workflow_version: Label
    entry: Identifier
    prompts: dict[Identifier, str] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    rounds: list[RoundDefinition] = Field(default_factory=list)
    nodes: list[NodeDefinition] = Field(min_length=1, max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported schema")
        return value

    @model_validator(mode="after")
    def check_references(self) -> Self:
        if len(self.skills) != len(set(self.skills)):
            raise ValueError("duplicate skill")
        fields = [key for group in self.rounds for key in group.fields]
        if len(fields) != len(set(fields)) or len({group.id for group in self.rounds}) != len(
            self.rounds
        ):
            raise ValueError("duplicate round or field")
        if len(self.required_fields) != len(set(self.required_fields)) or set(
            self.required_fields
        ) - set(fields):
            raise ValueError("required fields must belong to rounds")
        if self.agent_id == "xiaohe-reception" and (
            self.rounds or self.required_fields or self.skills
        ):
            raise ValueError("reception cannot collect professional details")
        if self.agent_id != "xiaohe-reception" and set(fields) - set(
            ALLOWED_FIELDS[self.agent_id.removeprefix("xiaohe-")]
        ):
            raise ValueError("field is not supported for this role")
        for node in self.nodes:
            if isinstance(node, (UnderstandNode, AnswerNode)) and node.prompt not in self.prompts:
                raise ValueError("missing prompt reference")
        return self


def bundle_digest(definitions: dict[AgentId, WorkflowDefinition], resources: dict[str, str]) -> str:
    value = {
        "definitions": {key: item.model_dump(mode="json") for key, item in definitions.items()},
        "resources": resources,
    }
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WorkflowBundle(DefinitionModel):
    bundle_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    definitions: dict[AgentId, WorkflowDefinition]
    resources: dict[str, str]

    @model_validator(mode="after")
    def verify_digest(self) -> Self:
        if set(self.definitions) != set(AGENT_IDS):
            raise ValueError("bundle must contain all four agents")
        if any(key != definition.agent_id for key, definition in self.definitions.items()):
            raise ValueError("agent identity mismatch")
        if self.bundle_id != bundle_digest(self.definitions, self.resources):
            raise ValueError("bundle content digest mismatch")
        return self
