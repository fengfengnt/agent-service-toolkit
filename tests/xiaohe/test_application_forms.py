from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_identity_sessions import actor, login

from xiaohe.contracts import ServiceError
from xiaohe.models import Conversation, Message, Run
from xiaohe.reception.contracts import ApplicationView, SubjectPage, SubmissionResult, SubmitReceipt
from xiaohe.reception.models import ReceptionDraft

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


class FormBackend:
    def __init__(self):
        self.source = ApplicationView(id="10", subject_id="20", direction="supplier", revision=3,
            public_fields={"companyName": "原公司", "city": "杭州"},
            request_fields={"contactName": "张先生", "contactPhone": "总台", "productDescription": "旧需求"}, files=[])
        self.receipts = {}
        self.created = 0
        self.lose_response = False

    async def application(self, authority, application_id):
        if application_id != "10":
            raise ServiceError("FORBIDDEN", 403)
        return self.source

    async def subjects(self, authority, query):
        return SubjectPage(items=[{"id": "20", "kind": "company", "name": "原公司",
            "publicFields": {"companyName": "原公司", "city": "上海"}}], next_cursor=None)

    async def result(self, authority, key):
        receipt = self.receipts.get(key)
        return SubmissionResult(status="accepted" if receipt else "not_found", receipt=receipt)

    async def submit(self, authority, payload):
        self.created += 1
        self.last_payload = payload
        receipt = SubmitReceipt(submission_key=payload.submission_key, kind="service",
            application_id=payload.application_id or "11", revision=4, status="accepted")
        self.receipts[payload.submission_key] = receipt
        if self.lose_response:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        return receipt


async def counts(factory):
    async with factory.begin() as db:
        return tuple([await db.scalar(select(func.count()).select_from(model))
                      for model in (Conversation, ReceptionDraft, Message, Run)])


async def setup(factory):
    _, token = await login(factory)
    return await actor(factory, token), FormBackend()


async def test_form_read_new_only_copies_current_public_profile_without_chat_side_effects(pg_factory):
    from xiaohe.reception.forms import read_application_form
    who, backend = await setup(pg_factory)
    before = await counts(pg_factory)
    view = await read_application_form(pg_factory, who, "10", "new", backend)
    assert view.fields == {"companyName": "原公司", "city": "上海"}
    assert view.expected_application_revision is None
    assert view.files == []
    assert await counts(pg_factory) == before


