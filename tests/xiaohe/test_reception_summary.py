from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import install, talk

from xiaohe.reception.models import ReceptionSubmission
from xiaohe.reception.store import patch_draft


@pytest.mark.pg
@pytest.mark.asyncio
async def test_changed_summary_and_plain_ok_cannot_authorize_submission(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    await talk(pg_factory, runtime_env, who, conversation, "供应商")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    async with pg_factory.begin() as db:
        await patch_draft(
            db,
            who,
            view.active_draft.id,
            view.active_draft.revision,
            {"companyName": "甲公司", "contactName": "测试联系人", "contactPhone": "总台分机"},
        )
    _, summary = await talk(pg_factory, runtime_env, who, conversation, "查看汇总")
    assert summary.active_draft.state == "review"
    _, view = await talk(pg_factory, runtime_env, who, conversation, "好的")
    assert view.active_draft.state == "review"
    async with pg_factory.begin() as db:
        await patch_draft(
            db, who, view.active_draft.id, view.active_draft.revision, {"companyName": "乙公司"}
        )
    _, view = await talk(pg_factory, runtime_env, who, conversation, "确认提交")
    assert view.active_draft.state == "collecting" and view.active_draft.summary_version is None
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReceptionSubmission)) == 0
