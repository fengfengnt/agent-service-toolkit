"""在显式隔离环境评测四个 Agent；费用授权不等于无限调用。"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xiaohe.agents.contracts import VisibleTurn  # noqa: E402
from xiaohe.reception.contracts import FieldValue  # noqa: E402
from xiaohe.workflows.models import AgentId, RouteEvent  # noqa: E402


class SemanticCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    agent_id: AgentId
    text: str = Field(min_length=1, max_length=10000)
    expected_actions: list[RouteEvent] = Field(min_length=1)
    forbidden_actions: list[RouteEvent] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)
    expected_target: Literal["buyer", "organization", "supplier"] | None = None
    expected_direction: Literal["unknown", "buyer", "organization", "supplier"] | None = None
    required_flags: dict[str, bool] = Field(default_factory=dict)
    recent_turns: list[VisibleTurn] = Field(default_factory=list)
    with_draft: bool = False
    summary_visible: bool = False
    initial_fields: dict[str, FieldValue] = Field(default_factory=dict)
    expected_fields: dict[str, FieldValue] = Field(default_factory=dict)
    forbid_field_changes: bool = False
    expect_submission: bool = False
    expect_intent: bool = False
    expect_onboarding: bool = False
    expect_professional_answer: bool = False
    expected_invitation_state: Literal["none", "offered", "deferred", "accepted"] | None = None
    source_application: bool = False
    invitation_state: Literal["none", "offered", "deferred", "accepted"] = "none"


class BudgetExhausted(RuntimeError):
    pass


class EvaluationBudget(BaseModel):
    max_calls: int = Field(strict=True, gt=0)
    max_output_tokens: int = Field(strict=True, gt=0)
    _calls: int = PrivateAttr(default=0)
    exhausted: bool = False

    @property
    def calls(self):
        return self._calls

    def use_call(self):
        if self._calls >= self.max_calls:
            self.exhausted = True
            raise BudgetExhausted("finite model call limit reached")
        self._calls += 1


class EvaluatedCase(BaseModel):
    id: str
    status: Literal["evaluated", "not_run"]
    passed: bool = False
    failures: list[str] = Field(default_factory=list)
    decision: dict | None = None
    outcome: dict | None = None
    effects: dict = Field(default_factory=dict)
    elapsed_seconds: float = 0


class EvaluationReport(BaseModel):
    status: Literal["pass", "fail", "incomplete"]
    model_calls: int
    evaluated: list[EvaluatedCase]
    failed: list[str]
    not_run: list[str]


SemanticRunner = Callable[[SemanticCase, EvaluationBudget], Awaitable[EvaluatedCase]]


def require_live_enabled(enabled: bool, model: str, budget: EvaluationBudget):
    if not enabled or not model or budget.max_calls <= 0 or budget.max_output_tokens <= 0:
        raise ValueError(
            "live evaluation requires explicit enable, configured model and finite limits"
        )


async def evaluate_cases(cases, runner, budget):
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("evaluation requires nonempty, uniquely identified cases")
    results = []
    for case in cases:
        if budget.calls >= budget.max_calls:
            results.append(EvaluatedCase(id=case.id, status="not_run", failures=["call limit"]))
            continue
        try:
            result = EvaluatedCase.model_validate(await runner(case, budget))
        except Exception as error:
            result = EvaluatedCase(
                id=case.id,
                status="not_run" if budget.exhausted else "evaluated",
                failures=[
                    "call limit"
                    if budget.exhausted
                    else getattr(error, "code", type(error).__name__)
                ],
            )
        results.append(result)
    failed = [item.id for item in results if item.status == "evaluated" and not item.passed]
    not_run = [item.id for item in results if item.status == "not_run"]
    return EvaluationReport(
        status="fail" if failed else "incomplete" if not_run else "pass",
        model_calls=budget.calls,
        evaluated=results,
        failed=failed,
        not_run=not_run,
    )


class LimitedModel:
    """统计每次真实模型调用；复用原 LangChain 客户端，不增加 HTTP 实现。"""

    def __init__(self, model, budget):
        self.model, self.budget = model, budget
        self.usage = []
        self.attempts = []

    def with_structured_output(self, schema, **kwargs):
        from langchain_core.runnables import RunnableLambda

        runnable = self.model.with_structured_output(schema, include_raw=True, **kwargs)

        async def invoke(messages, config):
            self.budget.use_call()
            call = self.budget.calls
            started = time.perf_counter()
            try:
                raw = await runnable.ainvoke(messages, config=config)
            except BaseException as error:
                self.attempts.append(
                    {
                        "call": call,
                        "schema": schema.__name__,
                        "failure_type": type(error).__name__,
                        "http_status": getattr(error, "status_code", None),
                        "seconds": round(time.perf_counter() - started, 3),
                    }
                )
                raise
            message = raw.get("raw")
            usage = getattr(message, "usage_metadata", None) or {}
            self.usage.append(
                {
                    "seconds": round(time.perf_counter() - started, 3),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
            )
            if raw.get("parsing_error") is not None or raw.get("parsed") is None:
                self.attempts.append(
                    {
                        "call": call,
                        "schema": schema.__name__,
                        "failure_type": type(raw["parsing_error"]).__name__
                        if raw.get("parsing_error")
                        else "EmptyStructuredResult",
                        "seconds": round(time.perf_counter() - started, 3),
                    }
                )
                raise ValueError("structured result unavailable")
            parsed = raw["parsed"]
            self.attempts.append(
                {
                    "call": call,
                    "schema": schema.__name__,
                    "untrusted_structured_result": parsed.model_dump(mode="json")
                    if hasattr(parsed, "model_dump")
                    else parsed,
                }
            )
            return raw["parsed"]

        return RunnableLambda(invoke)


def finite_model(model, budget):
    from langchain_openai import ChatOpenAI

    if not isinstance(model, ChatOpenAI):
        raise ValueError("this evaluator currently verifies finite OpenAI-compatible clients only")
    synchronous = model.root_client.with_options(max_retries=0)
    asynchronous = model.root_async_client.with_options(max_retries=0)
    return model.model_copy(
        update={
            "max_retries": 0,
            "max_tokens": budget.max_output_tokens,
            "streaming": False,
            "root_client": synchronous,
            "root_async_client": asynchronous,
            "client": synchronous.chat.completions,
            "async_client": asynchronous.chat.completions,
        }
    )


def load_cases(path):
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("case fixture exceeds size limit")
    cases = TypeAdapter(list[SemanticCase]).validate_python(yaml.safe_load(raw))
    if len(cases) != 32 or any(
        sum(case.agent_id == agent for case in cases) < 8
        for agent in ("xiaohe-reception", "xiaohe-buyer", "xiaohe-organization", "xiaohe-supplier")
    ):
        raise ValueError(
            "frozen acceptance corpus must contain eight cases for each of four agents"
        )
    return cases


async def main(args):
    if os.environ.get("XIAOHE_SEMANTIC_EVAL_ENABLED") != "1":
        raise ValueError("explicit semantic evaluation enable required")
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    budget = EvaluationBudget(max_calls=args.max_calls, max_output_tokens=args.max_output_tokens)
    cases = load_cases(args.cases)
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from xiaohe.database import create_database_engine
    from xiaohe.graph import reception_model

    url = os.environ.get("XIAOHE_TEST_DATABASE_URL", "")
    parsed = make_url(url)
    if parsed.host != "127.0.0.1" or not (parsed.database or "").startswith("test_xiaohe_"):
        raise ValueError("explicit loopback test database required")
    model = reception_model()
    require_live_enabled(True, model.model_name, budget)
    limited = LimitedModel(finite_model(model, budget), budget)
    engine = create_database_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sys.path.insert(0, str(ROOT / "tests/xiaohe"))
    from semantic_runtime_support import build_runner

    try:
        runner, bundle = await build_runner(factory, limited)
        metadata = {
            "model": model.model_name,
            "provider": "OpenAI-compatible",
            "bundle_id": bundle.bundle_id,
            "max_calls": budget.max_calls,
            "max_output_tokens": budget.max_output_tokens,
            "sdk_max_retries": 0,
            "max_case_attempts": 2,
            "harness_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
                + (ROOT / "tests/xiaohe/semantic_runtime_support.py").read_bytes()
            ).hexdigest(),
            "temperature": model.temperature,
            "code_sha256": hashlib.sha256(
                b"".join(
                    (ROOT / name).read_bytes()
                    for name in (
                        "src/xiaohe/agents/semantic.py",
                        "src/xiaohe/agents/nodes.py",
                        "src/xiaohe/agents/runtime.py",
                        "src/xiaohe/workflows/compiler.py",
                    )
                )
            ).hexdigest(),
            "prompt_digests": {
                key: hashlib.sha256(value.encode()).hexdigest()
                for key, value in bundle.resources.items()
                if key.startswith("prompts/")
            },
            "corpus_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "monetary_cap": None,
            "monetary_authorization": "用户明确不设金额预算",
            "available_cost": None,
            "business_backend": "isolated in-memory adapter",
            "answer_review": "需要逐条对照来源；自动状态不代替人工业务验收",
            "authentication": "fictional trusted identities",
        }
        partial = []

        async def checked(case, shared_budget):
            started = time.perf_counter()
            value = EvaluatedCase.model_validate(await runner(case, shared_budget))
            value.elapsed_seconds = round(time.perf_counter() - started, 3)
            partial.append(value.model_dump())
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(
                json.dumps(
                    metadata
                    | {
                        "status": "incomplete",
                        "model_calls": budget.calls,
                        "evaluated": partial,
                        "usage": limited.usage,
                        "attempts": limited.attempts,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "utf-8",
            )
            print(
                json.dumps({"case": case.id, "passed": value.passed, "calls": budget.calls}),
                flush=True,
            )
            return value

        report = await evaluate_cases(cases, checked, budget)
        args.receipt.write_text(
            json.dumps(
                metadata
                | report.model_dump()
                | {"usage": limited.usage, "attempts": limited.attempts},
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        print(
            json.dumps(
                {
                    "status": report.status,
                    "model_calls": report.model_calls,
                    "failed": report.failed,
                    "not_run": report.not_run,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if report.status == "pass" else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    try:
        raise SystemExit(
            asyncio.run(
                main(parser.parse_args()),
                loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
            )
        )
    except Exception as error:
        print(
            "semantic evaluation failed: " + getattr(error, "code", type(error).__name__),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
