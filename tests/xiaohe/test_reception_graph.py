from datetime import UTC, datetime

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableLambda

from xiaohe.contracts import AgentOutcome, ChatMessage, ReceptionContext, ServiceError
from xiaohe.graph import generate_reception_decision, reception_outcome
from xiaohe.professional import dispatch_direction


class DecisionModel(BaseChatModel):
    result: dict
    requests: list = []

    @property
    def _llm_type(self):
        return "test-only-structured-model"

    def _generate(self, *args, **kwargs):
        raise AssertionError("no provider call in fixture")

    def with_structured_output(self, schema, **kwargs):
        async def answer(messages):
            self.requests.append(messages)
            return self.result

        return RunnableLambda(answer)


def context(text, direction="unknown", **changes):
    return ReceptionContext(
        recent_messages=[
            ChatMessage(
                id="m1",
                role="user",
                created_at=datetime.now(UTC),
                parts=[{"type": "text", "text": text}],
            )
        ],
        direction=direction,
        pending_request=None,
        confirmed_facts={},
        **changes,
    )


def model(direction="supplier", intent="reception", answer="可以介绍相关服务方向。", **extra):
    return DecisionModel(
        result={"direction": direction, "intent": intent, "answer": answer, **extra}
    )


@pytest.mark.asyncio
async def test_explicit_direction_wins():
    decision = await generate_reception_decision(
        context("我代表供应商，想了解服务"), model("buyer")
    )
    assert decision.direction == "supplier" and decision.target_direction == "supplier"
    temporary = await generate_reception_decision(
        context("采购方通常怎样发布需求？", "supplier"), model("buyer")
    )
    assert temporary.direction == "supplier" and temporary.target_direction == "buyer"
    switch = await generate_reception_decision(
        context("切换到协会组织方向", "supplier"), model("supplier")
    )
    assert switch.direction == "organization"


@pytest.mark.asyncio
async def test_negated_switch_does_not_change_explicit_direction():
    decision = await generate_reception_decision(
        context("不要切换到采购方，保持当前方向", "supplier"), model("buyer")
    )
    assert decision.direction == "supplier"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer", ["注册成功，已经提交人工处理", "请输入手机号和短信验证码", "免费交付，保证三天完成"]
)
async def test_model_cannot_claim_success_or_ask_for_auth_secrets(answer):
    result = await reception_outcome(context("介绍服务"), {}, model(answer=answer), {})
    assert answer not in result.model_dump_json()


@pytest.mark.asyncio
async def test_ambiguous_and_off_topic():
    unclear = await reception_outcome(context("帮我看看"), {}, model("unknown", "clarify"), {})
    assert unclear.wait_for_input and unclear.parts[0].type == "choice"
    assert {option.id for option in unclear.parts[0].options} == {
        "buyer",
        "organization",
        "supplier",
    }
    unrelated = await reception_outcome(
        context("写一份数学试卷"), {}, model("unknown", "off_topic", "不受控原文"), {}
    )
    assert "不受控原文" not in unrelated.model_dump_json()
    assert unrelated.wait_for_input is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["不要登录", "我不想留资", "暂时不注册", "如何关闭登录提醒", "以后再登录", "请不要建议注册"],
)
async def test_only_user_auth_intent_creates_auth_part(text):
    outcome = await reception_outcome(context(text), {}, model(answer="请点击登录"), {})
    assert not [part for part in outcome.parts if part.type == "auth_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,intent", [("我要留资", "lead"), ("我想注册", "register"), ("请帮我登录", "login")]
)
async def test_positive_auth_intent_returns_card_only(text, intent):
    outcome = await reception_outcome(context(text), {}, model(), {})
    assert [part.intent for part in outcome.parts if part.type == "auth_request"] == [intent]
    assert "成功" not in outcome.model_dump_json()


@pytest.mark.asyncio
async def test_professional_dispatch_and_unavailable_are_honest():
    called = []

    async def supplier(state):
        called.append(state["direction"])
        return AgentOutcome(
            parts=[{"type": "text", "text": "受信专业处理"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    state = {"direction": "supplier"}
    outcome = await dispatch_direction("supplier", state, {"supplier": supplier})
    assert called == ["supplier"] and outcome.parts[0].text == "受信专业处理"
    unavailable = await dispatch_direction("buyer", state, {"supplier": supplier})
    assert "尚未开放" in unavailable.parts[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        {"direction": "root", "intent": "reception", "answer": "x"},
        {"direction": "supplier", "intent": "reception", "answer": "x", "tool": "delete"},
        {"direction": "supplier", "intent": "reception", "answer": "x" * 4001},
        {"direction": "supplier", "intent": "saved", "answer": "已保存"},
    ],
)
async def test_invalid_structured_output_is_safe_failure(value):
    with pytest.raises(ServiceError) as error:
        await generate_reception_decision(context("介绍服务"), DecisionModel(result=value))
    assert error.value.code == "UPSTREAM_UNAVAILABLE" and error.value.__context__ is None


def test_fake_or_unconfigured_product_model_is_unavailable(monkeypatch):
    from core.settings import settings
    from schema.models import FakeModelName
    from xiaohe.graph import reception_model

    for value in (None, FakeModelName.FAKE):
        monkeypatch.setattr(settings, "DEFAULT_MODEL", value)
        with pytest.raises(ServiceError):
            reception_model()


@pytest.mark.asyncio
async def test_model_timeout_is_safe_failure(monkeypatch):
    import asyncio

    import xiaohe.graph as graph

    class SlowModel(DecisionModel):
        def with_structured_output(self, schema, **kwargs):
            async def slow(messages):
                await asyncio.Event().wait()

            return RunnableLambda(slow)

    monkeypatch.setattr(graph, "_MODEL_TIMEOUT", 0.01)
    with pytest.raises(ServiceError) as error:
        await generate_reception_decision(context("介绍服务"), SlowModel(result={}))
    assert error.value.code == "UPSTREAM_UNAVAILABLE" and error.value.__context__ is None
