import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.identity import require_session
from xiaohe.models import User
from xiaohe.reception.contracts import (
    FORMAL_REQUIRED_FIELDS,
    ApplicationFormView,
    Submission,
    SubmissionResult,
    SubmitReceipt,
)
from xiaohe.reception.fields import PUBLIC_FIELDS, missing_required, validate_fields
from xiaohe.reception.models import ReceptionFormSubmission
from xiaohe.reception.submission import actor_from_user
from xiaohe.resources import RESOURCE_ROOT


async def authority(db, actor):
    await require_session(db, actor)
    user = await db.get(User, actor.user_id) if actor.user_id else None
    if user is None or not user.enabled:
        raise ServiceError("AUTH_REQUIRED", 401)
    return actor_from_user(user)


def form_scope(actor):
    return (actor.system.system_id, actor.system.tenant_id, actor.user_id)


def form_definition(direction):
    bundle = load_runtime_bundle(RESOURCE_ROOT)
    policy = bundle.field_policies[direction]
    required = list(dict.fromkeys(policy.required_fields + list(FORMAL_REQUIRED_FIELDS[direction])))
    return bundle.bundle_id, policy.model_copy(update={"required_fields": required})


def form_field_policy(direction, *, expected_bundle_id):
    bundle_id, policy = form_definition(direction)
    if bundle_id != expected_bundle_id:
        raise ServiceError("DRAFT_CONFLICT", 409)
    return policy


async def read_application_form(factory, actor, source_application_id, mode, client):
    if mode not in ("edit", "new"):
        raise ServiceError("INPUT_INVALID", 422)
    async with factory.begin() as db:
        bound = await authority(db, actor)
    if client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    source = await client.application(bound, source_application_id)
    fields = source.public_fields | source.request_fields
    if mode == "new":
        cursor = None
        while True:
            query = {"kind": "organization" if source.direction == "organization" else "company"}
            if cursor:
                query["cursor"] = cursor
            page = await client.subjects(bound, query)
            profile = next((item for item in page.items if item.id == source.subject_id), None)
            if profile:
                fields = {
                    k: v
                    for k, v in profile.public_fields.items()
                    if k in PUBLIC_FIELDS[source.direction]
                }
                break
            if not page.next_cursor or page.next_cursor == cursor:
                raise ServiceError("FORBIDDEN", 403)
            cursor = page.next_cursor
    bundle_id, definition = form_definition(source.direction)
    async with factory.begin() as db:
        await authority(db, actor)
    return ApplicationFormView(
        mode=mode,
        source_application_id=source.id,
        subject_id=source.subject_id,
        direction=source.direction,
        expected_application_revision=source.revision if mode == "edit" else None,
        definition_bundle_id=bundle_id,
        field_layout={
            "rounds": [{"id": r.id, "name": r.name, "fields": r.fields} for r in definition.rounds],
            "requiredFields": definition.required_fields,
        },
        fields=fields,
        files=source.files if mode == "edit" else [],
    )


