import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import inspect

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
        required_fields=policy.required_fields,
        notices=context.bundle.interaction_policy.notices,
    )
    progress = row.guidance_progress or {}
    steps = progress.get("steps", {})
    if prompt and prompt not in steps:
        steps = steps | {prompt: {"completed": False}}
    row.guidance_progress = progress | {
        "form": view.model_dump(mode="json", by_alias=True),
        "steps": steps,
    }
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


def _token(scope, *parts):
    return str(
        uuid5(NAMESPACE_URL, json.dumps([scope, *parts], sort_keys=True, ensure_ascii=False))
    )


async def lead_policy_v2(db, actor, draft_id):
    from xiaohe.lead_skills.views import current_interaction_policy
    from xiaohe.reception.store import require_draft

    row = await require_draft(db, actor, draft_id)
    policy = await resolve_draft_policy(db, row)
    progress = row.guidance_progress or {}
    if progress.get("form"):
        view = FormViewV2.model_validate(progress["form"])
        if view.field_rule_version != policy.policy_id:
            raise ServiceError("ACTION_STALE", 409)
        if view.draft_revision != row.revision:
            view = view.model_copy(
                update={
                    "draft_revision": row.revision,
                    "form_id": _token("draft", str(row.id), row.revision, view.form_id),
                }
            )
        return view.model_copy(
            update={
                "pending": [
                    prompt
                    for prompt in view.pending
                    if not progress.get("steps", {}).get(prompt, {}).get("completed")
                ],
                "actions": ["save", "submit"] if row.state in ("collecting", "review") else [],
            }
        )
    interaction = await current_interaction_policy(db, row.conversation_id)
    projected = lead_policy_view(policy, interaction).model_dump(mode="json", by_alias=True)
    groups = [
        dict(id=f"group-{index}", name=group.name, fields=group.fields)
        for index, group in enumerate(policy.rounds, 1)
    ]
    return FormViewV2(
        scope="draft",
        direction=row.direction,
        field_rule_version=policy.policy_id,
        interaction_version=interaction.policy_id,
        skill_version=policy.policy_id,
        form_id=_token(
            "draft", str(row.id), row.revision, policy.policy_id, interaction.policy_id, groups
        ),
        draft_revision=row.revision,
        fields=projected["fields"],
        groups=groups,
        required_fields=policy.required_fields,
        notices=interaction.notices,
        actions=["save", "submit"] if row.state in ("collecting", "review") else [],
    )


async def patch_form(db, actor, draft_id, *, form_id, expected_revision, fields):
    from xiaohe.reception.store import patch_locked_draft, require_draft

    row = await require_draft(db, actor, draft_id, expected_revision)
    view = await lead_policy_v2(db, actor, row.id)
    if form_id != view.form_id:
        raise ServiceError("ACTION_STALE", 409)
    editable = {key for group in view.groups for key in group.fields}
    if fields.keys() - editable:
        raise ServiceError("INPUT_INVALID", 422)
    return await patch_locked_draft(db, row, expected_revision, fields)


async def application_policy_v2(db, actor, view):
    from xiaohe.lead_skills.views import current_interaction_policy
    from xiaohe.reception.forms import authority, form_field_policy, form_scope

    await authority(db, actor)
    policy = form_field_policy(view.direction, expected_bundle_id=view.definition_bundle_id)
    interaction = await current_interaction_policy(db)
    projected = lead_policy_view(policy, interaction).model_dump(mode="json", by_alias=True)
    groups = [
        dict(id=f"group-{index}", name=group.name, fields=group.fields)
        for index, group in enumerate(policy.rounds, 1)
    ]
    return FormViewV2(
        scope="application",
        direction=view.direction,
        field_rule_version=policy.policy_id,
        interaction_version=interaction.policy_id,
        skill_version=view.definition_bundle_id,
        form_id=_token(
            "application",
            form_scope(actor),
            view.model_dump(mode="json", by_alias=True),
            interaction.policy_id,
        ),
        application_revision=view.expected_application_revision or 0,
        fields=projected["fields"],
        groups=groups,
        actions=["submit"],
        required_fields=policy.required_fields,
        notices=interaction.notices,
    )


async def record_published_guidance(db, command, message):
    from xiaohe.models import Message
    from xiaohe.reception.models import ReceptionDraft
    from xiaohe.runs import reply_id

    if (
        not inspect(message).persistent
        or message.role != "assistant"
        or message.id != reply_id(command.run_id, command.id)
        or message.conversation_id != command.conversation_id
        or await db.get(Message, message.id) is not message
    ):
        return
    from xiaohe.graph import visible_text

    published = visible_text(message.parts)
    for effect in command.payload.get("deepExecution", {}).get("effects", {}).values():
        if effect["operation"] != "compose_form":
            continue
        form = effect.get("data", {}).get("form", {})
        # Locate only this command's server-composed form; arbitrary model state is never evidence.
        from sqlalchemy import select

        rows = await db.scalars(
            select(ReceptionDraft).where(ReceptionDraft.conversation_id == command.conversation_id)
        )
        for row in rows:
            progress = row.guidance_progress or {}
            if progress.get("form", {}).get("formId") != form.get("formId"):
                continue
            steps = dict(progress.get("steps", {}))
            for prompt in form.get("pending", []):
                if prompt and prompt in published:
                    steps[prompt] = {
                        "completed": True,
                        "message_id": str(message.id),
                        "command_id": str(command.id),
                    }
            row.guidance_progress = progress | {"steps": steps}
