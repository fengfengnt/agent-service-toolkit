import asyncio
import json
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from xiaohe.agents.contracts import AgentFrame, AnswerDraft, SemanticDecision
from xiaohe.contracts import ServiceError
from xiaohe.graph import _safe_model_text
from xiaohe.reception.contracts import KnowledgeHit
from xiaohe.reception.fields import map_member_size, validate_fields
from xiaohe.workflows.models import WorkflowBundle, WorkflowDefinition

MODEL_TIMEOUT = 30


def _safe_context(frame: AgentFrame) -> dict:
    context = frame.model_context.model_dump(mode="json")
    context["text"] = _safe_model_text(context["text"])
    for turn in context["recent_turns"]:
        turn["text"] = _safe_model_text(turn["text"])
    context["saved_fields"] = {
        key: [_safe_model_text(item) for item in value]
        if isinstance(value, list)
        else _safe_model_text(value)
        if isinstance(value, str)
        else value
        for key, value in context["saved_fields"].items()
    }
    return context


async def _structured(model, schema, messages):
    try:
        async with asyncio.timeout(MODEL_TIMEOUT):
            raw = await model.with_structured_output(schema).ainvoke(
                messages, config={"tags": ["xiaohe_internal"]}
            )
            return schema.model_validate(raw)
    except Exception:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None


async def understand_turn(
    model: BaseChatModel,
    frame: AgentFrame,
    definition: WorkflowDefinition,
    bundle: WorkflowBundle,
    *,
    prompt_key: str = "intent",
) -> SemanticDecision:
    if frame.agent_id != definition.agent_id or frame.bundle_id != bundle.bundle_id:
        raise ServiceError("FORBIDDEN", 403)
    context = _safe_context(frame)
    prompt = bundle.resources["prompts/" + definition.prompts[prompt_key]]
    if frame.model_context.role_guide:
        prompt += "\n\n本轮适用的角色留资规范：\n" + frame.model_context.role_guide
    context.pop("role_guide", None)
    from xiaohe.reception.skills import load_skill_materials

    context["available_topics"] = [
        {"id": item.source, "scope": _safe_model_text(item.text)}
        for item in load_skill_materials(bundle, frame.agent_id, definition.skills)
    ]
    decision = await _structured(
        model,
        SemanticDecision,
        [
            SystemMessage(content=prompt),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ],
    )
    if (
        frame.model_context.allowed_actions
        and decision.action not in frame.model_context.allowed_actions
        or len(decision.topics) != len(set(decision.topics))
        or set(decision.topics) - set(definition.skills)
        or set(decision.topics) - set(frame.model_context.eligible_topics)
        or set(decision.candidates) - set(frame.model_context.allowed_fields)
    ):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
    for key, candidate in decision.candidates.items():
        if key == "memberSize" and candidate.value is not None:
            normalized = map_member_size(candidate.evidence)
            if normalized and (
                candidate.value == normalized
                or isinstance(candidate.value, str)
                and candidate.value in candidate.evidence
            ):
                candidate = candidate.model_copy(update={"value": normalized})
                decision = decision.model_copy(
                    update={"candidates": decision.candidates | {key: candidate}}
                )
        values = candidate.value if isinstance(candidate.value, list) else [candidate.value]
        supported = (
            candidate.value is None
            or all(isinstance(value, str) and value in candidate.evidence for value in values)
            or key == "memberSize"
            and candidate.value == map_member_size(candidate.evidence)
            or bool(frame.model_context.field_options.get(key))
            and all(value in frame.model_context.field_options[key] for value in values)
        )
        if candidate.evidence not in context["text"] or not supported:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
    if decision.candidates:
        try:
            validate_fields(
                frame.agent_id.removeprefix("xiaohe-"),
                {key: item.value for key, item in decision.candidates.items()},
                policy=frame.field_policy,
            )
        except ServiceError:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None
    return decision


async def compose_answer(
    model: BaseChatModel,
    frame: AgentFrame,
    decision: SemanticDecision,
    materials: list[KnowledgeHit],
    prompt: str,
) -> AnswerDraft:
    payload = {
        "context": _safe_context(frame),
        "topics": decision.topics,
        "materials": [
            {
                "source": item.source,
                "text": _safe_model_text(item.text),
                "conditions": item.conditions,
            }
            for item in materials
        ],
    }
    for _ in range(2):
        answer = await _structured(
            model,
            AnswerDraft,
            [
                SystemMessage(content=prompt),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
        )
        if "\ufffd" not in answer.text:
            break
    else:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
    if set(answer.source_ids) - {item.source for item in materials}:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
    if frame.interaction_policy and re.search(
        r"(?:已(?:经)?|成功)(?:为您|帮您|替您)?(?:成功)?(?:保存|提交|受理|安排|转交|审核|开通|注册|登录|绑定)"
        r"|(?:提交|受理|保存|审核|开通|注册|登录|绑定)(?:成功|完成|通过)", answer.text
    ):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
    return answer
