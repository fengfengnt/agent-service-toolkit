import json
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent

from xiaohe.workflows.models import (
    AGENT_IDS,
    AgentId,
    WorkflowBundle,
    WorkflowDefinition,
    WorkflowValidationError,
    bundle_digest,
)

MAX_DEFINITION_BYTES = 256 * 1024
MAX_RESOURCE_BYTES = 1024 * 1024


class DefinitionLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise WorkflowValidationError("workflow aliases are not allowed")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise WorkflowValidationError("workflow keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_workflow(text: str) -> WorkflowDefinition:
    if len(text.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise WorkflowValidationError("workflow exceeds size limit")
    try:
        return WorkflowDefinition.model_validate(yaml.load(text, Loader=DefinitionLoader))
    except (yaml.YAMLError, ValidationError, RecursionError, ValueError) as error:
        if isinstance(error, WorkflowValidationError):
            raise
        raise WorkflowValidationError("invalid workflow definition") from None


def _read_resource(directory: Path, reference: str, limit: int) -> str:
    relative = PurePosixPath(reference)
    if (
        not reference
        or "\\" in reference
        or ":" in reference
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise WorkflowValidationError("invalid resource reference")
    target = (directory / reference).resolve()
    if not target.is_relative_to(directory.resolve()):
        raise WorkflowValidationError("resource escapes allowed directory")
    try:
        with target.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise WorkflowValidationError("resource exceeds size limit")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise WorkflowValidationError("resource unavailable") from None


def load_bundle(resource_root: Path) -> WorkflowBundle:
    resource_root = resource_root.resolve()
    for name in ("workflows", "prompts", "skills"):
        if not (resource_root / name).resolve().is_relative_to(resource_root):
            raise WorkflowValidationError("resource directory escapes allowed root")
    definitions = {}
    resources = {}
    catalog = None
    for agent_id in AGENT_IDS:
        stem = agent_id.removeprefix("xiaohe-")
        text = _read_resource(resource_root / "workflows", stem + ".yaml", MAX_DEFINITION_BYTES)
        definition = parse_workflow(text)
        if definition.agent_id != agent_id:
            raise WorkflowValidationError("workflow filename and agent identity differ")
        definitions[agent_id] = definition
        for reference in definition.prompts.values():
            resources["prompts/" + reference] = _read_resource(
                resource_root / "prompts", reference, MAX_RESOURCE_BYTES
            )
        if definition.skills and catalog is None:
            try:
                from xiaohe.reception.skills import SkillDefinition

                entries = json.loads(
                    _read_resource(resource_root / "skills", "catalog.json", MAX_RESOURCE_BYTES)
                )
                catalog = {}
                for entry in entries:
                    item = SkillDefinition.model_validate(entry)
                    if item.id in catalog:
                        raise WorkflowValidationError("duplicate skill catalog entry")
                    catalog[item.id] = item
            except (ValueError, TypeError, ValidationError):
                raise WorkflowValidationError("invalid skill catalog") from None
        for skill_id in definition.skills:
            item = catalog.get(skill_id)
            if item is None or item.direction != stem:
                raise WorkflowValidationError("skill is not allowed for this agent")
            resources["skills/" + item.path] = _read_resource(
                resource_root / "skills", item.path, MAX_RESOURCE_BYTES
            )
    return WorkflowBundle(
        bundle_id=bundle_digest(definitions, resources),
        definitions=definitions,
        resources=resources,
    )


def workflow_for(bundle: WorkflowBundle, agent_id: AgentId) -> WorkflowDefinition:
    try:
        return bundle.definitions[agent_id]
    except KeyError:
        raise WorkflowValidationError("unknown agent") from None
