from uuid import UUID

from fastapi import APIRouter, Request

from xiaohe.api import _actor, _body
from xiaohe.reception.contracts import CreateDraft, PatchDraft, ReceptionAction
from xiaohe.reception.flow import apply_reception_action
from xiaohe.reception.store import create_draft, load_reception, patch_draft
from xiaohe.reception.submission import create_draft_from_business


def create_reception_router(factory, business_client=None):
    router = APIRouter(prefix="/ai-api/v1/reception")

    @router.get("/conversations/{conversation_id}")
    async def state(conversation_id: UUID, request: Request):
        async with factory.begin() as db:
            view = await load_reception(db, await _actor(request, db), conversation_id)
            data = view.model_dump(mode="json", by_alias=True)
            if view.active_draft is not None:
                data["activeDraft"] = view.active_draft.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            return data

    @router.post("/conversations/{conversation_id}/drafts", status_code=201)
    async def create(conversation_id: UUID, request: Request):
        body = await _body(request, CreateDraft)
        if body.subject_id or body.source_application_id:
            async with factory.begin() as db:
                actor = await _actor(request, db)
            view = await create_draft_from_business(
                factory, actor, conversation_id, body, business_client
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

    @router.post("/drafts/{draft_id}/actions", status_code=202)
    async def action(draft_id: UUID, request: Request):
        body = await _body(request, ReceptionAction)
        async with factory.begin() as db:
            return await apply_reception_action(db, await _actor(request, db), draft_id, body)

    return router
