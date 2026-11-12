import pytest
from test_http_api import client_for, session

from xiaohe.reception.api import create_reception_router

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_draft_routes_share_auth_csrf_and_safe_errors(pg_factory):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        path = "/ai-api/v1/reception/conversations/" + snapshot["conversationId"]
        create = await client.post(
            path + "/drafts", json={"clientDraftId": "one", "kind": "service", "direction": "buyer"}
        )
        assert create.status_code == 201, create.text
        draft = create.json()
        assert "sourceApplicationId" not in draft and draft["missingRequired"] == [
            "companyName",
            "contactName",
            "contactPhone",
        ]
        endpoint = "/ai-api/v1/reception/drafts/" + draft["id"]
        result = await client.patch(
            endpoint, json={"expectedRevision": 1, "fields": {"companyName": "甲公司"}}
        )
        assert result.status_code == 200 and result.json()["step"] == "round1"
        result = await client.patch(
            endpoint, json={"expectedRevision": 1, "fields": {"companyName": "旧资料"}}
        )
        assert result.status_code == 409 and result.json()["error"]["code"] == "DRAFT_CONFLICT"
        result = await client.patch(
            endpoint, json={"expectedRevision": 2, "fields": {}, "userId": "forged-secret"}
        )
        assert result.status_code == 422 and "forged-secret" not in result.text
        del client.headers[snapshot["csrf"]["headerName"]]
        assert (
            await client.patch(endpoint, json={"expectedRevision": 2, "fields": {}})
        ).status_code == 403
        view = await client.get(path)
        assert view.status_code == 200 and view.headers["cache-control"] == "private, no-store"
        assert view.json()["activeDraft"]["fields"] == {"companyName": "甲公司"}
        client.cookies.clear()
        assert (await client.get(path)).status_code == 401


async def test_valid_multifield_unicode_round_fits_reception_body_budget(pg_factory):
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        result = await client.post(
            "/ai-api/v1/reception/conversations/" + snapshot["conversationId"] + "/drafts",
            json={"clientDraftId": "long", "kind": "service", "direction": "buyer"},
        )
        fields = {
            key: "中文" * 2000
            for key in (
                "category",
                "requirement",
                "annualPurchase",
                "painPoints",
                "contactName",
                "contactRole",
            )
        }
        saved = await client.patch(
            "/ai-api/v1/reception/drafts/" + result.json()["id"],
            json={"expectedRevision": 1, "fields": fields},
        )
        assert saved.status_code == 200
        assert saved.json()["fields"] == fields


async def test_invitation_control_records_system_notice_not_user_speech(
    pg_factory, runtime_env, monkeypatch
):
    from uuid import UUID

    from sqlalchemy import select, update
    from test_identity_sessions import actor
    from test_reception_flow import install, talk

    from xiaohe.models import GuestBudget, Message, Session
    from xiaohe.reception.store import load_reception

    await install(pg_factory, runtime_env, monkeypatch)
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        snapshot = await session(client)
        who = await actor(pg_factory, client.cookies.get("__Host-XIAOHESESSION"))
        conversation = UUID(snapshot["conversationId"])
        await talk(pg_factory, runtime_env, who, conversation, "采购商")
        await talk(pg_factory, runtime_env, who, conversation, "多少钱")
        from xiaohe.reception.store import flow_record

        async with pg_factory.begin() as db:
            (await flow_record(db, who, conversation)).invitation_state = "offered"
        async with pg_factory.begin() as db:
            await db.execute(
                update(Session).where(Session.id == who.session_id).values(message_count=100)
            )
            await db.execute(
                update(GuestBudget)
                .where(
                    GuestBudget.id
                    == select(Session.budget_id)
                    .where(Session.id == who.session_id)
                    .scalar_subquery()
                )
                .values(message_count=100)
            )
        path = f"/ai-api/v1/reception/conversations/{conversation}/controls"
        body = {"clientActionId": "accept-control", "action": "accept_invitation"}
        response = await client.post(path, json=body)
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["message"]["role"] == "assistant"
        assert accepted["message"]["parts"] == [{"type": "system_notice", "text": "开始注册或登录"}]
        await runtime_env.worker.execute_run(UUID(accepted["run"]["id"]))
        repeated = await client.post(path, json=body)
        assert (
            await client.post(path, json=body | {"clientActionId": "duplicate-invitation"})
        ).status_code == 409
        assert (
            repeated.status_code == 202
            and repeated.json()["message"]["id"] == accepted["message"]["id"]
        )
        async with pg_factory() as db:
            view = await load_reception(db, who, conversation)
            assert view.invitation_state == "accepted"
            users = (
                await db.scalars(
                    select(Message).where(
                        Message.conversation_id == conversation, Message.role == "user"
                    )
                )
            ).all()
            assert [message.parts[0]["text"] for message in users] == ["采购商", "多少钱"]
            assert (await db.get(Session, who.session_id)).message_count == 100
        assert (
            await client.post(path, json={"clientActionId": "forged", "action": "start_collection"})
        ).status_code == 401
        assert (await client.post(path, json=body | {"text": "假冒用户"})).status_code == 422
        del client.headers[snapshot["csrf"]["headerName"]]
        assert (await client.post(path, json=body)).status_code == 403


async def test_collection_control_keeps_real_user_text_and_creates_one_draft(
    pg_factory, runtime_env, monkeypatch
):
    from uuid import UUID

    from sqlalchemy import select
    from test_identity_sessions import actor, login
    from test_reception_flow import install, talk

    from xiaohe.models import Message
    from xiaohe.reception.store import flow_record, load_reception

    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    async with client_for(pg_factory) as (client, app):
        app.include_router(create_reception_router(pg_factory))
        client.cookies.set("__Host-XIAOHESESSION", token)
        snapshot = await session(client)
        who = await actor(pg_factory, token)
        conversation = UUID(snapshot["conversationId"])
        await talk(pg_factory, runtime_env, who, conversation, "采购商")
        async with pg_factory.begin() as db:
            (await flow_record(db, who, conversation)).invitation_state = "accepted"
        body = {"clientActionId": "collection-control", "action": "start_collection"}
        response = await client.post(
            f"/ai-api/v1/reception/conversations/{conversation}/controls", json=body
        )
        assert response.status_code == 202
        assert response.json()["message"]["parts"] == [
            {"type": "system_notice", "text": "采集信息"}
        ]
        await runtime_env.worker.execute_run(UUID(response.json()["run"]["id"]))
        async with pg_factory() as db:
            view = await load_reception(db, who, conversation)
            assert len(view.draft_refs) == 1 and view.active_draft.step == "round1"
            users = (
                await db.scalars(
                    select(Message).where(
                        Message.conversation_id == conversation, Message.role == "user"
                    )
                )
            ).all()
            assert [message.parts[0]["text"] for message in users] == ["采购商"]