async def submission_row(db, actor, client_id, *, lock=False):
    system, tenant, user = form_scope(actor)
    query = select(ReceptionFormSubmission).where(
        ReceptionFormSubmission.system_id == system,
        ReceptionFormSubmission.tenant_id == tenant,
        ReceptionFormSubmission.user_id == user,
        ReceptionFormSubmission.client_submission_id == client_id,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def submit_application_form(factory, actor, payload, client):
    raw = payload.model_dump(mode="json", by_alias=True)
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    async with factory.begin() as db:
        await authority(db, actor)
        existing = await submission_row(db, actor, payload.client_submission_id)
        if existing and existing.input_hash != digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
    if existing is None:
        view = await read_application_form(
            factory, actor, payload.source_application_id, payload.mode, client
        )
        if (
            view.expected_application_revision != payload.expected_application_revision
            or view.definition_bundle_id != payload.definition_bundle_id
        ):
            raise ServiceError("DRAFT_CONFLICT", 409)
        if getattr(payload, "policy_form_id", None):
            from xiaohe.deep_runtime.forms import application_policy_v2

            async with factory.begin() as db:
                projected = await application_policy_v2(db, actor, view)
            if projected.form_id != payload.policy_form_id:
                raise ServiceError("ACTION_STALE", 409)
            editable = {key for group in projected.groups for key in group.fields}
            if payload.fields.keys() - editable:
                raise ServiceError("INPUT_INVALID", 422)
        policy = form_field_policy(view.direction, expected_bundle_id=view.definition_bundle_id)
        fields = validate_fields(view.direction, view.fields | payload.fields, policy=policy)
        if missing_required(view.direction, fields, policy=policy):
            raise ServiceError("INPUT_INVALID", 422)
        source_files = {f.id: f for f in view.files}
        files = []
        async with factory.begin() as db:
            await authority(db, actor)
            for file_id in payload.files:
                if file_id in source_files:
                    files.append(source_files[file_id])
                else:
                    from xiaohe.reception.files import _ref
                    from xiaohe.reception.form_files import owned_form_file

                    row = await owned_form_file(db, actor, payload.form_id, file_id)
                    if row.status != "ready":
                        raise ServiceError("FILE_INVALID", 422)
                    files.append(_ref(row))
            scope = form_scope(actor)
            key = (
                "form:"
                + hashlib.sha256(
                    json.dumps([*scope, payload.client_submission_id]).encode()
                ).hexdigest()
            )
            formal = Submission(
                submission_key=key,
                draft_id="form:" + str(payload.form_id),
                draft_revision=1,
                operation="update" if payload.mode == "edit" else "create",
                application_id=view.source_application_id if payload.mode == "edit" else None,
                expected_application_revision=payload.expected_application_revision,
                subject_id=view.subject_id,
                subject_kind="organization" if view.direction == "organization" else "company",
                direction=view.direction,
                public_fields={
                    k: v for k, v in fields.items() if k in PUBLIC_FIELDS[view.direction]
                },
                request_fields={
                    k: v for k, v in fields.items() if k not in PUBLIC_FIELDS[view.direction]
                },
                files=files,
            )
            await db.execute(
                insert(ReceptionFormSubmission)
                .values(
                    system_id=scope[0],
                    tenant_id=scope[1],
                    user_id=scope[2],
                    client_submission_id=payload.client_submission_id,
                    form_id=payload.form_id,
                    input_hash=digest,
                    payload=formal.model_dump(mode="json", by_alias=True),
                    status="pending",
                )
                .on_conflict_do_nothing()
            )
            row = await submission_row(db, actor, payload.client_submission_id, lock=True)
            if row.input_hash != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
    return await application_form_result(factory, actor, payload.client_submission_id, client)


async def application_form_result(factory, actor, client_submission_id, client):
    async with factory.begin() as db:
        bound = await authority(db, actor)
        row = await submission_row(db, actor, client_submission_id)
        if row is None:
            return SubmissionResult(status="not_found", receipt=None)
        if row.status == "accepted":
            return SubmissionResult(
                status="accepted", receipt=SubmitReceipt.model_validate(row.receipt)
            )
        if row.status == "failed":
            raise ServiceError(row.receipt["code"], row.receipt["status"])
        payload = Submission.model_validate(row.payload)
    if client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    try:
        result = await client.result(bound, payload.submission_key)
        if result.status == "pending":
            return result
        receipt = (
            result.receipt if result.status == "accepted" else await client.submit(bound, payload)
        )
        if (
            receipt.status != "accepted"
            or receipt.kind != "service"
            or receipt.submission_key != payload.submission_key
            or (payload.application_id and receipt.application_id != payload.application_id)
        ):
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    except ServiceError as error:
        if error.code in (
            "INPUT_INVALID",
            "DRAFT_CONFLICT",
            "SUBJECT_CONFLICT",
            "IDEMPOTENCY_CONFLICT",
            "FORBIDDEN",
            "APPLICATION_FLOW_CHANGED",
            "APPLICATION_MIGRATION_REQUIRED",
        ):
            async with factory.begin() as db:
                await authority(db, actor)
                row = await submission_row(db, actor, client_submission_id, lock=True)
                if row.status == "pending":
                    row.status, row.receipt = (
                        "failed",
                        {"code": error.code, "status": error.http_status},
                    )
        raise
    async with factory.begin() as db:
        await authority(db, actor)
        row = await submission_row(db, actor, client_submission_id, lock=True)
        row.status, row.receipt = "accepted", receipt.model_dump(mode="json", by_alias=True)
    return SubmissionResult(status="accepted", receipt=receipt)
