from typing import Literal, Self

from pydantic import Field, model_validator

from xiaohe.agents.contracts import AnswerDraft, FieldCandidate
from xiaohe.contracts import WireModel
from xiaohe.lead_skills.models import FieldPolicy, InteractionPolicy, policy_digest
from xiaohe.workflows.models import AgentId, DefinitionModel, Identifier

TOOLS = frozenset(
    {
        "read_lead",
        "prepare_lead",
        "compose_form",
        "save_lead",
        "submit_lead",
        "query_progress",
        "request_handoff",
    }
)
READ_TOOLS = frozenset({"read_lead", "query_progress"})


class SkillSpec(DefinitionModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    path: str
    direction: Literal["general", "buyer", "organization", "supplier"]
    authenticated: bool = False
    requires_tools: list[str] = Field(default_factory=list)
    legacy_id: str | None = None


class ChildBinding(DefinitionModel):
    name: Identifier
    description: str = Field(min_length=1, max_length=1024)
    skills: list[str]
    tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def readonly_tools(self) -> Self:
        if set(self.tools) - READ_TOOLS:
            raise ValueError("child tools must be read only")
        return self


class RuntimeBinding(DefinitionModel):
    agent_id: AgentId
    skills: list[str]
    flow_skill: str | None = None
    tools: list[str]
    children: list[ChildBinding] = Field(default_factory=list)


class RuntimeBundle(DefinitionModel):
    skills: dict[str, SkillSpec]
    bindings: dict[AgentId, RuntimeBinding]
    resources: dict[str, str]
    field_policies: dict[str, FieldPolicy]
    interaction_policy: InteractionPolicy

    @property
    def bundle_id(self) -> str:
        return policy_digest(self)


class DeepExecution(DefinitionModel):
    runtime: Literal["deepagent-v1"] = "deepagent-v1"
    bundle_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    active: AgentId | None = None
    handoffs: int = Field(default=0, ge=0, le=1)
    threads: dict[str, dict] = Field(default_factory=dict)
    effects: dict[str, dict] = Field(default_factory=dict)
    result: dict | None = None


class ChildTask(DefinitionModel):
    text: str = Field(min_length=1, max_length=10000)


class ChildResult(DefinitionModel):
    summary: str = Field(min_length=1, max_length=4000)
    candidates: dict[str, FieldCandidate] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class DeepTurnResult(DefinitionModel):
    answer: AnswerDraft | None = None
    wait_for_input: bool = True


class SubmitIntent(DefinitionModel):
    confirmed: bool
    evidence: str = Field(min_length=1, max_length=10000)
    negated: bool = False
    deferred: bool = False
    conditional: bool = False
    needs_clarification: bool = False


class ToolResult(DefinitionModel):
    operation: str
    status: Literal["ok", "unknown", "auth", "choice", "error"] = "ok"
    revision: int | None = None
    receipt_id: str | None = None
    data: dict = Field(default_factory=dict)
    error_code: str | None = None


class FormGroup(WireModel):
    id: str
    name: str
    fields: list[str]


class FormViewV2(WireModel):
    version: Literal[2] = 2
    scope: Literal["draft", "application"]
    direction: Literal["buyer", "organization", "supplier"]
    field_rule_version: str
    interaction_version: str
    skill_version: str
    form_id: str
    draft_revision: int | None = None
    application_revision: int | None = None
    fields: list[dict]
    groups: list[FormGroup]
    pending: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    notices: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scoped_revision(self) -> Self:
        if self.scope == "draft" and (
            self.draft_revision is None or self.application_revision is not None
        ):
            raise ValueError("draft revision required")
        if self.scope == "application" and (
            self.application_revision is None or self.draft_revision is not None
        ):
            raise ValueError("application revision required")
        return self
