import hmac
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import TypeAdapter, ValidationError

from xiaohe.api import _actor, _body
from xiaohe.contracts import ServiceError
from xiaohe.identity import CSRF_HEADER, require_session
from xiaohe.lead_skills.views import supports_lead_policy_v2
from xiaohe.models import User
from xiaohe.reception.contracts import (
    ApplicationFormQuery,
    CreateDraft,
    DetachFile,
    FormInitFile,
    FormSubmissionInput,
    InitFile,
    OperationalDownload,
    PageCursor,
    PatchDraft,
    ReceptionAction,
    ReceptionControl,
    SubjectQuery,
)
from xiaohe.reception.files import (
    detach_file,
    download_file,
    download_operational_file,
    init_file,
    write_file,
)
from xiaohe.reception.flow import apply_reception_action, apply_reception_control
from xiaohe.reception.store import create_draft, load_reception, patch_draft
from xiaohe.reception.submission import actor_from_user, create_draft_from_business


def create_reception_router(factory, business_client=None):
    router = APIRouter(prefix="/ai-api/v1/reception")

    async def business_query(request, model, method):
        query = request.query_params
        if len(query.multi_items()) != len(query):
            raise ServiceError("INPUT_INVALID", 422)
        try:
            payload = model.model_validate(dict(query)).model_dump(
                mode="json", by_alias=True, exclude_unset=True
            )
        except ValidationError:
            raise ServiceError("INPUT_INVALID", 422) from None
        async with factory.begin() as db:
            actor = await _actor(request, db)
            if actor.user_id is None:
                raise ServiceError("AUTH_REQUIRED", 401)
            record = await require_session(db, actor)
            proof = request.headers.getlist(CSRF_HEADER)
            if (
                len(proof) != 1
                or not proof[0].isascii()
                or not hmac.compare_digest(proof[0], record.csrf_token)
            ):
                raise ServiceError("CSRF_INVALID", 403)
            authority = actor_from_user(await db.get(User, actor.user_id))
        if business_client is None:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        result = await getattr(business_client, method)(authority, payload)
        async with factory.begin() as db:
            if await _actor(request, db) != actor:
                raise ServiceError("AUTH_REQUIRED", 401)
        return result.model_dump(mode="json", by_alias=True)

    @router.get("/subjects")
    async def subjects(request: Request):
        return await business_query(request, SubjectQuery, "subjects")

    @router.get("/progress")
    async def progress(request: Request):
        return await business_query(request, PageCursor, "progress")

    def storage(request):
        service = getattr(request.app.state, "xiaohe_artifacts", None)
        if service is None:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return service

    async def form_actor(request):
        async with factory.begin() as db:
            actor = await _actor(request, db)
            if actor.user_id is None:
                raise ServiceError("AUTH_REQUIRED", 401)
            record = await require_session(db, actor)
            proof = request.headers.getlist(CSRF_HEADER)
            if (
                len(proof) != 1
                or not proof[0].isascii()
                or not hmac.compare_digest(proof[0], record.csrf_token)
            ):
                raise ServiceError("CSRF_INVALID", 403)
        return actor

    @router.get("/application-form")
    async def form_view(request: Request):
        from xiaohe.reception.forms import read_application_form

        if len(request.query_params.multi_items()) != len(request.query_params):
            raise ServiceError("INPUT_INVALID", 422)
        try:
            query = ApplicationFormQuery.model_validate(dict(request.query_params))
        except ValidationError:
            raise ServiceError("INPUT_INVALID", 422) from None
        view = await read_application_form(
            factory,
            await form_actor(request),
            query.source_application_id,
            query.mode,
            business_client,
        )
        data = view.model_dump(mode="json", by_alias=True)
        from xiaohe.lead_skills.views import (
            current_interaction_policy,
            lead_policy_view,
            supports_lead_policy,
        )

        if supports_lead_policy_v2(request):
            from xiaohe.deep_runtime.forms import application_policy_v2

            async with factory.begin() as db:
                actor = await _actor(request, db)
                data["leadPolicy"] = (await application_policy_v2(db, actor, view)).model_dump(
                    mode="json", by_alias=True
                )
        elif supports_lead_policy(request):
            from xiaohe.reception.forms import form_field_policy

            policy = form_field_policy(view.direction, expected_bundle_id=view.definition_bundle_id)
            async with factory.begin() as db:
                await _actor(request, db)
                interaction = await current_interaction_policy(db)
            data["leadPolicy"] = lead_policy_view(policy, interaction).model_dump(
                mode="json", by_alias=True
            )
        return data

    @router.post("/application-form/submissions")
    async def form_submit(request: Request):
        from xiaohe.reception.contracts import FormSubmissionV2
        from xiaohe.reception.forms import submit_application_form

        body = await _body(
            request,
            FormSubmissionV2 if supports_lead_policy_v2(request) else FormSubmissionInput,
            max_bytes=512 * 1024,
        )
        result = await submit_application_form(
            factory, await form_actor(request), body, business_client
        )
        return result.model_dump(mode="json", by_alias=True)

    @router.get("/application-form/submissions/{client_submission_id}")
    async def form_result(client_submission_id: str, request: Request):
        from xiaohe.reception.contracts import Id
        from xiaohe.reception.forms import application_form_result

        try:
            TypeAdapter(Id).validate_python(client_submission_id)
        except ValidationError:
            raise ServiceError("INPUT_INVALID", 422) from None
        result = await application_form_result(
            factory, await form_actor(request), client_submission_id, business_client
        )
        return result.model_dump(mode="json", by_alias=True)

    @router.post("/application-form/{form_id}/files", status_code=201)
    async def form_file_init(form_id: UUID, request: Request):
        from xiaohe.reception.form_files import init_form_file

        body = await _body(request, FormInitFile)
        result = await init_form_file(factory, await form_actor(request), form_id, body)
        return result.model_dump(mode="json", by_alias=True)

    @router.put("/application-form/{form_id}/files/{file_id}")
    async def form_file_upload(form_id: UUID, file_id: UUID, request: Request):
        from xiaohe.reception.form_files import write_form_file

        ref = await write_form_file(
            factory,
            await form_actor(request),
            form_id,
            file_id,
            request.stream(),
            artifacts=storage(request),
        )
        return ref.model_dump(mode="json", by_alias=True)

    @router.delete("/application-form/{form_id}/files/{file_id}", status_code=204)
    async def form_file_detach(form_id: UUID, file_id: UUID, request: Request):
        from xiaohe.reception.form_files import detach_form_file

        await detach_form_file(factory, await form_actor(request), form_id, file_id)
        return Response(status_code=204)

    @router.delete("/application-form/{form_id}", status_code=204)
    async def form_abandon(form_id: UUID, request: Request):
        from xiaohe.reception.form_files import abandon_form

        await abandon_form(factory, await form_actor(request), form_id)
        return Response(status_code=204)

    @router.get("/application-form/files/{file_id}/download")
    async def form_file_download(file_id: UUID, request: Request):
        from xiaohe.reception.form_files import download_form_file

        ref, content = await download_form_file(
            factory, await form_actor(request), file_id, artifacts=storage(request)
        )
        return file_response(ref, content)

    @router.get("/conversations/{conversation_id}")
    async def state(conversation_id: UUID, request: Request):
        async with factory.begin() as db:
            view = await load_reception(db, await _actor(request, db), conversation_id)
            data = view.model_dump(mode="json", by_alias=True)
            if view.active_draft is not None:
                data["activeDraft"] = view.active_draft.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            from xiaohe.lead_skills.views import (
                current_interaction_policy,
                lead_policy_view,
                supports_lead_policy,
            )

            if supports_lead_policy_v2(request):
                from xiaohe.deep_runtime.forms import lead_policy_v2

                data["leadPolicy"] = None
                if view.active_draft is not None:
                    data["leadPolicy"] = (
                        await lead_policy_v2(
                            db, await _actor(request, db), UUID(view.active_draft.id)
                        )
                    ).model_dump(mode="json", by_alias=True)
            elif supports_lead_policy(request):
                data["leadPolicy"] = None
                if view.active_draft is not None:
                    from xiaohe.lead_skills.store import resolve_draft_policy
                    from xiaohe.reception.models import ReceptionDraft

                    policy = await resolve_draft_policy(
                        db, await db.get(ReceptionDraft, UUID(view.active_draft.id))
                    )
                    interaction = await current_interaction_policy(db, conversation_id)
                    data["leadPolicy"] = lead_policy_view(policy, interaction).model_dump(
                        mode="json", by_alias=True
                    )
            return data

    @router.post("/conversations/{conversation_id}/controls", status_code=202)
    async def control(conversation_id: UUID, request: Request):
        body = await _body(request, ReceptionControl)
        async with factory.begin() as db:
            return await apply_reception_control(
                db, await _actor(request, db), conversation_id, body
            )

    @router.post("/conversations/{conversation_id}/drafts", status_code=201)
    async def create(conversation_id: UUID, request: Request):
        body = await _body(request, CreateDraft)
        if body.subject_id or body.source_application_id:
            async with factory.begin() as db:
                actor = await _actor(request, db)
            view = await create_draft_from_business(
                factory,
                actor,
                conversation_id,
                body,
                business_client,
            )
            return view.model_dump(mode="json", by_alias=True, exclude_none=True)
        async with factory.begin() as db:
            view = await create_draft(db, await _actor(request, db), conversation_id, body)
            return view.model_dump(mode="json", by_alias=True, exclude_none=True)

    @router.patch("/drafts/{draft_id}")
    async def patch(draft_id: UUID, request: Request):
        body = await _body(request, PatchDraft, max_bytes=512 * 1024)
        async with factory.begin() as db:
            view = await patch_draft(
                db, await _actor(request, db), draft_id, body.expected_revision, body.fields
            )
            return view.model_dump(mode="json", by_alias=True, exclude_none=True)

    @router.patch("/drafts/{draft_id}/form")
    async def dynamic_form_patch(draft_id: UUID, request: Request):
        from xiaohe.deep_runtime.forms import patch_form
        from xiaohe.reception.contracts import PatchForm

        body = await _body(request, PatchForm, max_bytes=512 * 1024)
        async with factory.begin() as db:
            view = await patch_form(
                db,
                await _actor(request, db),
                draft_id,
                form_id=body.form_id,
                expected_revision=body.expected_revision,
                fields=body.fields,
            )
            return view.model_dump(mode="json", by_alias=True, exclude_none=True)

    @router.post("/drafts/{draft_id}/actions", status_code=202)
    async def action(draft_id: UUID, request: Request):
        body = await _body(request, ReceptionAction)
        async with factory.begin() as db:
            return await apply_reception_action(db, await _actor(request, db), draft_id, body)

    @router.post("/drafts/{draft_id}/files", status_code=201)
    async def initialize_file(draft_id: UUID, request: Request):
        body = await _body(request, InitFile)
        async with factory.begin() as db:
            return await init_file(db, await _actor(request, db), draft_id, body)

    @router.put("/drafts/{draft_id}/files/{file_id}")
    async def upload(draft_id: UUID, file_id: UUID, request: Request):
        async with factory.begin() as db:
            actor = await _actor(request, db)
        return await write_file(
            factory, actor, draft_id, file_id, request.stream(), artifacts=storage(request)
        )

    @router.delete("/drafts/{draft_id}/files/{file_id}")
    async def detach(draft_id: UUID, file_id: UUID, request: Request):
        body = await _body(request, DetachFile)
        async with factory.begin() as db:
            result = await detach_file(
                db, await _actor(request, db), draft_id, file_id, body.expected_revision
            )
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    @router.get("/files/{file_id}/download")
    async def download(file_id: UUID, request: Request):
        async with factory.begin() as db:
            actor = await _actor(request, db)
        ref, content = await download_file(factory, actor, file_id, artifacts=storage(request))
        return file_response(ref, content)

    return router


def file_response(ref, content):
    return Response(
        content=content,
        media_type=ref.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(ref.filename, safe=""),
        },
    )


def create_reception_file_router(factory, settings, artifacts):
    router = APIRouter(prefix="/internal-api/reception-files/v1")

    @router.post("/files/{file_id}/download")
    async def operational_download(file_id: UUID, request: Request):
        application = request.headers.getlist("X-Reception-Application")
        credentials = request.headers.getlist("Authorization")
        if (
            not all(
                (
                    settings.reception_file_application_id,
                    settings.reception_file_secret,
                    settings.reception_file_system_id,
                )
            )
            or application != [settings.reception_file_application_id]
            or len(credentials) != 1
            or not hmac.compare_digest(
                credentials[0].encode("utf-8"),
                ("Bearer " + settings.reception_file_secret.get_secret_value()).encode("utf-8"),
            )
        ):
            raise ServiceError("FORBIDDEN", 403)
        body = await _body(request, OperationalDownload)
        ref, content = await download_operational_file(
            factory,
            settings.reception_file_system_id,
            file_id,
            body.application_id,
            body.owner_ai_user_id,
            artifacts=artifacts,
        )
        return file_response(ref, content)

    return router
