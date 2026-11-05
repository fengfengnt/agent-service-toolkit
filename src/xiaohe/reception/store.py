import hashlib
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import Actor, ServiceError
from xiaohe.conversations import require_conversation
from xiaohe.models import Conversation
from xiaohe.reception.contracts import CreateDraft, DraftRef, DraftView, FileRef, ReceptionView
from xiaohe.reception.fields import PUBLIC_FIELDS, missing_required, validate_fields
from xiaohe.reception.models import (
    ReceptionDraft,
    ReceptionFile,
    ReceptionFlow,
    ReceptionSubmission,
)


def _uuid(value: str | UUID) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ServiceError("INPUT_INVALID", 422) from None


async def flow_record(db: AsyncSession, actor: Actor, conversation_id: UUID) -> ReceptionFlow:
    conversation = await require_conversation(db, actor, conversation_id)
    return await locked_flow(db, conversation)


async def locked_flow(db, conversation) -> ReceptionFlow:
    row = await db.get(ReceptionFlow, conversation.id)
    if row is None:
        row = ReceptionFlow(
            conversation_id=conversation.id,
            system_id=conversation.system_id,
            tenant_id=conversation.tenant_id,
        )
        db.add(row)
        await db.flush()
    return row


async def require_draft(
    db: AsyncSession, actor: Actor, draft_id: str | UUID, expected_revision: int | None = None
) -> ReceptionDraft:
    # Read only the locator before authorization; lock order stays session -> conversation -> draft.
    conversation_id = await db.scalar(
        select(ReceptionDraft.conversation_id).where(ReceptionDraft.id == _uuid(draft_id))
    )
    if conversation_id is None:
        raise ServiceError("FORBIDDEN", 403)
    await require_conversation(db, actor, conversation_id)
    row = await db.scalar(
        select(ReceptionDraft)
        .where(ReceptionDraft.id == _uuid(draft_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if expected_revision is not None and row.revision != expected_revision:
        raise ServiceError("DRAFT_CONFLICT", 409)
    return row


async def draft_view(db: AsyncSession, row: ReceptionDraft) -> DraftView:
    files = (
        await db.scalars(
            select(ReceptionFile)
            .where(ReceptionFile.draft_id == row.id, ReceptionFile.detached.is_(False))
            .order_by(ReceptionFile.created_at, ReceptionFile.id)
        )
    ).all()
    return DraftView(
        id=str(row.id),
        kind=row.kind,
        direction=row.direction,
        subject_id=row.subject_id,
        source_application_id=row.source_application_id,
        source_onboarding_id=row.source_onboarding_id,
        revision=row.revision,
        step=row.step,
        state=row.state,
        fields=row.fields,
        missing_required=missing_required(row.direction, row.fields),
        summary_version=row.summary_version,
        files=[FileRef.model_validate(file) for file in row.source_files]
        + [
            FileRef(
                id=str(file.id),
                category=file.category,
                filename=file.filename,
                media_type=file.media_type,
                bytes=file.bytes,
                sha256=file.sha256,
                status=file.status,
            )
            for file in files
        ],
    )


async def create_draft(
    db: AsyncSession, actor: Actor, conversation_id: UUID, request: CreateDraft, *, source=None
) -> DraftView:
    flow = await flow_record(db, actor, conversation_id)
    existing = await db.scalar(
        select(ReceptionDraft).where(
            ReceptionDraft.conversation_id == conversation_id,
            ReceptionDraft.client_draft_id == request.client_draft_id,
        )
    )
    if existing is not None:
        if existing.creation_request != request.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ):
            raise ServiceError("DRAFT_CONFLICT", 409)
        return await draft_view(db, existing)
    if (request.subject_id or request.source_application_id) and source is None:
        # Linked formal data must first be loaded and authorized by the business-client path.
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    row = ReceptionDraft(
        conversation_id=conversation_id,
        system_id=actor.system.system_id,
        tenant_id=actor.system.tenant_id,
        creation_request=request.model_dump(mode="json", by_alias=True, exclude_none=True),
        **request.model_dump(),
    )
    if source is not None:
        if request.source_application_id:
            if (
                source.id != request.source_application_id
                or source.direction != request.direction
                or request.subject_id is not None
                and source.subject_id != request.subject_id
            ):
                raise ServiceError("FORBIDDEN", 403)
            row.subject_id = source.subject_id
            row.source_revision = (
                source.revision
                if request.kind == "service" or request.source_onboarding_id
                else None
            )
            row.fields = source.public_fields | source.request_fields
            row.source_files = [
                file.model_dump(mode="json", by_alias=True) for file in source.files
            ]
        else:
            kind = "organization" if request.direction == "organization" else "company"
            if source.id != request.subject_id or source.kind != kind:
                raise ServiceError("FORBIDDEN", 403)
            row.fields = {
                key: value
                for key, value in source.public_fields.items()
                if key in PUBLIC_FIELDS[request.direction]
            }
        row.fields = validate_fields(request.direction, row.fields)
    db.add(row)
    await db.flush()
    conversation = await db.get(Conversation, conversation_id)
    conversation.direction = request.direction
    flow.active_draft_id = row.id
    return await draft_view(db, row)


async def patch_draft(
    db: AsyncSession, actor: Actor, draft_id: str | UUID, expected_revision: int, fields: dict
) -> DraftView:
    row = await require_draft(db, actor, draft_id, expected_revision)
    return await patch_locked_draft(db, row, expected_revision, fields)


async def patch_locked_draft(db, row, expected_revision, fields) -> DraftView:
    if row.revision != expected_revision:
        raise ServiceError("DRAFT_CONFLICT", 409)
    if row.state in ("submitting", "accepted"):
        raise ServiceError("DRAFT_CONFLICT", 409)
    merged = row.fields | validate_fields(row.direction, fields)
    if merged != row.fields:
        row.fields = merged
        row.revision += 1
        row.summary_version = None
        row.state = "collecting"
        await db.flush()
    return await draft_view(db, row)


async def load_reception(db: AsyncSession, actor: Actor, conversation_id: UUID) -> ReceptionView:
    flow = await flow_record(db, actor, conversation_id)
    conversation = await require_conversation(db, actor, conversation_id)
    rows = (
        await db.scalars(
            select(ReceptionDraft)
            .where(ReceptionDraft.conversation_id == conversation_id)
            .order_by(ReceptionDraft.created_at, ReceptionDraft.id)
        )
    ).all()
    active = next((row for row in rows if row.id == flow.active_draft_id), None)
    refs = [
        DraftRef(
            id=str(row.id),
            kind=row.kind,
            direction=row.direction,
            subject_name=row.fields.get("companyName") or row.fields.get("organizationName") or "",
            summary=str(
                row.fields.get("requirement")
                or row.fields.get("aiNeeds")
                or row.fields.get("productDescription")
                or ""
            )[:4000],
            step=row.step,
            revision=row.revision,
            state=row.state,
        )
        for row in rows
    ]
    return ReceptionView(
        conversation_id=str(conversation_id),
        direction=conversation.direction if conversation.direction != "unknown" else None,
        invitation_state=flow.invitation_state,
        pending_request_id=str(flow.pending_request_id) if flow.pending_request_id else None,
        draft_refs=refs,
        active_draft=await draft_view(db, active) if active else None,
    )


async def merge_reception(db, source, target):
    drafts = (
        await db.scalars(select(ReceptionDraft).where(ReceptionDraft.conversation_id == source.id))
    ).all()
    for draft in drafts:
        if await db.scalar(
            select(ReceptionDraft.id).where(
                ReceptionDraft.conversation_id == target.id,
                ReceptionDraft.client_draft_id == draft.client_draft_id,
            )
        ):
            draft.client_draft_id = (
                "claim:"
                + hashlib.sha256((str(source.id) + draft.client_draft_id).encode()).hexdigest()
            )
            draft.creation_request = draft.creation_request | {
                "clientDraftId": draft.client_draft_id
            }
        draft.conversation_id = target.id
    for model in (ReceptionFile, ReceptionSubmission):
        await db.execute(
            update(model)
            .where(model.conversation_id == source.id)
            .values(conversation_id=target.id)
        )
    source_flow = await db.get(ReceptionFlow, source.id)
    if source_flow is not None:
        target_flow = await locked_flow(db, target)
        target_flow.invitation_state = source_flow.invitation_state
        target_flow.pending_request_id = None
        remaining = (
            await db.scalars(
                select(ReceptionDraft).where(
                    ReceptionDraft.conversation_id == target.id,
                    ReceptionDraft.direction == target.direction,
                    ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
                )
            )
        ).all()
        target_flow.active_draft_id = remaining[0].id if len(remaining) == 1 else None
        await db.delete(source_flow)
