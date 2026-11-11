import pytest
from test_deep_tools import tool_context

from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime import forms
from xiaohe.reception.models import ReceptionDraft

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
GROUPS = [["contactName"], ["companyName"], ["city", "industry"], ["contactPhone"]]


async def test_four_groups_keep_old_field_rules(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path)
    view = await forms.compose_form(context, GROUPS, prompt="请先核对联系人，再补充公司资料。")
    assert len(view.groups) == 4
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        policy_id = row.field_policy_id
        assert policy_id == frame.field_policy.policy_id
        projected = await forms.lead_policy_v2(db, context.actor, row.id)
        assert projected.groups == view.groups and row.field_policy_id == policy_id
        assert projected.required_fields == ["companyName", "contactName", "contactPhone"]
    async with pg_factory.begin() as db:
        result = await forms.patch_form(
            db,
            context.actor,
            frame.draft_id,
            form_id=view.form_id,
            expected_revision=view.draft_revision,
            fields={"city": "苏州"},
        )
        assert result.fields["companyName"] == "虚构甲公司"
    recomposed = await forms.compose_form(context, GROUPS, prompt="请先核对联系人，再补充公司资料。")
    assert recomposed.draft_revision == view.draft_revision + 1
    assert recomposed.form_id != view.form_id
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
            await forms.patch_form(
                db,
                context.actor,
                frame.draft_id,
                form_id=view.form_id,
                expected_revision=view.draft_revision,
                fields={"city": "无锡"},
            )


@pytest.mark.parametrize("groups", [[["stage"]], [["city"], ["city"]], [[]]])
async def test_form_rejects_uneditable_or_duplicate_fields(pg_factory, job_app, tmp_path, groups):
    context, _ = await tool_context(pg_factory, tmp_path)
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        await forms.compose_form(context, groups, prompt="test")


async def test_form_id_and_display_field_boundary(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path)
    view = await forms.compose_form(context, [["city"]], prompt="城市")
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="ACTION_STALE"):
            await forms.patch_form(
                db,
                context.actor,
                frame.draft_id,
                form_id="guessed",
                expected_revision=frame.draft_revision,
                fields={"city": "苏州"},
            )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="INPUT_INVALID"):
            await forms.patch_form(
                db,
                context.actor,
                frame.draft_id,
                form_id=view.form_id,
                expected_revision=frame.draft_revision,
                fields={"companyName": "越过展示组合"},
            )
