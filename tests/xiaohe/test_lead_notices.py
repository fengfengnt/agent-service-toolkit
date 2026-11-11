import importlib

import pytest
from test_lead_skills import skills


def renderer():
    assert importlib.util.find_spec("xiaohe.lead_skills.notices") is not None, (
        "critical notice renderer missing"
    )
    return importlib.import_module("xiaohe.lead_skills.notices").render_lead_notice


@pytest.mark.parametrize(
    "notice,kwargs,expected",
    [
        ("prepare", {}, "请在资料面板填写本次采购资料。"),
        (
            "missing",
            {"missing_labels": ["企业名称", "联系电话"]},
            "正式提交还需：企业名称、联系电话。",
        ),
        ("saved", {}, "本次资料已保存，尚未正式提交。"),
        ("submitting", {}, "正在提交本次资料。"),
        ("unknown", {}, "正在核实提交结果，请稍后查询，无需重复提交。"),
        ("accepted", {}, "本次需求已受理，可在“我的进展”查看状态。"),
        ("failed", {"public_guidance": "请核对资料。"}, "本次提交未完成。请核对资料。"),
    ],
)
def test_critical_notice_uses_real_state(notice, kwargs, expected):
    assert renderer()(skills().interaction_policy, notice, direction="buyer", **kwargs) == expected


def test_notice_rejects_unknown_category_and_fake_missing():
    render = renderer()
    for kind in ("approved", "paid", "missing"):
        with pytest.raises(ValueError):
            render(skills().interaction_policy, kind, direction="buyer")


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_conversion_answers_then_prepares_role_form(pg_factory, job_app, tmp_path, role):
    from test_agent_operations import cache_decision
    from test_agent_semantics import ScriptedModel
    from test_lead_submission import lead_prepare

    from xiaohe.agents.contracts import SemanticDecision
    from xiaohe.agents.nodes import make_node_executors
    from xiaohe.workflows.models import OperationNode

    bundle, frame = await lead_prepare(pg_factory, tmp_path, role=role)
    decision = SemanticDecision(action="accept_invitation")
    await cache_decision(pg_factory, frame, decision)
    model = ScriptedModel(
        result={},
        responses={
            "AnswerDraft": {
                "text": "可以先从当前业务需求讨论。",
                "basis": "general_advice",
                "source_ids": [],
            }
        },
    )
    nodes = make_node_executors(pg_factory, model=model, business_client=None, bundle=bundle)
    result = await nodes["operation"](
        OperationNode(
            id="auth", name="准备资料", type="operation", operation="request_auth", next="done"
        ),
        {"frame": frame.model_dump(), "decision": decision.model_dump()},
        {},
    )
    assert result["answer"]["text"] == "可以先从当前业务需求讨论。"
    expected_role = {"buyer": "采购", "organization": "组织机构", "supplier": "供应商"}[role]
    assert result["operation_result"]["text"] == f"请在资料面板填写本次{expected_role}资料。"
    assert result["operation_result"]["collection"] is True
    assert not result["operation_result"].get("auth")


@pytest.mark.pg
@pytest.mark.asyncio
async def test_lost_submit_reply_stays_unknown_until_receipt(pg_factory, job_app, tmp_path):
    from test_lead_submission import Business, lead_prepare, submit

    from xiaohe.contracts import ServiceError
    from xiaohe.lead_skills.notices import render_lead_notice

    bundle, frame = await lead_prepare(pg_factory, tmp_path)
    client = Business(lose_reply=True)
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit(pg_factory, bundle, frame, client)
    assert "无需重复提交" in render_lead_notice(
        frame.interaction_policy, "unknown", direction="buyer"
    )
    result = await submit(pg_factory, bundle, frame, client)
    assert result["operation_result"]["text"] == "本次需求已受理，可在“我的进展”查看状态。"
    assert len(client.payloads) == 1


@pytest.mark.asyncio
async def test_model_cannot_replace_real_business_status(tmp_path):
    from test_agent_semantics import ScriptedModel, prepared

    from xiaohe.agents.contracts import SemanticDecision
    from xiaohe.agents.semantic import compose_answer
    from xiaohe.contracts import ServiceError

    _, _, frame = prepared(tmp_path)
    frame = frame.model_copy(update={"interaction_policy": skills().interaction_policy})
    model = ScriptedModel(
        result={},
        responses={
            "AnswerDraft": {
                "text": "您的需求已受理，已经安排专员。",
                "basis": "general_advice",
                "source_ids": [],
            }
        },
    )
    with pytest.raises(ServiceError):
        await compose_answer(model, frame, SemanticDecision(action="consultation"), [], "答疑")


@pytest.mark.pg
@pytest.mark.asyncio
async def test_login_is_not_submission_consent(pg_factory, job_app, tmp_path):
    from sqlalchemy import select
    from test_agent_operations import cache_decision
    from test_lead_submission import lead_prepare

    from xiaohe.agents.contracts import SemanticDecision
    from xiaohe.agents.nodes import make_node_executors
    from xiaohe.models import Command
    from xiaohe.reception.models import ReceptionSubmission
    from xiaohe.workflows.models import OperationNode

    bundle, frame = await lead_prepare(pg_factory, tmp_path)
    decision = SemanticDecision(action="accept_invitation")
    await cache_decision(pg_factory, frame, decision)
    async with pg_factory.begin() as db:
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {"receptionControl": "accept_invitation"}
    nodes = make_node_executors(pg_factory, model=None, business_client=None, bundle=bundle)
    result = await nodes["operation"](
        OperationNode(
            id="auth", name="登录后续", type="operation", operation="request_auth", next="done"
        ),
        {"frame": frame.model_dump(), "decision": decision.model_dump()},
        {},
    )
    assert result["operation_result"]["collection"] is True
    async with pg_factory() as db:
        assert await db.scalar(select(ReceptionSubmission)) is None
