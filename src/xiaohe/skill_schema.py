from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from xiaohe.reception.fields import FIELD_LABELS

AgentId = Literal["xiaohe-reception", "xiaohe-buyer", "xiaohe-organization", "xiaohe-supplier"]


AGENT_IDS: tuple[AgentId, ...] = (
    "xiaohe-reception",
    "xiaohe-buyer",
    "xiaohe-organization",
    "xiaohe-supplier",
)


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


class SkillValidationError(ValueError):
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
