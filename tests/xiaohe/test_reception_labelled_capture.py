from types import SimpleNamespace

import pytest
from test_reception_graph import DecisionModel

from xiaohe.reception.flow import _extract


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["；", "，", "\n"])
async def test_explicit_first_round_labels_are_captured_without_a_provider(separator):
    model = DecisionModel(result={"fields": {}})
    definition = SimpleNamespace(
        rounds=[
            SimpleNamespace(id="round1", fields=["companyName", "industry", "companyScale", "city"])
        ]
    )
    text = separator.join(
        ["企业名称：联调测试企业", "行业：制造业", "企业规模：500人", "城市：杭州"]
    )
    result = await _extract(model, "buyer", "round1", text, definition)
    assert result == {
        "companyName": "联调测试企业",
        "industry": "制造业",
        "companyScale": "500人",
        "city": "杭州",
    }
    assert model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "企业名称：甲公司；企业名称：乙公司",
        "企业名称：甲公司？",
        "不是企业名称：甲公司",
        "预算：100万",
    ],
)
async def test_ambiguous_questions_and_other_round_fields_are_not_captured(text):
    model = DecisionModel(result={"fields": {}})
    definition = SimpleNamespace(
        rounds=[
            SimpleNamespace(id="round1", fields=["companyName", "industry", "companyScale", "city"])
        ]
    )
    assert await _extract(model, "buyer", "round1", text, definition) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "企业名称：甲公司或乙公司",
        "企业名称：暂不提供",
        "城市：还没确定",
        "企业名称：不提供",
        "企业名称：不填写",
        "城市：尚不清楚",
    ],
)
async def test_unconfirmed_labelled_values_are_not_stored(text):
    model = DecisionModel(result={"fields": {}})
    definition = SimpleNamespace(
        rounds=[
            SimpleNamespace(id="round1", fields=["companyName", "industry", "companyScale", "city"])
        ]
    )
    assert await _extract(model, "buyer", "round1", text, definition) == {}
    assert model.requests == []


@pytest.mark.asyncio
async def test_uncertain_city_does_not_discard_confirmed_company_or_invent_a_city():
    definition = SimpleNamespace(
        rounds=[SimpleNamespace(id="round1", fields=["companyName", "city"])]
    )
    assert await _extract(
        None, "buyer", "round1", "企业名称：甲公司；城市：还没确定", definition
    ) == {"companyName": "甲公司"}


@pytest.mark.asyncio
async def test_alternatives_in_a_requirement_remain_the_users_explicit_request():
    definition = SimpleNamespace(rounds=[SimpleNamespace(id="round2", fields=["requirement"])])
    assert await _extract(None, "buyer", "round2", "需求描述：希望比较A或B方案", definition) == {
        "requirement": "希望比较A或B方案"
    }


@pytest.mark.pg
@pytest.mark.asyncio
async def test_chat_capture_updates_current_draft_without_advancing_or_submitting(
    pg_factory, runtime_env, monkeypatch
):
    from test_admission import visitor
    from test_reception_flow import install, talk

    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft

    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await visitor(pg_factory)
    await talk(pg_factory, runtime_env, who, conversation, "采购商")
    async with pg_factory.begin() as db:
        await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="chat-capture", kind="service", direction="buyer"),
        )
    _, view = await talk(
        pg_factory,
        runtime_env,
        who,
        conversation,
        "企业名称：聊天同步测试企业；行业：制造业；企业规模：500人；城市：杭州",
    )
    assert view.active_draft.fields == {
        "companyName": "聊天同步测试企业",
        "industry": "制造业",
        "companyScale": "500人",
        "city": "杭州",
    }
    assert view.active_draft.step == "round1" and view.active_draft.state == "collecting"
    assert view.active_draft.summary_version is None and view.pending_request_id
    for text in (
        "企业名称：甲公司或乙公司",
        "企业名称：暂不提供",
        "城市：还没确定",
        "企业名称：不提供",
        "企业名称：不填写",
        "城市：尚不清楚",
    ):
        _, current = await talk(pg_factory, runtime_env, who, conversation, text)
        assert current.active_draft.fields == view.active_draft.fields
        assert current.active_draft.revision == view.active_draft.revision