async def test_form_submit_lost_response_reconciles_same_key(pg_factory):
    import hashlib
    import json

    from xiaohe.reception.contracts import FormSubmissionInput
    from xiaohe.reception.forms import (
        application_form_result,
        read_application_form,
        submission_row,
        submit_application_form,
    )
    who, backend = await setup(pg_factory)
    before = await counts(pg_factory)
    view = await read_application_form(pg_factory, who, "10", "edit", backend)
    payload = FormSubmissionInput(form_id=uuid4(), client_submission_id=str(uuid4()), mode="edit",
        source_application_id="10", expected_application_revision=3,
        definition_bundle_id=view.definition_bundle_id, fields={"productDescription": "更新需求"}, files=[])
    backend.lose_response = True
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit_application_form(pg_factory, who, payload, backend)
    recovered = await application_form_result(pg_factory, who, payload.client_submission_id, backend)
    assert recovered.status == "accepted"
    assert recovered.receipt.application_id == "10"
    assert backend.created == 1
    assert backend.last_payload.request_fields["contactPhone"] == "总台"
    assert backend.last_payload.request_fields["productDescription"] == "更新需求"
    legacy_raw = payload.model_dump(mode="json", by_alias=True, exclude={"direction"})
    async with pg_factory.begin() as db:
        stored = await submission_row(db, who, payload.client_submission_id)
        assert stored.input_hash == hashlib.sha256(
            json.dumps(legacy_raw, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    assert await counts(pg_factory) == before
    changed = payload.model_copy(update={"fields": {"productDescription": "另一版本"}})
    with pytest.raises(ServiceError, match="IDEMPOTENCY_CONFLICT"):
        await submit_application_form(pg_factory, who, changed, backend)


async def test_form_rejects_stale_revision_invalid_fields_and_unowned_source(pg_factory):
    from xiaohe.reception.contracts import FormSubmissionInput
    from xiaohe.reception.forms import read_application_form, submit_application_form
    who, backend = await setup(pg_factory)
    view = await read_application_form(pg_factory, who, "10", "edit", backend)
    payload = FormSubmissionInput(form_id=uuid4(), client_submission_id=str(uuid4()), mode="edit",
        source_application_id="10", expected_application_revision=2,
        definition_bundle_id=view.definition_bundle_id, fields={}, files=[])
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await submit_application_form(pg_factory, who, payload, backend)
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await read_application_form(pg_factory, who, "999", "edit", backend)
    invalid = payload.model_copy(update={"expected_application_revision": 3, "fields": {"stage": "onboarded"}})
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        await submit_application_form(pg_factory, who, invalid, backend)
    assert backend.created == 0


async def test_form_http_requires_csrf_and_uses_standalone_submission(pg_factory):
    from test_http_api import client_for, session

    from xiaohe.identity import SESSION_COOKIE
    from xiaohe.reception.api import create_reception_router
    _, token = await login(pg_factory)
    backend = FormBackend()
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, backend))
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/ai-api/v1/reception/application-form?sourceApplicationId=10&mode=edit")
        assert response.status_code == 403
        await session(client)
        response = await client.get("/ai-api/v1/reception/application-form?sourceApplicationId=10&mode=edit")
        assert response.status_code == 200, response.text
        assert "no-store" in response.headers["cache-control"]
        view = response.json()
        body = {"formId": str(uuid4()), "clientSubmissionId": str(uuid4()), "mode": "edit",
            "sourceApplicationId": "10", "expectedApplicationRevision": 3,
            "definitionBundleId": view["definitionBundleId"], "fields": {"productDescription": "HTTP修改"}, "files": []}
        accepted = await client.post("/ai-api/v1/reception/application-form/submissions", json=body)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["receipt"]["applicationId"] == "10"
        again = await client.get("/ai-api/v1/reception/application-form/submissions/"+body["clientSubmissionId"])
        assert again.status_code == 200 and again.json() == accepted.json()
        duplicate = await client.get("/ai-api/v1/reception/application-form?sourceApplicationId=10&sourceApplicationId=99&mode=edit")
        assert duplicate.status_code == 422
        forged = await client.post("/ai-api/v1/reception/application-form/submissions", json=body | {"stage": "approved"})
        assert forged.status_code == 422
        assert backend.created == 1


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
@pytest.mark.parametrize("policy_version", [None, "1", "2"])
async def test_source_free_form_submits_only_current_fields(pg_factory, direction, policy_version):
    from test_http_api import client_for, session

    from xiaohe.identity import SESSION_COOKIE
    from xiaohe.reception.api import create_reception_router

    _, token = await login(pg_factory)
    backend = FormBackend()
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, backend))
        client.cookies.set(SESSION_COOKIE, token)
        path = "/ai-api/v1/reception/application-form"
        query = {"mode": "new", "direction": direction}
        assert (await client.get(path, params=query)).status_code == 403
        await session(client)
        before = await counts(pg_factory)
        headers = {"X-Xiaohe-Lead-Policy": policy_version} if policy_version else {}
        response = await client.get(path, params=query, headers=headers)
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["sourceApplicationId"] is None and view["subjectId"] is None
        assert view["mode"] == "new" and view["direction"] == direction
        assert view["fields"] == {} and view["files"] == []
        assert view["expectedApplicationRevision"] is None
        if policy_version:
            assert view["leadPolicy"]["version"] == int(policy_version)
            assert view["leadPolicy"]["direction"] == direction
            assert view["leadPolicy"]["requiredFields"] == view["fieldLayout"]["requiredFields"]
        body = {
            "formId": str(uuid4()),
            "clientSubmissionId": str(uuid4()),
            "mode": "new",
            "sourceApplicationId": None,
            "direction": direction,
            "expectedApplicationRevision": None,
            "definitionBundleId": view["definitionBundleId"],
            "fields": {},
            "files": [],
        }
        if policy_version == "2":
            body["policyFormId"] = view["leadPolicy"]["formId"]
        missing = await client.post(path + "/submissions", json=body, headers=headers)
        assert missing.status_code == 422, missing.text
        fields = {"contactName": "新联系人", "contactPhone": "新联系电话"}
        fields.update(
            {
                "organizationName": "新组织",
                "memberSize": "100 家以下",
                "coverage": "本市",
                "onlineChannels": ["暂无"],
            }
            if direction == "organization"
            else {"companyName": "新公司"}
        )
        body["fields"] = fields
        stale = await client.post(
            path + "/submissions", json=body | {"definitionBundleId": "stale"}, headers=headers
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "DRAFT_CONFLICT"
        if policy_version == "2":
            invalid = await client.post(
                path + "/submissions", json=body | {"policyFormId": "stale"}, headers=headers
            )
            assert invalid.status_code == 409 and invalid.json()["error"]["code"] == "ACTION_STALE"
        accepted = await client.post(path + "/submissions", json=body, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert (
            await client.post(path + "/submissions", json=body, headers=headers)
        ).json() == accepted.json()
        assert backend.created == 1
        formal = backend.last_payload
        assert (
            formal.operation == "create"
            and formal.application_id is None
            and formal.subject_id is None
        )
        assert (
            formal.direction == direction and formal.public_fields | formal.request_fields == fields
        )
        assert formal.files == [] and await counts(pg_factory) == before
        changed_direction = "supplier" if direction == "buyer" else "buyer"
        conflict = await client.post(
            path + "/submissions", json=body | {"direction": changed_direction}, headers=headers
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        )


async def test_form_http_rejects_invalid_new_targets_and_source_direction_changes(pg_factory):
    from test_http_api import client_for, session

    from xiaohe.identity import SESSION_COOKIE
    from xiaohe.reception.api import create_reception_router

    _, token = await login(pg_factory)
    backend = FormBackend()
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory, backend))
        client.cookies.set(SESSION_COOKIE, token)
        await session(client)
        path = "/ai-api/v1/reception/application-form"
        for query in (
            {"mode": "new"},
            {"mode": "edit", "direction": "buyer"},
            {"mode": "new", "direction": "unknown"},
        ):
            assert (await client.get(path, params=query)).status_code == 422
            body = {
                "formId": str(uuid4()),
                "clientSubmissionId": str(uuid4()),
                "sourceApplicationId": None,
                "expectedApplicationRevision": None,
                "definitionBundleId": "bundle",
                "fields": {},
                "files": [],
            } | query
            assert (await client.post(path + "/submissions", json=body)).status_code == 422
        for mode in ("edit", "new"):
            query = {"sourceApplicationId": "10", "mode": mode, "direction": "buyer"}
            denied = await client.get(path, params=query)
            assert denied.status_code == 422, denied.text
            view = (
                await client.get(path, params={"sourceApplicationId": "10", "mode": mode})
            ).json()
            body = {
                "formId": str(uuid4()),
                "clientSubmissionId": str(uuid4()),
                "mode": mode,
                "sourceApplicationId": "10",
                "direction": "buyer",
                "expectedApplicationRevision": 3 if mode == "edit" else None,
                "definitionBundleId": view["definitionBundleId"],
                "fields": {},
                "files": [],
            }
            assert (await client.post(path + "/submissions", json=body)).status_code == 422
        assert backend.created == 0
