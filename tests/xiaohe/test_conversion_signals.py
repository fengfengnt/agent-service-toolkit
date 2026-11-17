"""显式启用的真实模型回归；只理解虚构对话，不访问业务数据库。"""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from xiaohe.agents.contracts import AgentFrame, ModelContext
from xiaohe.agents.semantic import understand_turn
from xiaohe.contracts import Actor, TrustedSystem, WriteFence
from xiaohe.workflows.loader import load_bundle, workflow_for

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.environ.get("XIAOHE_CONVERSION_EVAL") != "1",
        reason="真实模型回归需显式设置 XIAOHE_CONVERSION_EVAL=1",
    ),
]


@pytest.fixture(scope="module")
def conversion_model():
    from scripts.evaluate_xiaohe_semantics import EvaluationBudget, finite_model
    from xiaohe.graph import reception_model

    return finite_model(reception_model(), EvaluationBudget(max_calls=9, max_output_tokens=1024))


@pytest.mark.parametrize(
    "direction,text,conversion",
    [
        ("buyer", "合作", True),
        ("organization", "合作", True),
        ("supplier", "合作", True),
        ("supplier", "请安排顾问联系我聊聊合作", True),
        ("organization", "我想预约你们的方案演示", True),
        ("supplier", "暂不合作，先不用登记", False),
        ("buyer", "你们的合作流程是什么？我只是了解一下，不登记", False),
        ("organization", "客户说他想合作，不是我要求合作或登记", False),
        ("buyer", "如果以后有预算再考虑合作，现在先不登记", False),
    ],
    ids=[
        "buyer-cooperation",
        "organization-cooperation",
        "supplier-cooperation",
        "contact",
        "demo",
        "declined",
        "inquiry",
        "quoted",
        "conditional",
    ],
)
async def test_conversion_signal_uses_collection_action(
    conversion_model, direction, text, conversion
):
    bundle = load_bundle(Path("resources/xiaohe"))
    definition = workflow_for(bundle, "xiaohe-" + direction)
    frame = AgentFrame(
        fence=WriteFence(run_id=uuid4(), attempt=1, ownership_epoch=0),
        command_id=uuid4(),
        message_id=uuid4(),
        conversation_id=uuid4(),
        agent_id="xiaohe-" + direction,
        bundle_id=bundle.bundle_id,
        actor=Actor(
            system=TrustedSystem(
                system_id="fixture",
                tenant_id=1,
                portal_client_id="fixture",
                allowed_origin="https://fixture.example",
            ),
            session_id=uuid4(),
            guest_id=uuid4(),
            user_id=None,
        ),
        model_context=ModelContext(
            text=text, direction=direction, eligible_topics=definition.skills
        ),
    )
    decision = await understand_turn(conversion_model, frame, definition, bundle)
    assert (decision.action == "accept_invitation") is conversion
    if conversion:
        assert not (
            decision.negated
            or decision.deferred
            or decision.conditional
            or decision.needs_clarification
        )
    assert decision.action != "confirm_current_summary"
    assert not decision.candidates
