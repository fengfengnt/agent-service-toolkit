from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.lead_skills.models import FieldPolicy, InteractionPolicy
from xiaohe.models import LeadPolicyRecord
from xiaohe.workflows.models import WorkflowValidationError


async def persist_policy(db: AsyncSession, policy: FieldPolicy | InteractionPolicy) -> str:
    kind = "fields" if isinstance(policy, FieldPolicy) else "interaction"
    await db.execute(
        insert(LeadPolicyRecord)
        .values(policy_id=policy.policy_id, kind=kind, payload=policy.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=["policy_id"])
    )
    if await load_policy(db, policy.policy_id) != policy:
        raise WorkflowValidationError("policy content mismatch")
    return policy.policy_id


async def load_policy(db: AsyncSession, policy_id: str) -> FieldPolicy | InteractionPolicy:
    row = await db.get(LeadPolicyRecord, policy_id, populate_existing=True)
    if row is None:
        raise WorkflowValidationError("bound lead policy is unavailable")
    try:
        cls = {"fields": FieldPolicy, "interaction": InteractionPolicy}[row.kind]
        policy = cls.model_validate(row.payload)
        if policy.policy_id != policy_id:
            raise ValueError("content mismatch")
        return policy
    except (KeyError, ValueError):
        raise WorkflowValidationError("bound lead policy is corrupt") from None


async def resolve_draft_policy(db, row) -> FieldPolicy:
    if row.field_policy_id:
        policy = await load_policy(db, row.field_policy_id)
        if not isinstance(policy, FieldPolicy) or policy.direction != row.direction:
            raise WorkflowValidationError("draft policy role mismatch")
        return policy
    from xiaohe.lead_skills.loader import load_lead_skills, parse_lead_skills
    from xiaohe.workflows.definitions import RESOURCE_ROOT, legacy_draft_definition
    from xiaohe.workflows.store import load_persisted_bundle

    definition = await legacy_draft_definition(db, row)
    bundle = await load_persisted_bundle(db, row.definition_bundle_id)
    resources = bundle.resources
    profiles = (
        parse_lead_skills(resources)
        if "lead-skills/common/policy.yaml" in resources
        else load_lead_skills(RESOURCE_ROOT)
    ).field_policies
    base = profiles[row.direction]
    policy = FieldPolicy(
        version=base.version,
        skill_id=base.skill_id,
        direction=row.direction,
        fields=base.fields,
        rounds=definition.rounds,
        required_fields=definition.required_fields,
    )
    # 已出站的旧动作不因读取补绑规则；它沿原提交核对。
    if row.state != "submitting":
        row.field_policy_id = await persist_policy(db, policy)
    return policy


async def bind_command_policy(db, command, latest: InteractionPolicy, *, workflow_bundle_id: str):
    if command.interaction_policy_id:
        policy = await load_policy(db, command.interaction_policy_id)
        if not isinstance(policy, InteractionPolicy):
            raise WorkflowValidationError("command policy kind mismatch")
        return policy
    command.interaction_policy_id = await persist_policy(db, latest)
    command.payload = command.payload | {"leadWorkflowBundleId": workflow_bundle_id}
    return latest


def command_bundle_id(run, command):
    if command.interaction_policy_id:
        value = command.payload.get("leadWorkflowBundleId")
        if not isinstance(value, str):
            raise WorkflowValidationError("command workflow binding missing")
        return value
    return run.workflow_bundle_id
