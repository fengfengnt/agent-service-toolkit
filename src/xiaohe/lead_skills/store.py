from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.lead_skills.models import FieldPolicy, InteractionPolicy
from xiaohe.models import LeadPolicyRecord
from xiaohe.skill_schema import SkillValidationError


async def persist_policy(db: AsyncSession, policy: FieldPolicy | InteractionPolicy) -> str:
    kind = "fields" if isinstance(policy, FieldPolicy) else "interaction"
    await db.execute(
        insert(LeadPolicyRecord)
        .values(policy_id=policy.policy_id, kind=kind, payload=policy.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=["policy_id"])
    )
    if await load_policy(db, policy.policy_id) != policy:
        raise SkillValidationError("policy content mismatch")
    return policy.policy_id


async def load_policy(db: AsyncSession, policy_id: str) -> FieldPolicy | InteractionPolicy:
    row = await db.get(LeadPolicyRecord, policy_id, populate_existing=True)
    if row is None:
        raise SkillValidationError("bound lead policy is unavailable")
    try:
        cls = {"fields": FieldPolicy, "interaction": InteractionPolicy}[row.kind]
        policy = cls.model_validate(row.payload)
        if policy.policy_id != policy_id:
            raise ValueError("content mismatch")
        return policy
    except (KeyError, ValueError):
        raise SkillValidationError("bound lead policy is corrupt") from None


async def resolve_draft_policy(db, row) -> FieldPolicy:
    if not row.field_policy_id:
        raise SkillValidationError("draft field policy is required")
    policy = await load_policy(db, row.field_policy_id)
    if not isinstance(policy, FieldPolicy) or policy.direction != row.direction:
        raise SkillValidationError("draft policy role mismatch")
    return policy
