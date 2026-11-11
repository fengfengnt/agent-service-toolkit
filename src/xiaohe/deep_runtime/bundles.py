import re
from pathlib import Path, PurePosixPath

import yaml
from deepagents.backends.utils import create_file_data
from pydantic import ValidationError

from xiaohe.contracts import Actor
from xiaohe.deep_runtime.models import (
    TOOLS,
    DeepExecution,
    RuntimeBinding,
    RuntimeBundle,
    SkillSpec,
)
from xiaohe.lead_skills.models import CommonPolicy, FieldPolicy, InteractionPolicy
from xiaohe.workflows.loader import MAX_DEFINITION_BYTES, MAX_RESOURCE_BYTES, DefinitionLoader
from xiaohe.workflows.models import AGENT_IDS, AgentId, WorkflowValidationError


def _read(root: Path, reference: str) -> str:
    relative = PurePosixPath(reference)
    if (
        not reference
        or "\\" in reference
        or ":" in reference
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise WorkflowValidationError("invalid runtime resource reference")
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise WorkflowValidationError("runtime resource escapes root")
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_RESOURCE_BYTES + 1)
        if len(raw) > MAX_RESOURCE_BYTES:
            raise WorkflowValidationError("runtime resource exceeds size limit")
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        raise WorkflowValidationError("runtime resource unavailable") from None


def _yaml(text: str):
    if len(text.encode()) > MAX_DEFINITION_BYTES:
        raise WorkflowValidationError("runtime definition exceeds size limit")
    return yaml.load(text, Loader=DefinitionLoader)


def _validate_binding(bundle: RuntimeBundle, binding: RuntimeBinding) -> None:
    role = binding.agent_id.removeprefix("xiaohe-")
    if len(binding.skills) != len(set(binding.skills)) or set(binding.tools) - TOOLS:
        raise ValueError("invalid runtime binding")
    if binding.flow_skill is not None and binding.flow_skill not in binding.skills:
        raise ValueError("flow skill must be bound")
    if role == "reception" and (binding.flow_skill or set(binding.tools) - {"request_handoff"}):
        raise ValueError("reception cannot write professional data")
    flows = []
    for name in binding.skills:
        skill = bundle.skills[name]
        if skill.direction not in ("general", role) or set(skill.requires_tools) - set(
            binding.tools
        ):
            raise ValueError("skill is not allowed for this binding")
        if skill.legacy_id and skill.legacy_id.endswith(".lead_capture"):
            flows.append(name)
    if flows != ([binding.flow_skill] if binding.flow_skill else []):
        raise ValueError("conflicting flow skills")
    if len({child.name for child in binding.children}) != len(binding.children):
        raise ValueError("duplicate child names")
    for child in binding.children:
        if set(child.tools) - set(binding.tools):
            raise ValueError("child cannot gain parent tools")
        child_binding = RuntimeBinding(
            agent_id=binding.agent_id, skills=child.skills, tools=child.tools
        )
        _validate_binding(bundle, child_binding)


def load_runtime_bundle(resource_root: Path) -> RuntimeBundle:
    root = resource_root.resolve()
    resources = {"deep-runtime.yaml": _read(root, "deep-runtime.yaml")}
    try:
        config = _yaml(resources["deep-runtime.yaml"])
        if (
            set(config) != {"version", "skills", "agents", "field_policies", "common_policy"}
            or type(config["version"]) is not int
            or config["version"] != 1
        ):
            raise ValueError("invalid runtime manifest")
        skills = {}
        for name, definition in config["skills"].items():
            directory = PurePosixPath(definition["path"])
            text = _read(root, str(directory / "SKILL.md"))
            match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
            if match is None:
                raise ValueError("skill requires standard frontmatter")
            metadata = _yaml(match[1])
            spec = SkillSpec(
                name=metadata["name"], description=metadata["description"], **definition
            )
            if spec.name != name or directory.name != name or set(spec.requires_tools) - TOOLS:
                raise ValueError("skill name or dependency unavailable")
            package_root = (root / str(directory)).resolve()
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    reference = str(directory / path.relative_to(package_root).as_posix())
                    if not path.resolve().is_relative_to(package_root):
                        raise ValueError("skill reference escapes package")
                    resources[reference] = _read(root, reference)
            for reference in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text[match.end() :]):
                if "://" in reference or reference.startswith("#"):
                    continue
                relative = PurePosixPath(reference.split("#", 1)[0])
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or str(directory / relative) not in resources
                ):
                    raise ValueError("skill reference unavailable")
            skills[name] = spec
        fields = {}
        guides = {}
        for role, reference in config["field_policies"].items():
            resources[reference] = _read(root, reference)
            fields[role] = FieldPolicy.model_validate(_yaml(resources[reference]))
            if fields[role].direction != role:
                raise ValueError("field policy role mismatch")
            guides[role] = resources[f"skills/{role}-lead-capture/SKILL.md"]
        common_path = config["common_policy"]
        resources[common_path] = _read(root, common_path)
        common = CommonPolicy.model_validate(_yaml(resources[common_path]))
        bindings = {
            key: RuntimeBinding(agent_id=key, **value) for key, value in config["agents"].items()
        }
        if set(bindings) != set(AGENT_IDS) or set(fields) != {"buyer", "organization", "supplier"}:
            raise ValueError("missing role binding")
        bundle = RuntimeBundle(
            skills=skills,
            bindings=bindings,
            resources=resources,
            field_policies=fields,
            interaction_policy=InteractionPolicy(**common.model_dump(), guides=guides),
        )
        for binding in bindings.values():
            _validate_binding(bundle, binding)
        return bundle
    except (KeyError, TypeError, ValueError, ValidationError, yaml.YAMLError, RecursionError):
        raise WorkflowValidationError(
            "invalid or unavailable runtime skill configuration"
        ) from None


