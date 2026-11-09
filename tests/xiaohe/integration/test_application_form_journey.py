"""独立表单真实 HTTP/PG/MySQL/MinIO 闭环；中心和运营身份使用受控替身。"""

import ssl
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from integration.reception_journey_runtime import configuration
from integration.test_reception_journey import identity

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_standalone_forms_preserve_progress_files_and_chat_history(direction):
    config, settings = configuration()
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    control = {"X-Journey-Control": config["control"]}
    tls = ssl.create_default_context(cafile=config["certificate"])
    async with httpx.AsyncClient(base_url=config["origin"], verify=tls, trust_env=False,
                                headers={"Origin": config["origin"]}, timeout=30) as client:
        client.cookies.set("__Host-XIAOHESESSION", await identity(config, settings, "journey-user"))
        snapshot = (await client.post("/ai-api/v1/session", json={})).json()
        client.headers[snapshot["csrf"]["headerName"]] = snapshot["csrf"]["token"]
        before_applications=(await client.get("/__test/counts",headers=control)).json()["co_application"]
        public={"organizationName":"联合表单协会","memberSize":"100—500","coverage":"本省","onlineChannels":["官网"],"city":"杭州"} if direction=="organization" else {"companyName":"联合表单企业","city":"杭州"}
        stage="communicating" if direction=="buyer" else "approved"
        # 测试前置正式记录，不经聊天创建草稿。
        response = await client.post(config["java_origin"] + "/internal-api/reception/v1/applications/submit",
            headers={"Authorization": "Bearer " + settings.reception_secret.get_secret_value(),
                     "X-Reception-Application": "journey-ai"},
            json={"actor": {"aiUserId": snapshot["viewer"]["userId"], "systemId": "portal",
                "issuer": config["origin"], "sub": "journey-user", "sourceTenantId": "2"},
                "payload": {"submissionKey": "seed-form-"+direction, "draftId": "form:seed", "draftRevision": 1,
                    "operation": "create", "subjectKind": "organization" if direction=="organization" else "company", "direction": direction,
                    "publicFields": public,
                    "requestFields": {"contactName": "张先生", "contactPhone": "企业总台"}, "files": []}})
        assert response.status_code == 201, response.text
        application_id = response.json()["data"]["applicationId"]
        for route, payload in [("assign", {"applicationId": application_id, "expectedRevision": 1, "managerId": "99"}),
                               ("progress", {"applicationId": application_id, "expectedRevision": 2,
                                   "stage": stage, "grade": "A", "visibility": "public", "text": "审核通过"})]:
            response = await client.post("/admin-api/collaborator/reception/" + route, headers=control, json=payload)
            assert response.status_code == 200, response.text

        def chat_counts():
            with engine.connect() as db:
                return tuple(db.scalar(text("SELECT count(*) FROM xiaohe." + name))
                             for name in ("conversations", "messages", "runs", "reception_drafts"))

        before = chat_counts()
        base = "/ai-api/v1/reception/application-form"
        response = await client.get(base, params={"sourceApplicationId": application_id, "mode": "edit"})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["expectedApplicationRevision"] == 3
        form_id = str(uuid4())
        material = b"%PDF-1.7\nstandalone material\n%%EOF\n"
        response = await client.post(base + "/" + form_id + "/files", json={"clientUploadId": "material",
            "category": "case", "filename": "资料.pdf", "mediaType": "application/pdf", "bytes": len(material)})
        assert response.status_code == 201, response.text
        upload = response.json()
        response = await client.put(upload["uploadPath"], content=material, headers={"Content-Type": "application/pdf"})
        assert response.status_code == 200, response.text
        file_id = response.json()["id"]
        body = {"formId": form_id, "clientSubmissionId": str(uuid4()), "mode": "edit",
            "sourceApplicationId": application_id, "expectedApplicationRevision": 3,
            "definitionBundleId": view["definitionBundleId"], "fields": {"contactPhone": "更新后的总台"}, "files": [file_id]}
        await client.post("/__test/drop-next-submit", headers=control)
        response = await client.post(base + "/submissions", json=body)
        assert response.status_code == 503, response.text
        response = await client.get(base + "/submissions/" + body["clientSubmissionId"])
        assert response.status_code == 200, response.text
        assert response.json()["receipt"]["applicationId"] == application_id
        assert response.json()["receipt"]["revision"] == 4
        assert (await client.post(base + "/submissions", json=body)).json() == response.json()
        await client.delete(base + "/" + form_id)
        assert (await client.get(base + "/files/" + file_id + "/download")).content == material
        response = await client.get("/admin-api/collaborator/reception/applications/" + application_id + "/files/" + file_id, headers=control)
        assert response.status_code == 200 and response.content == material
        response = await client.get(base, params={"sourceApplicationId": application_id, "mode": "new"})
        new = response.json()
        assert new["fields"] == public
        assert new["files"] == [] and new["expectedApplicationRevision"] is None
        response = await client.post(base + "/submissions", json=body | {"formId": str(uuid4()),
            "clientSubmissionId": str(uuid4()), "mode": "new", "expectedApplicationRevision": None,
            "fields": new["fields"] | {"contactName": "李女士", "contactPhone": "新联系人"}, "files": []})
        assert response.status_code == 200, response.text
        assert response.json()["receipt"]["applicationId"] != application_id
        progress = (await client.get("/ai-api/v1/reception/progress")).json()
        applications = [item for group in progress["groups"] for item in group["applications"]]
        original = next(item for item in applications if item["id"] == application_id)
        assert original["stage"] == stage and original["grade"] == "A"
        assert original["manager"]["name"] == "隔离生态经理"
        assert original["onboarding"] is None
        response = await client.post("/admin-api/collaborator/reception/progress", headers=control,
            json={"applicationId": application_id, "expectedRevision": 4, "stage": "onboarded", "grade": "A", "visibility": "public", "text": "已完成入驻"})
        assert response.status_code == (422 if direction=="buyer" else 200), response.text
        counts = (await client.get("/__test/counts", headers=control)).json()
        assert counts["co_application"] == before_applications+2 and counts["co_onboarding"] == 0
        assert chat_counts() == before
        invalid = await client.post(base + "/submissions", json=body | {"stage":"approved"})
        assert invalid.status_code == 422
        denied = await client.get(base, params={"sourceApplicationId": application_id,"mode":"edit"}, headers={snapshot["csrf"]["headerName"]:""})
        assert denied.status_code == 403
        async with httpx.AsyncClient(base_url=config["origin"], verify=tls, trust_env=False, headers={"Origin":config["origin"]}) as other:
            other.cookies.set("__Host-XIAOHESESSION",await identity(config,settings,"journey-other"))
            other_snapshot=(await other.post("/ai-api/v1/session",json={})).json()
            other.headers[other_snapshot["csrf"]["headerName"]]=other_snapshot["csrf"]["token"]
            denied=await other.get(base,params={"sourceApplicationId":application_id,"mode":"edit"})
            assert denied.status_code == 403
            denied=await other.get(base+"/files/"+file_id+"/download")
            assert denied.status_code == 403

    engine.dispose()
