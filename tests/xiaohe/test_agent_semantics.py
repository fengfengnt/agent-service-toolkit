import asyncio
import json
from uuid import uuid4

import pytest
import yaml
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from test_identity_sessions import system
from test_reception_graph import DecisionModel
from workflow_support import workflow_data, write_test_bundle

from xiaohe.agents import semantic
from xiaohe.agents.contracts import AgentFrame, ModelContext, SemanticDecision
from xiaohe.agents.semantic import compose_answer, understand_turn
from xiaohe.contracts import Actor, ServiceError, WriteFence
from xiaohe.reception.skills import load_skill_materials
from xiaohe.workflows.loader import load_bundle, workflow_for


class ScriptedModel(DecisionModel):
    responses: dict = {}
    delay: float = 0

    def with_structured_output(self, schema, **kwargs):
        async def respond(messages):
            self.requests.append(messages)
            await asyncio.sleep(self.delay)
            return self.responses[schema.__name__]

        return RunnableLambda(respond)


def prepared(tmp_path, text="我司在苏州，怎么计费"):
    root = write_test_bundle(tmp_path / "resources")
    material = root / "skills/buyer/rfx"
    material.mkdir(parents=True)
    (material / "SKILL.md").write_text("# 费用说明\n收费需正式政策确认，不承诺固定金额。", "utf-8")
    (root / "skills/catalog.json").write_text(
        json.dumps(
            [
                {
                    "id": "buyer.rfx",
                    "direction": "buyer",
                    "title": "费用说明",
                    "path": "buyer/rfx/SKILL.md",
                }
            ]
        ),
        "utf-8",
    )
    data = workflow_data()
    data["skills"] = ["buyer.rfx"]
    (root / "workflows/buyer.yaml").write_text(yaml.safe_dump(data), "utf-8")
    bundle = load_bundle(root)
    frame = AgentFrame(
        fence=WriteFence(run_id=uuid4(), attempt=1, ownership_epoch=0),
        command_id=uuid4(),
        message_id=uuid4(),
        conversation_id=uuid4(),
        agent_id="xiaohe-buyer",
        bundle_id=bundle.bundle_id,
        actor=Actor(system=system(), session_id=uuid4(), guest_id=uuid4(), user_id=None),
        model_context=ModelContext(
            text=text,
            direction="buyer",
            draft_step="round1",
            allowed_fields=["city"],
            eligible_topics=["buyer.rfx"],
        ),
    )
    return root, bundle, frame


def semantic_result(**changes):
    return {
        "topics": ["buyer.rfx"],
        "action": "consultation_with_details",
        "target_direction": None,
        "explicit_switch": False,
        "negated": False,
        "deferred": False,
        "conditional": False,
        "needs_clarification": False,
        "candidates": {"city": {"value": "苏州", "evidence": "我司在苏州"}},
    } | changes


@pytest.mark.parametrize("extra", ["actor", "draftId", "version", "url", "function"])
def test_semantic_schema_rejects_business_identity_and_executable_fields(extra):
    with pytest.raises(ValidationError):
        SemanticDecision.model_validate(semantic_result(**{extra: "not-trusted"}))


@pytest.mark.asyncio
async def test_question_and_field_update_are_both_retained(tmp_path):
    _, bundle, frame = prepared(tmp_path)
    model = ScriptedModel(result={}, responses={"SemanticDecision": semantic_result()})
    decision = await understand_turn(model, frame, workflow_for(bundle, frame.agent_id), bundle)
    assert decision.action == "consultation_with_details"
    assert decision.topics == ["buyer.rfx"]
    assert decision.candidates["city"].value == "苏州"
    assert len(model.requests) == 1
    assert str(frame.fence.run_id) not in str(model.requests)
    assert str(frame.actor.session_id) not in str(model.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "invalid", "invented_evidence", "wrong_topic"])
async def test_timeout_and_invalid_result_are_retryable_without_keyword_fallback(
    tmp_path, monkeypatch, kind
):
    _, bundle, frame = prepared(tmp_path, text="切换到供应商")
    raw = semantic_result(candidates={})
    if kind == "invalid":
        raw = {"actor": "private-error-sentinel"}
    elif kind == "invented_evidence":
        raw = semantic_result()
    elif kind == "wrong_topic":
        raw["topics"] = ["supplier.security"]
    model = ScriptedModel(
        result={}, responses={"SemanticDecision": raw}, delay=0.05 if kind == "timeout" else 0
    )
    monkeypatch.setattr(semantic, "MODEL_TIMEOUT", 0.01)

    def forbidden_fallback(*args):
        raise AssertionError("新语义路径不得回退关键词")

    monkeypatch.setattr("xiaohe.reception.flow.recognize_direction", forbidden_fallback)
    before = frame.model_dump()
    with pytest.raises(ServiceError) as error:
        await understand_turn(model, frame, workflow_for(bundle, frame.agent_id), bundle)
    assert error.value.code == "UPSTREAM_UNAVAILABLE" and error.value.retryable
    assert "private-error-sentinel" not in str(error.value)
    assert frame.model_dump() == before


@pytest.mark.asyncio
async def test_role_answer_uses_question_and_frozen_materials(tmp_path):
    root, bundle, frame = prepared(tmp_path)
    (root / "skills/buyer/rfx/SKILL.md").write_text("未审定新价格999元", "utf-8")
    materials = load_skill_materials(bundle, frame.agent_id, ["buyer.rfx"])
    assert "999" not in materials[0].text
    model = ScriptedModel(
        result={},
        responses={
            "AnswerDraft": {
                "text": "收费需顾问依据正式政策确认。",
                "basis": "needs_confirmation",
                "source_ids": ["buyer.rfx"],
            }
        },
    )
    answer = await compose_answer(
        model,
        frame,
        SemanticDecision.model_validate(semantic_result()),
        materials,
        bundle.resources["prompts/answer.md"],
    )
    assert answer.basis == "needs_confirmation"
    assert "正式政策" in answer.text
    assert len(model.requests) == 1
    assert "怎么计费" in str(model.requests) and "固定金额" in str(model.requests)
    assert str(frame.command_id) not in str(model.requests)


@pytest.mark.asyncio
async def test_model_payload_excludes_authentication_values(tmp_path):
    _, bundle, frame = prepared(
        tmp_path, text="Bearer private-auth-token 手机号13800138000 验证码987654"
    )
    model = ScriptedModel(result={}, responses={"SemanticDecision": semantic_result(candidates={})})
    await understand_turn(model, frame, workflow_for(bundle, frame.agent_id), bundle)
    sent = str(model.requests)
    assert all(value not in sent for value in ("private-auth-token", "13800138000", "987654"))


@pytest.mark.asyncio
async def test_answer_cannot_invent_a_source_or_success_card(tmp_path):
    _, _, frame = prepared(tmp_path)
    model = ScriptedModel(
        result={},
        responses={
            "AnswerDraft": {
                "text": "未确认的保证",
                "basis": "confirmed_material",
                "source_ids": ["invented-policy"],
            }
        },
    )
    with pytest.raises(ServiceError):
        await compose_answer(
            model, frame, SemanticDecision.model_validate(semantic_result()), [], "只依据资料回答"
        )
    model.responses["AnswerDraft"] = {
        "text": "已注册",
        "basis": "general_advice",
        "source_ids": [],
        "auth_request": {},
    }
    with pytest.raises(ServiceError):
        await compose_answer(
            model, frame, SemanticDecision.model_validate(semantic_result()), [], "只依据资料回答"
        )