def select_binding(bundle: RuntimeBundle, actor: Actor, agent_id: AgentId) -> RuntimeBinding:
    binding = bundle.bindings[agent_id]
    skills = [
        name for name in binding.skills if actor.user_id or not bundle.skills[name].authenticated
    ]
    tools = (
        binding.tools
        if actor.user_id
        else [name for name in binding.tools if name in {"prepare_lead", "request_handoff"}]
    )
    children = [
        child.model_copy(
            update={
                "skills": [
                    name
                    for name in child.skills
                    if actor.user_id or not bundle.skills[name].authenticated
                ],
                "tools": [name for name in child.tools if name in tools],
            }
        )
        for child in binding.children
    ]
    return binding.model_copy(
        update={
            "skills": skills,
            "tools": tools,
            "children": children,
            "flow_skill": binding.flow_skill if binding.flow_skill in skills else None,
        }
    )


def skill_files(bundle: RuntimeBundle, binding: RuntimeBinding) -> dict[str, dict]:
    result = {}
    for name in binding.skills:
        spec = bundle.skills[name]
        prefix = spec.path + "/"
        for path, text in bundle.resources.items():
            if path.startswith(prefix):
                result[f"/skills/{name}/" + path.removeprefix(prefix)] = create_file_data(text)
    return result


async def persist_runtime_bundle(db, bundle: RuntimeBundle) -> str:
    from sqlalchemy.dialects.postgresql import insert

    from xiaohe.lead_skills.store import persist_policy
    from xiaohe.models import DeepRuntimeBundleRecord

    for policy in bundle.field_policies.values():
        await persist_policy(db, policy)
    await persist_policy(db, bundle.interaction_policy)
    await db.execute(
        insert(DeepRuntimeBundleRecord)
        .values(bundle_id=bundle.bundle_id, payload=bundle.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=["bundle_id"])
    )
    if await load_runtime_bundle_record(db, bundle.bundle_id) != bundle:
        raise WorkflowValidationError("native snapshot content mismatch")
    return bundle.bundle_id


async def load_runtime_bundle_record(db, bundle_id: str) -> RuntimeBundle:
    from xiaohe.models import DeepRuntimeBundleRecord

    row = await db.get(DeepRuntimeBundleRecord, bundle_id, populate_existing=True)
    if row is None:
        raise WorkflowValidationError("bound native snapshot unavailable")
    try:
        bundle = RuntimeBundle.model_validate(row.payload)
        if bundle.bundle_id != bundle_id:
            raise ValueError("content mismatch")
        return bundle
    except (TypeError, ValueError):
        raise WorkflowValidationError("bound native snapshot corrupt") from None


async def bind_deep_command(db, run, command, bundle: RuntimeBundle) -> DeepExecution:
    from xiaohe.lead_skills.store import persist_policy

    if previous := command.payload.get("deepExecution"):
        execution = DeepExecution.model_validate(previous)
        await load_runtime_bundle_record(db, execution.bundle_id)
        return execution
    if command.attempts or command.completed_at is not None:
        raise WorkflowValidationError("cannot switch started command runtime")
    await persist_runtime_bundle(db, bundle)
    execution = DeepExecution(bundle_id=bundle.bundle_id)
    command.interaction_policy_id = await persist_policy(db, bundle.interaction_policy)
    command.payload = command.payload | {"deepExecution": execution.model_dump(mode="json")}
    return execution
