"""真实 HTTP/PG/MinIO/Java MySQL 联合验收；不使用会清库的 pg_factory。"""

import asyncio
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from integration.reception_journey_runtime import configuration
from xiaohe.contracts import TrustedSystem, VerifiedCompletion
from xiaohe.database import create_database_engine
from xiaohe.identity import create_user_session

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def identity(config, settings, sub):
    engine = create_database_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            _, token = await create_user_session(
                db,
                TrustedSystem(
                    system_id="portal",
                    tenant_id=1,
                    portal_client_id="fixture-portal",
                    allowed_origin=config["origin"],
                ),
                VerifiedCompletion(
                    issuer=config["origin"],
                    sub=sub,
                    source_tenant_id=2,
                    authenticated_at=datetime.now(UTC),
                    portal_client_id="fixture-portal",
                    transaction_id=str(uuid4()),
                ),
            )
            return token.get_secret_value()
    finally:
        await engine.dispose()


async def wait(check, seconds=40):
    async with asyncio.timeout(seconds):
        while True:
            value = await check()
            if value:
                return value
            await asyncio.sleep(0.2)


async def test_actual_reception_application_progress_onboarding_and_private_files():
    config, settings = configuration()
    tls = ssl.create_default_context(cafile=config["certificate"])
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    with engine.connect() as db:
        assert db.scalar(text("SELECT current_database()")).startswith("test_xiaohe_reception_")
    control = {"X-Journey-Control": config["control"]}
    async with httpx.AsyncClient(
        base_url=config["origin"],
        verify=tls,
        trust_env=False,
        timeout=25,
        headers={"Origin": config["origin"]},
    ) as client:
        client.cookies.set("__Host-XIAOHESESSION", await identity(config, settings, "journey-user"))
        snapshot = (await client.post("/ai-api/v1/session", json={})).json()
        assert snapshot["viewer"]["kind"] == "user"
        conversation = snapshot["conversationId"]
        client.headers[snapshot["csrf"]["headerName"]] = snapshot["csrf"]["token"]
        path = "/ai-api/v1/reception/conversations/" + conversation

        async def view():
            response = await client.get(path)
            assert response.status_code == 200
            return response.json()

        async def idle():
            response = await client.get("/ai-api/v1/conversations/" + conversation)
            if response.status_code != 200:
                return False
            value = response.json()
            return (
                value
                if not value["activeRun"] or value["activeRun"]["status"] == "waiting_input"
                else False
            )

        async def turn(message):
            current = await wait(idle)
            pending = (await view())["pendingRequestId"]
            if current["activeRun"]:
                response = await client.post(
                    "/ai-api/v1/conversations/" + conversation + "/actions",
                    json={"clientActionId": str(uuid4()), "requestId": pending, "text": message},
                )
            else:
                response = await client.post(
                    "/ai-api/v1/conversations/" + conversation + "/messages",
                    json={"clientMessageId": str(uuid4()), "text": message},
                )
            assert response.status_code == 202, response.text
            await wait(idle)
            return await view()

        async def create(direction, fields, **source):
            response = await client.post(
                path + "/drafts",
                json={
                    "clientDraftId": str(uuid4()),
                    "kind": "service",
                    "direction": direction,
                    **source,
                },
            )
            assert response.status_code == 201, response.text
            draft = response.json()
            if fields:
                response = await client.patch(
                    "/ai-api/v1/reception/drafts/" + draft["id"],
                    json={"expectedRevision": draft["revision"], "fields": fields},
                )
                assert response.status_code == 200, response.text
            return (await turn("继续采集资料"))["activeDraft"]

        async def action(kind, key=None):
            current = await view()
            draft = current["activeDraft"]
            body = {
                "clientActionId": key or str(uuid4()),
                "requestId": current["pendingRequestId"],
                "expectedRevision": draft["revision"],
                "action": kind,
            }
            if kind == "confirm":
                body["summaryVersion"] = draft["summaryVersion"]
            response = await client.post(
                "/ai-api/v1/reception/drafts/" + draft["id"] + "/actions", json=body
            )
            assert response.status_code == 202, response.text
            return draft["id"], body

        async def submitted():
            current = await view()
            return (
                current["activeDraft"] if current["activeDraft"]["state"] == "accepted" else False
            )

        def receipt(draft_id):
            with engine.connect() as db:
                value = db.scalar(
                    text(
                        "SELECT receipt FROM xiaohe.reception_submissions WHERE draft_id=:id ORDER BY draft_revision DESC"
                    ),
                    {"id": draft_id},
                )
                assert value and value["status"] == "accepted"
                return value

        async def finish():
            await action("show_summary")
            await wait(idle)
            draft_id, body = await action("confirm")
            await wait(submitted)
            await wait(idle)
            replay = await client.post(
                "/ai-api/v1/reception/drafts/" + draft_id + "/actions", json=body
            )
            assert replay.status_code == 202
            return receipt(draft_id)

        buyer_draft = await create("buyer", {"companyName": "联合甲公司"})
        switched = await turn("切换到供应商")
        assert switched["direction"] == "supplier" and switched["activeDraft"] is None
        resumed = await turn("切换到采购商")
        assert (
            resumed["activeDraft"]["id"] == buyer_draft["id"]
            and resumed["activeDraft"]["fields"]["companyName"] == "联合甲公司"
        )
        first = await finish()
        await create("buyer", {"companyName": "联合甲公司", "requirement": "第二次独立需求"})
        await client.post("/__test/drop-next-submit", headers=control)
        await action("show_summary")
        await wait(idle)
        second_draft, _ = await action("confirm")
        await wait(idle)
        assert (await view())["activeDraft"]["state"] == "submitting"
        await turn("查询提交结果")
        await wait(submitted)
        second = receipt(second_draft)
        assert first["applicationId"] != second["applicationId"]
        await create("buyer", {"companyName": "联合乙公司"})
        await turn("查看汇总")
        assert (await turn("好的"))["activeDraft"]["state"] == "review"
        await turn("确认提交当前汇总")
        await wait(submitted)
        third = receipt((await view())["activeDraft"]["id"])
        await create(
            "buyer",
            {"city": "杭州", "requirement": "第一条更新需求"},
            sourceApplicationId=first["applicationId"],
        )
        edited = await finish()
        assert edited["applicationId"] == first["applicationId"] and edited["revision"] == 2
        organization = {
            "organizationName": "联合行业协会",
            "memberSize": "100—500",
            "coverage": "本省",
            "onlineChannels": ["官网"],
            "contactName": "李先生",
        }
        await create("organization", organization)
        org = await finish()
        assert (await client.get("/__test/counts", headers=control)).json()["co_onboarding"] == 0
        await create(
            "organization", {}, kind="onboarding", sourceApplicationId=org["applicationId"]
        )
        onboarding = await finish()
        assert onboarding["onboardingId"]
        await create("supplier", {"companyName": "联合甲公司", "category": "零部件"})
        current = (await view())["activeDraft"]
        payload = b"%PDF-1.7\nisolated supplier material\n%%EOF\n"
        response = await client.post(
            "/ai-api/v1/reception/drafts/" + current["id"] + "/files",
            json={
                "clientUploadId": str(uuid4()),
                "expectedRevision": current["revision"],
                "category": "product_manual",
                "filename": "联合产品手册.pdf",
                "mediaType": "application/pdf",
                "bytes": len(payload),
            },
        )
        assert response.status_code == 201, response.text
        upload = response.json()
        file_id = upload["file"]["id"]
        assert (
            await client.put(
                upload["uploadPath"], content=payload, headers={"Content-Type": "application/pdf"}
            )
        ).status_code == 200
        supplier = await finish()
        assert (
            await client.get("/ai-api/v1/reception/files/" + file_id + "/download")
        ).content == payload
        response = await client.get(
            "/admin-api/collaborator/reception/applications/"
            + supplier["applicationId"]
            + "/files/"
            + file_id,
            headers=control,
        )
        assert response.status_code == 200 and response.content == payload
        response = await client.post(
            "/admin-api/collaborator/reception/assign",
            headers=control,
            json={
                "applicationId": first["applicationId"],
                "expectedRevision": 2,
                "managerId": "99",
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/admin-api/collaborator/reception/progress",
            headers=control,
            json={
                "applicationId": first["applicationId"],
                "expectedRevision": 3,
                "stage": "communicating",
                "grade": "人工评估A",
                "expectedContactAt": None,
                "visibility": "public",
                "text": "生态经理已沟通需求",
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/admin-api/collaborator/reception/onboarding/review",
            headers=control,
            json={
                "applicationId": org["applicationId"],
                "onboardingId": onboarding["onboardingId"],
                "expectedRevision": 1,
                "stage": "approved",
                "text": "审核通过，等待实际入驻",
            },
        )
        assert response.status_code == 200, response.text
        progress = (await client.get("/ai-api/v1/reception/progress")).json()
        assert len(progress["groups"]) == 3
        records = {
            item["id"]: item for group in progress["groups"] for item in group["applications"]
        }
        assert (
            len(records) == 5
            and records[first["applicationId"]]["manager"]["name"] == "隔离生态经理"
        )
        assert records[first["applicationId"]]["grade"] == "人工评估A"
        assert records[second["applicationId"]]["manager"] is None
        assert records[org["applicationId"]]["onboarding"]["stage"] == "approved"
        counts = (await client.get("/__test/counts", headers=control)).json()
        assert (
            counts["co_subject"] == 3
            and counts["co_application"] == 5
            and counts["co_onboarding"] == 1
        )
        # 已知来源租户2、业务租户9；不透明 sub 不转换为原会员。
        response = await client.post(
            config["java_origin"] + "/internal-api/reception/v1/subjects/query",
            headers={
                "Authorization": "Bearer " + settings.reception_secret.get_secret_value(),
                "X-Reception-Application": "journey-ai",
            },
            json={
                "actor": {
                    "aiUserId": snapshot["viewer"]["userId"],
                    "systemId": "other",
                    "issuer": config["origin"],
                    "sub": "journey-user",
                    "sourceTenantId": "2",
                },
                "payload": {"kind": "company"},
            },
        )
        assert response.status_code == 403
        async with httpx.AsyncClient(
            base_url=config["origin"],
            verify=tls,
            trust_env=False,
            headers={"Origin": config["origin"]},
        ) as stranger:
            stranger.cookies.set(
                "__Host-XIAOHESESSION", await identity(config, settings, "journey-other")
            )
            other = (await stranger.post("/ai-api/v1/session", json={})).json()
            stranger.headers[other["csrf"]["headerName"]] = other["csrf"]["token"]
            assert (
                await stranger.get("/ai-api/v1/reception/files/" + file_id + "/download")
            ).status_code == 403
            assert (await stranger.get("/ai-api/v1/reception/progress")).json()["groups"] == []
            stranger.headers[other["csrf"]["headerName"]] = snapshot["csrf"]["token"]
            assert (await stranger.get("/ai-api/v1/reception/progress")).status_code == 403
        Path(config["directory"], "dataset.json").write_text(
            json.dumps(
                {
                    "first": first,
                    "second": second,
                    "third": third,
                    "organization": org,
                    "supplier": supplier,
                    "fileId": file_id,
                    "counts": counts,
                }
            ),
            "utf-8",
        )
    engine.dispose()


async def test_process_kill_after_java_accept_recovers_original_submission():
    config, settings = configuration()
    directory = Path(config["directory"])
    dataset = json.loads((directory / "dataset.json").read_text("utf-8"))
    application = dataset["first"]["applicationId"]
    tls = ssl.create_default_context(cafile=config["certificate"])
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    async with httpx.AsyncClient(
        base_url=config["origin"],
        verify=tls,
        trust_env=False,
        timeout=25,
        headers={"Origin": config["origin"]},
    ) as client:
        client.cookies.set("__Host-XIAOHESESSION", await identity(config, settings, "journey-user"))
        snapshot = (await client.post("/ai-api/v1/session", json={})).json()
        client.headers[snapshot["csrf"]["headerName"]] = snapshot["csrf"]["token"]
        path = "/ai-api/v1/reception/conversations/" + snapshot["conversationId"]
        created = await client.post(
            path + "/drafts",
            json={
                "clientDraftId": str(uuid4()),
                "kind": "service",
                "direction": "buyer",
                "sourceApplicationId": application,
            },
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        patched = await client.patch(
            "/ai-api/v1/reception/drafts/" + draft["id"],
            json={
                "expectedRevision": draft["revision"],
                "fields": {"requirement": "进程重启后核对的需求"},
            },
        )
        assert patched.status_code == 200
        response = await client.post(
            "/ai-api/v1/conversations/" + snapshot["conversationId"] + "/messages",
            json={"clientMessageId": str(uuid4()), "text": "继续采集资料"},
        )
        assert response.status_code == 202, response.text

        async def current():
            response = await client.get(path)
            return response.json() if response.status_code == 200 else None

        async def has_question():
            value = await current()
            return value if value and value["pendingRequestId"] else False

        await wait(has_question)

        async def act(kind):
            value = await current()
            draft = value["activeDraft"]
            body = {
                "clientActionId": str(uuid4()),
                "requestId": value["pendingRequestId"],
                "expectedRevision": draft["revision"],
                "action": kind,
            }
            if kind == "confirm":
                body["summaryVersion"] = draft["summaryVersion"]
            response = await client.post(
                "/ai-api/v1/reception/drafts/" + draft["id"] + "/actions", json=body
            )
            assert response.status_code == 202, response.text

        await act("show_summary")

        async def reviewed():
            value = await current()
            return value if value and value["activeDraft"]["state"] == "review" else False

        await wait(reviewed)
        control = {"X-Journey-Control": config["control"]}
        assert (await client.post("/__test/hold-next-submit", headers=control)).status_code == 204
        await act("confirm")

        async def held():
            return (directory / "java-accepted").is_file()

        await wait(held)
        with engine.connect() as db:
            assert (
                db.scalar(
                    text("SELECT receipt FROM xiaohe.reception_submissions WHERE draft_id=:id"),
                    {"id": draft["id"]},
                )
                is None
            )
        (directory / "restart-runtime").write_text("kill-owned-runtime", "utf-8")

        async def restarted():
            return (directory / "runtime-restarted").is_file()

        await wait(restarted, 60)

        async def accepted():
            value = await current()
            return value if value and value["activeDraft"]["state"] == "accepted" else False

        await wait(accepted, 210)
        with engine.connect() as db:
            receipt = db.scalar(
                text("SELECT receipt FROM xiaohe.reception_submissions WHERE draft_id=:id"),
                {"id": draft["id"]},
            )
            assert receipt["applicationId"] == application and receipt["revision"] == 5
            assert (
                db.scalar(
                    text("SELECT COUNT(*) FROM xiaohe.reception_submissions WHERE draft_id=:id"),
                    {"id": draft["id"]},
                )
                == 1
            )
        counts = (await client.get("/__test/counts", headers=control)).json()
        assert counts["co_application"] == 5
        (directory / "restart-result.json").write_text(
            json.dumps({"receipt": receipt, "applications": 5, "recoveredAfterProcessKill": True}),
            "utf-8",
        )
    engine.dispose()
