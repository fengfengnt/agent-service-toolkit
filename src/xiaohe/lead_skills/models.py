import hashlib
import json
from string import Formatter
from typing import Literal, Self

from pydantic import Field, model_validator

from xiaohe.reception.fields import ALLOWED_FIELDS
from xiaohe.workflows.models import DefinitionModel, RoundDefinition

Direction = Literal["buyer", "organization", "supplier"]
NOTICE_VARIABLES = {
    "prepare": {"direction"},
    "missing": {"missing"},
    "saved": set(),
    "submitting": set(),
    "unknown": set(),
    "accepted": set(),
    "failed": {"guidance"},
}


def policy_digest(policy) -> str:
    raw = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class FieldSpec(DefinitionModel):
    id: str
    label: str = Field(min_length=1, max_length=200)
    input_type: Literal["text", "textarea", "select", "multiselect"] = "text"
    scope: Literal["public", "request"]
    validator: Literal["generic", "text", "enum", "email", "channels"] = "generic"
    options: list[str] = Field(default_factory=list)
    max_length: int = Field(default=4000, ge=1, le=4000)
    min_length: int = Field(default=0, ge=0, le=4000)
    max_items: int = Field(default=32, ge=1, le=32)
    item_max_length: int = Field(default=256, ge=1, le=256)
    trim: bool = False
    nonblank: bool = False
    length_on_trimmed: bool = False

    @model_validator(mode="after")
    def valid_options(self) -> Self:
        if self.min_length > self.max_length or len(self.options) != len(set(self.options)):
            raise ValueError("invalid field limits")
        if (self.validator in ("enum", "channels")) != bool(self.options):
            raise ValueError("options must match registered validator")
        return self


class FieldPolicy(DefinitionModel):
    version: int = Field(ge=1)
    skill_id: str
    direction: Direction
    fields: list[FieldSpec] = Field(min_length=1, max_length=64)
    rounds: list[RoundDefinition] = Field(min_length=1)
    required_fields: list[str]

    @property
    def policy_id(self) -> str:
        return policy_digest(self)

    @model_validator(mode="after")
    def valid_references(self) -> Self:
        ids = [item.id for item in self.fields]
        grouped = [key for group in self.rounds for key in group.fields]
        if self.skill_id != self.direction + ".lead_capture":
            raise ValueError("role skill mismatch")
        if len(ids) != len(set(ids)) or set(ids) - set(ALLOWED_FIELDS[self.direction]):
            raise ValueError("unsupported role field")
        if set(grouped) != set(ids) or len(grouped) != len(ids):
            raise ValueError("every field must belong to one group")
        if len({r.id for r in self.rounds}) != len(self.rounds):
            raise ValueError("duplicate round")
        if len(set(self.required_fields)) != len(self.required_fields) or set(
            self.required_fields
        ) - set(ids):
            raise ValueError("invalid required field")
        return self


class CommonPolicy(DefinitionModel):
    version: int = Field(ge=1)
    direct_submission: Literal[True]
    notices: dict[str, str]

    @model_validator(mode="after")
    def valid_notices(self) -> Self:
        if self.notices.keys() != NOTICE_VARIABLES.keys():
            raise ValueError("missing or unknown notice")
        for key, text in self.notices.items():
            if not text or len(text) > 1000:
                raise ValueError("invalid notice")
            variables = set()
            for _, name, spec, conversion in Formatter().parse(text):
                if name is not None:
                    if spec or conversion:
                        raise ValueError("notice formatting is not allowed")
                    variables.add(name)
            if variables != NOTICE_VARIABLES[key]:
                raise ValueError("invalid notice variables")
        return self


class InteractionPolicy(CommonPolicy):
    guides: dict[Direction, str]

    @property
    def policy_id(self) -> str:
        return policy_digest(self)

    @model_validator(mode="after")
    def complete_guides(self) -> Self:
        if set(self.guides) != {"buyer", "organization", "supplier"} or not all(
            self.guides.values()
        ):
            raise ValueError("three role guides are required")
        return self


class LeadSkillBundle(DefinitionModel):
    field_policies: dict[Direction, FieldPolicy]
    interaction_policy: InteractionPolicy
