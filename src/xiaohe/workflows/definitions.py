import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from xiaohe.models import WorkflowDefinitionRecord
from xiaohe.workflows.loader import workflow_for
from xiaohe.workflows.models import WorkflowBundle, WorkflowValidationError
from xiaohe.workflows.store import load_persisted_bundle, persist_bundle

RESOURCE_ROOT = Path(__file__).resolve().parents[3] / "resources/xiaohe"
# 固定旧草稿的原采集规则；提示词更新不改变此指纹，也不复制轮次表。
LEGACY_RULES_DIGEST = "de4f318a63a6208df80c995eeeae53cc0a482bc06cc6d56e22d53d07c6bd9d73"


def rules_digest(bundle):
    value = {
        key: {
            "rounds": [item.model_dump() for item in definition.rounds],
            "required_fields": definition.required_fields,
        }
        for key, definition in bundle.definitions.items()
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def baseline_bundle(db):
    rows = (
        await db.scalars(
            select(WorkflowDefinitionRecord).order_by(
                WorkflowDefinitionRecord.created_at, WorkflowDefinitionRecord.bundle_id
            )
        )
    ).all()
    for row in rows:
        bundle = WorkflowBundle.model_validate(row.payload)
        if rules_digest(bundle) == LEGACY_RULES_DIGEST:
            return bundle
    try:
        bundle = WorkflowBundle.model_validate_json(
            (RESOURCE_ROOT / "legacy-bundle-v1.json").read_text("utf-8")
        )
    except ValueError:
        raise WorkflowValidationError(
            "original draft definition unavailable; explicit migration required"
        ) from None
    if rules_digest(bundle) != LEGACY_RULES_DIGEST:
        raise WorkflowValidationError(
            "original draft definition unavailable; explicit migration required"
        )
    await persist_bundle(db, bundle)
    return bundle


async def draft_definition(db, row):
    if row.definition_bundle_id is None:
        bundle = await baseline_bundle(db)
        row.definition_bundle_id = bundle.bundle_id
    else:
        bundle = await load_persisted_bundle(db, row.definition_bundle_id)
    definition = workflow_for(bundle, "xiaohe-" + row.direction)
    if row.state == "submitting":
        return definition
    # 新的受理门槛只投影联系人字段，不改写已持久化的历史流程或提交 payload。
    fields = {key for group in definition.rounds for key in group.fields}
    contacts = [key for key in ("contactName", "contactPhone") if key not in fields]
    required = list(dict.fromkeys(definition.required_fields + ["contactName", "contactPhone"]))
    if not contacts and required == definition.required_fields:
        return definition
    rounds = list(definition.rounds)
    index = next((i for i, group in enumerate(rounds) if group.id == "round2"), 0)
    rounds[index] = rounds[index].model_copy(update={"fields": rounds[index].fields + contacts})
    return definition.model_copy(update={"rounds": rounds, "required_fields": required})


def rounds_for(bundle, direction):
    return tuple(workflow_for(bundle, "xiaohe-" + direction).rounds)
