from pathlib import Path

import yaml
from pydantic import ValidationError

from xiaohe.lead_skills.models import CommonPolicy, FieldPolicy, InteractionPolicy, LeadSkillBundle
from xiaohe.workflows.loader import (
    MAX_DEFINITION_BYTES,
    MAX_RESOURCE_BYTES,
    DefinitionLoader,
    _read_resource,
)
from xiaohe.workflows.models import WorkflowValidationError


def read_lead_resources(resource_root: Path) -> dict[str, str]:
    root = resource_root.resolve()
    directory = (root / "lead-skills").resolve()
    if not directory.is_relative_to(root):
        raise WorkflowValidationError("lead skill directory escapes root")
    paths = ["common/policy.yaml"] + [
        f"{role}/{name}"
        for role in ("buyer", "organization", "supplier")
        for name in ("policy.yaml", "SKILL.md")
    ]
    return {
        "lead-skills/" + path: _read_resource(directory, path, MAX_RESOURCE_BYTES) for path in paths
    }


def parse_lead_skills(resources: dict[str, str]) -> LeadSkillBundle:
    def parse(path):
        text = resources["lead-skills/" + path]
        if len(text.encode()) > MAX_DEFINITION_BYTES:
            raise WorkflowValidationError("lead policy exceeds size limit")
        return yaml.load(text, Loader=DefinitionLoader)

    try:
        common = CommonPolicy.model_validate(parse("common/policy.yaml"))
        fields, guides = {}, {}
        for role in ("buyer", "organization", "supplier"):
            data = parse(role + "/policy.yaml")
            fields[role] = FieldPolicy.model_validate(data)
            if fields[role].direction != role:
                raise ValueError("policy filename and role differ")
            guides[role] = resources[f"lead-skills/{role}/SKILL.md"]
        return LeadSkillBundle(
            field_policies=fields,
            interaction_policy=InteractionPolicy(**common.model_dump(), guides=guides),
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError, ValidationError, RecursionError):
        raise WorkflowValidationError("invalid lead skill configuration") from None


def load_lead_skills(resource_root: Path) -> LeadSkillBundle:
    return parse_lead_skills(read_lead_resources(resource_root))
