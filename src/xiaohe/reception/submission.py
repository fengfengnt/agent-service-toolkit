from uuid import uuid5

from sqlalchemy import select

from xiaohe.contracts import ServiceError
from xiaohe.models import Command, Message, Run, User
from xiaohe.reception.contracts import (
    OnboardingSubmission,
    ReceptionActor,
    Submission,
    SubmitReceipt,
)
from xiaohe.reception.fields import PUBLIC_FIELDS, missing_required
from xiaohe.reception.models import ReceptionDraft, ReceptionFile, ReceptionSubmission
from xiaohe.reception.store import create_draft, require_draft
from xiaohe.runs import require_write


def actor_from_user(user: User) -> ReceptionActor:
    return ReceptionActor(
        ai_user_id=user.user_id,
        system_id=user.system_id,
        issuer=user.issuer,
        sub=user.sub,
        source_tenant_id=str(user.source_tenant_id),
    )


async def confirm_draft(
    db, actor, draft_id, expected_revision, summary_version, confirmation_source
):
    row = await require_draft(db, actor, draft_id, expected_revision)
    return await confirm_locked_draft(
        db, row, expected_revision, summary_version, confirmation_source, actor.user_id
    )


async def confirm_locked_draft(
    db, row, expected_revision, summary_version, message_id, user_id, *, evidence=None
):
    from xiaohe.reception.flow import _explicit_confirmation

    if user_id is None:
        raise ServiceError("AUTH_REQUIRED", 401)
    command = await db.scalar(
        select(Command).where(
            Command.message_id == message_id,
            Command.conversation_id == row.conversation_id,
            Command.completed_at.is_(None),
        )
    )
    run = await db.get(Run, command.run_id) if command else None
    if run is not None and run.runtime_version == "dsl-v1":
        from xiaohe.agents.nodes import validate_confirmation_evidence

        validate_confirmation_evidence(evidence, row, command, run, message_id, user_id)
    elif evidence is not None:
        raise ServiceError("INPUT_INVALID", 422)
    existing = await db.scalar(
        select(ReceptionSubmission).where(
            ReceptionSubmission.draft_id == row.id,
            ReceptionSubmission.draft_revision == expected_revision,
        )
    )
    if existing is not None:
        if existing.summary_version != summary_version:
            raise ServiceError("DRAFT_CONFLICT", 409)
        return existing
    if (
        row.revision != expected_revision
        or row.state != "review"
        or row.summary_version != summary_version
        or summary_version != row.revision
    ):
        raise ServiceError("DRAFT_CONFLICT", 409)
    message = await db.get(Message, message_id)
    command = await db.scalar(
        select(Command).where(
            Command.message_id == message_id,
            Command.conversation_id == row.conversation_id,
            Command.completed_at.is_(None),
        )
    )
    run = await db.get(Run, command.run_id) if command else None
    if (
        message is None
        or message.role != "user"
        or message.conversation_id != row.conversation_id
        or run is None
        or run.user_id != user_id
    ):
        raise ServiceError("FORBIDDEN", 403)
    text = "\n".join(part["text"] for part in message.parts if part["type"] == "text")
    action = command.payload.get("reception", {})
    explicit_action = (
        action.get("action") == "confirm"
        and action.get("draftId") == str(row.id)
        and action.get("expectedRevision") == row.revision
        and action.get("summaryVersion") == summary_version
    )
    if run.runtime_version != "dsl-v1" and not (explicit_action or _explicit_confirmation(text)):
        raise ServiceError("INPUT_INVALID", 422)
    if missing_required(row.direction, row.fields):
        raise ServiceError("INPUT_INVALID", 422)
    files = (
        await db.scalars(
            select(ReceptionFile).where(
                ReceptionFile.draft_id == row.id,
                ReceptionFile.detached.is_(False),
                ReceptionFile.status == "ready",
            )
        )
    ).all()
    from xiaohe.reception.contracts import FileRef

    refs = [FileRef.model_validate(file) for file in row.source_files] + [
        FileRef(
            id=str(file.id),
            category=file.category,
            filename=file.filename,
            media_type=file.media_type,
            bytes=file.bytes,
            sha256=file.sha256,
            status="ready",
        )
        for file in files
    ]
    key = str(uuid5(row.id, f"{row.kind}:{row.revision}"))
    public = {
        key: value for key, value in row.fields.items() if key in PUBLIC_FIELDS[row.direction]
    }
    private = {
        key: value for key, value in row.fields.items() if key not in PUBLIC_FIELDS[row.direction]
    }
    shared = dict(
        submission_key=key,
        draft_id=str(row.id),
        draft_revision=row.revision,
        public_fields=public,
        request_fields=private,
        files=refs,
    )
    if row.kind == "onboarding":
        payload = OnboardingSubmission(
            **shared,
            application_id=row.source_application_id,
            onboarding_id=row.source_onboarding_id,
            expected_onboarding_revision=row.source_revision if row.source_onboarding_id else None,
        )
    else:
        payload = Submission(
            **shared,
            operation="update" if row.source_application_id else "create",
            application_id=row.source_application_id,
            expected_application_revision=row.source_revision
            if row.source_application_id
            else None,
            subject_id=row.subject_id,
            subject_kind="organization" if row.direction == "organization" else "company",
            direction=row.direction,
        )
    submission = ReceptionSubmission(
        draft_id=row.id,
        conversation_id=row.conversation_id,
        system_id=row.system_id,
        tenant_id=row.tenant_id,
        draft_revision=row.revision,
        summary_version=summary_version,
        confirmation_message_id=message_id,
        submission_key=key,
        payload=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    db.add(submission)
    row.state = "submitting"
    await db.flush()
    return submission


async def submit_confirmed(factory, fence, submission_id, client) -> SubmitReceipt:
    async with factory.begin() as db:
        run = await require_write(db, fence)
        row = await db.get(ReceptionSubmission, submission_id)
        if (
            row is None
            or (row.conversation_id, row.system_id, row.tenant_id)
            != (run.conversation_id, run.system_id, run.tenant_id)
            or run.user_id is None
        ):
            raise ServiceError("FORBIDDEN", 403)
        if row.status == "accepted":
            return SubmitReceipt.model_validate(row.receipt)
        if row.status != "pending":
            raise ServiceError("DRAFT_CONFLICT", 409)
        draft = await db.get(ReceptionDraft, row.draft_id)
        if draft.revision != row.draft_revision or draft.state != "submitting":
            raise ServiceError("DRAFT_CONFLICT", 409)
        user = await db.get(User, run.user_id)
        actor = actor_from_user(user)
        kind = draft.kind
        payload = (OnboardingSubmission if kind == "onboarding" else Submission).model_validate(
            row.payload
        )
    if client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    try:
        result = await client.result(actor, payload.submission_key)
        if result.status == "accepted":
            receipt = result.receipt
        elif result.status == "not_found":
            receipt = await (
                client.onboarding(actor, payload)
                if kind == "onboarding"
                else client.submit(actor, payload)
            )
        else:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    except ServiceError as error:
        if error.code in (
            "INPUT_INVALID",
            "DRAFT_CONFLICT",
            "SUBJECT_CONFLICT",
            "IDEMPOTENCY_CONFLICT",
            "FORBIDDEN",
        ):
            async with factory.begin() as db:
                await require_write(db, fence)
                row = await db.get(ReceptionSubmission, submission_id, populate_existing=True)
                draft = await db.get(ReceptionDraft, row.draft_id, populate_existing=True)
                if row.status == "pending" and draft.revision == row.draft_revision:
                    row.status = "failed"
                    draft.state, draft.summary_version = "collecting", None
                    draft.revision += 1
        raise
    if (
        receipt.status != "accepted"
        or receipt.submission_key != payload.submission_key
        or receipt.kind != kind
        or payload.application_id is not None
        and receipt.application_id != payload.application_id
        or kind == "onboarding"
        and payload.onboarding_id is not None
        and receipt.onboarding_id != payload.onboarding_id
    ):
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    async with factory.begin() as db:
        run = await require_write(db, fence)
        row = await db.get(ReceptionSubmission, submission_id, populate_existing=True)
        draft = await db.get(ReceptionDraft, row.draft_id, populate_existing=True)
        if draft.revision != row.draft_revision or (
            row.conversation_id,
            row.system_id,
            row.tenant_id,
        ) != (run.conversation_id, run.system_id, run.tenant_id):
            raise ServiceError("DRAFT_CONFLICT", 409)
        row.receipt = receipt.model_dump(mode="json", by_alias=True, exclude_none=True)
        row.status = "accepted"
        draft.state = "accepted"
        return receipt


async def create_draft_from_business(
    factory, actor, conversation_id, request, client, *, fence=None
):
    from xiaohe.conversations import require_conversation

    async with factory.begin() as db:
        await require_conversation(db, actor, conversation_id)
        if fence is not None:
            run = await require_write(db, fence)
            if run.conversation_id != conversation_id or run.user_id != actor.user_id:
                raise ServiceError("FORBIDDEN", 403)
        if actor.user_id is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        existing = await db.scalar(
            select(ReceptionDraft).where(
                ReceptionDraft.conversation_id == conversation_id,
                ReceptionDraft.client_draft_id == request.client_draft_id,
            )
        )
        if existing is not None:
            return await create_draft(db, actor, conversation_id, request)
        user = await db.get(User, actor.user_id)
        trusted = actor_from_user(user)
    if client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    if request.source_application_id:
        source = await client.application(trusted, request.source_application_id)
        if request.source_onboarding_id:
            current = await client.onboarding_application(
                trusted, request.source_application_id, request.source_onboarding_id
            )
            if (
                current.application_id != request.source_application_id
                or current.id != request.source_onboarding_id
            ):
                raise ServiceError("FORBIDDEN", 403)
            source = source.model_copy(
                update={
                    "public_fields": current.public_fields,
                    "request_fields": current.request_fields,
                    "files": current.files,
                    "revision": current.revision,
                }
            )
    else:
        source, cursor, seen = None, None, set()
        while True:
            page = await client.subjects(
                trusted,
                {
                    "kind": "organization" if request.direction == "organization" else "company",
                    "cursor": cursor,
                },
            )
            source = next(
                (subject for subject in page.items if subject.id == request.subject_id), None
            )
            if source is not None or page.next_cursor is None:
                break
            if page.next_cursor in seen:
                raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        if source is None:
            raise ServiceError("FORBIDDEN", 403)
    async with factory.begin() as db:
        if fence is not None:
            await require_conversation(db, actor, conversation_id)
            await require_write(db, fence)
        return await create_draft(db, actor, conversation_id, request, source=source)
