from sqlalchemy import select

from xiaohe.lead_skills.loader import load_lead_skills
from xiaohe.lead_skills.models import FieldPolicy, InteractionPolicy
from xiaohe.lead_skills.store import load_policy
from xiaohe.models import Command
from xiaohe.reception.contracts import LeadPolicyView
from xiaohe.workflows.definitions import RESOURCE_ROOT
from xiaohe.workflows.models import WorkflowValidationError


def supports_lead_policy(request) -> bool:
    return request.headers.getlist("X-Xiaohe-Lead-Policy") == ["1"]


def supports_lead_policy_v2(request) -> bool:
    return request.headers.getlist("X-Xiaohe-Lead-Policy") == ["2"]


async def current_interaction_policy(db, conversation_id=None) -> InteractionPolicy:
    policy_id = None
    if conversation_id is not None:
        policy_id = await db.scalar(
            select(Command.interaction_policy_id).where(
                Command.conversation_id == conversation_id, Command.completed_at.is_(None)
            )
        )
    policy_id = policy_id or db.info.get("lead_interaction_policy_id")
    if policy_id:
        policy = await load_policy(db, policy_id)
        if not isinstance(policy, InteractionPolicy):
            raise WorkflowValidationError("interaction projection policy mismatch")
        return policy
    return load_lead_skills(RESOURCE_ROOT).interaction_policy


def lead_policy_view(policy: FieldPolicy, interaction: InteractionPolicy) -> LeadPolicyView:
    visible = {key for group in policy.rounds for key in group.fields}
    return LeadPolicyView(
        direction=policy.direction,
        field_rule_version=policy.policy_id,
        interaction_version=interaction.policy_id,
        fields=[
            dict(
                id=field.id,
                label=field.label,
                input_type=field.input_type,
                validator=field.validator,
                options=field.options,
                limits={
                    "maxLength": field.max_length,
                    "minLength": field.min_length,
                    "maxItems": field.max_items,
                    "itemMaxLength": field.item_max_length,
                    "trim": field.trim,
                    "nonblank": field.nonblank,
                    "lengthOnTrimmed": field.length_on_trimmed,
                },
            )
            for field in policy.fields
            if field.id in visible
        ],
        rounds=[r.model_dump() for r in policy.rounds],
        required_fields=policy.required_fields,
        notices=interaction.notices,
    )
