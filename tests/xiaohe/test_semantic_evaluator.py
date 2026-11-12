import pytest
from pydantic import ValidationError

from scripts.evaluate_xiaohe_semantics import (
    BudgetExhausted,
    EvaluatedCase,
    EvaluationBudget,
    LimitedModel,
    SemanticCase,
    evaluate_cases,
    finite_model,
    require_live_enabled,
)


@pytest.mark.parametrize(
    "enabled,model,cap", [(False, "model", 2), (True, "", 2), (True, "model", 0)]
)
def test_evaluator_never_calls_model_without_enable_and_finite_limits(enabled, model, cap):
    with pytest.raises((ValueError, ValidationError)):
        budget = EvaluationBudget(max_calls=cap, max_output_tokens=100)
        require_live_enabled(enabled, model, budget)


@pytest.mark.asyncio
async def test_budget_exhaustion_is_incomplete_not_pass():
    budget = EvaluationBudget(max_calls=2, max_output_tokens=100)
    cases = [
        SemanticCase(
            id=f"case-{i}",
            agent_id="xiaohe-buyer",
            text="如何收费",
            expected_actions=["consultation"],
        )
        for i in range(3)
    ]

    async def runner(case, budget):
        budget.use_call()
        budget.use_call()
        return EvaluatedCase(id=case.id, status="evaluated", passed=True)

    report = await evaluate_cases(cases, runner, budget)
    assert report.status == "incomplete" and report.not_run == ["case-1", "case-2"]
    assert report.model_calls == 2 <= budget.max_calls


@pytest.mark.asyncio
async def test_failed_expectation_is_not_hidden_by_other_success():
    budget = EvaluationBudget(max_calls=3, max_output_tokens=100)
    cases = [
        SemanticCase(id="one", agent_id="xiaohe-buyer", text="别提交", expected_actions=["unclear"])
    ]

    async def runner(case, budget):
        budget.use_call()
        return EvaluatedCase(
            id=case.id, status="evaluated", passed=False, failures=["unexpected submission"]
        )

    report = await evaluate_cases(cases, runner, budget)
    assert report.status == "fail" and report.failed == ["one"]


def test_live_client_disables_sdk_retries_and_applies_output_limit():
    from langchain_openai import ChatOpenAI

    original = ChatOpenAI(model="fixture-model", api_key="fixture-only")
    bounded = finite_model(original, EvaluationBudget(max_calls=1, max_output_tokens=123))
    assert bounded.max_tokens == 123 and bounded.max_retries == 0 and not bounded.streaming
    assert bounded.root_client.max_retries == bounded.root_async_client.max_retries == 0


@pytest.mark.asyncio
async def test_model_wrapper_checks_budget_before_each_call():
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    calls = []

    class FakeRawModel:
        def with_structured_output(self, schema, **kwargs):
            async def respond(messages):
                calls.append(messages)
                return {
                    "parsed": {"action": "unclear"},
                    "raw": AIMessage(
                        content="",
                        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    ),
                }

            return RunnableLambda(respond)

    budget = EvaluationBudget(max_calls=1, max_output_tokens=123)
    wrapped = LimitedModel(FakeRawModel(), budget).with_structured_output(dict)
    await wrapped.ainvoke([])
    with pytest.raises(BudgetExhausted):
        await wrapped.ainvoke([])
    assert len(calls) == budget.calls == 1
