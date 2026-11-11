import json
from uuid import uuid5

from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime.models import FormViewV2
from xiaohe.lead_skills.store import resolve_draft_policy
from xiaohe.lead_skills.views import lead_policy_view


async def compose_locked_form(db, context, row, field_groups, prompt):
    policy = await resolve_draft_policy(db, row)
    allowed = {key for group in policy.rounds for key in group.fields}
    flattened = [key for group in field_groups for key in group]
    if (
        not field_groups
        or not all(field_groups)
        or len(flattened) != len(set(flattened))
        or set(flattened) - allowed
        or len(prompt) > 2000
    ):
        raise ServiceError("INPUT_INVALID", 422)
    token = json.dumps(
        [str(row.id), row.revision, context.bundle.bundle_id, field_groups, prompt],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    view = FormViewV2(
        scope="draft",
        direction=row.direction,
        field_rule_version=policy.policy_id,
        interaction_version=context.bundle.interaction_policy.policy_id,
        skill_version=context.bundle.bundle_id,
        form_id=str(uuid5(context.command_id, token)),
        draft_revision=row.revision,
        fields=lead_policy_view(policy, context.bundle.interaction_policy).model_dump(
            mode="json", by_alias=True
        )["fields"],
        groups=[
            dict(id=f"group-{index}", name=f"资料 {index}", fields=fields)
            for index, fields in enumerate(field_groups, 1)
        ],
        pending=[prompt],
        actions=["save", "submit"],
    )
    progress = row.guidance_progress or {}
    row.guidance_progress = progress | {"form": view.model_dump(mode="json", by_alias=True)}
    return view


async def compose_form(context, field_groups: list[list[str]], *, prompt: str) -> FormViewV2:
    from xiaohe.deep_runtime.tools import execute_effect

    result = await execute_effect(
        context,
        "compose_form",
        draft_id=None,
        expected_revision=None,
        payload={"field_groups": field_groups, "prompt": prompt},
    )
    return FormViewV2.model_validate(result.data["form"])
