from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.models import Run, WorkflowDefinitionRecord
from xiaohe.workflows.compiler import validate_graph
from xiaohe.workflows.models import WorkflowBundle, WorkflowValidationError


def _validate(payload: dict) -> WorkflowBundle:
    try:
        bundle = WorkflowBundle.model_validate(payload)
    except (ValidationError, ValueError):
        raise WorkflowValidationError("workflow snapshot is invalid or unsupported") from None
    for definition in bundle.definitions.values():
        validate_graph(definition)
    return bundle


async def persist_bundle(db: AsyncSession, bundle: WorkflowBundle) -> str:
    bundle = _validate(bundle.model_dump())
    await db.execute(
        insert(WorkflowDefinitionRecord)
        .values(bundle_id=bundle.bundle_id, payload=bundle.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=[WorkflowDefinitionRecord.bundle_id])
    )
    if await load_persisted_bundle(db, bundle.bundle_id) != bundle:
        raise WorkflowValidationError("workflow snapshot is immutable")
    return bundle.bundle_id


async def load_persisted_bundle(db: AsyncSession, bundle_id: str) -> WorkflowBundle:
    payload = await db.scalar(
        select(WorkflowDefinitionRecord.payload).where(
            WorkflowDefinitionRecord.bundle_id == bundle_id
        )
    )
    if payload is None:
        raise WorkflowValidationError("workflow snapshot is unavailable")
    bundle = _validate(payload)
    if bundle.bundle_id != bundle_id:
        raise WorkflowValidationError("workflow snapshot identity mismatch")
    return bundle


async def bind_run_bundle(db: AsyncSession, run_id: UUID, bundle_id: str) -> str:
    run = await db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise WorkflowValidationError("workflow run is unavailable")
    if run.workflow_bundle_id is not None and run.workflow_bundle_id != bundle_id:
        raise WorkflowValidationError("run is bound to another workflow snapshot")
    if run.workflow_bundle_id is None and (run.status != "queued" or run.attempt != 0):
        raise WorkflowValidationError("started legacy run cannot change runtime")
    await load_persisted_bundle(db, bundle_id)
    run.workflow_bundle_id = bundle_id
    run.runtime_version = "dsl-v1"
    await db.flush()
    return bundle_id


async def bundle_for_run(db: AsyncSession, run_id: UUID) -> WorkflowBundle | None:
    run = await db.get(Run, run_id)
    if run is None:
        raise WorkflowValidationError("workflow run is unavailable")
    if run.runtime_version == "legacy" and run.workflow_bundle_id is None:
        return None
    if run.runtime_version != "dsl-v1" or run.workflow_bundle_id is None:
        raise WorkflowValidationError("unsupported run workflow version")
    return await load_persisted_bundle(db, run.workflow_bundle_id)
