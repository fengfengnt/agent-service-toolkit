import re

from xiaohe.agents.contracts import AgentFrame, SemanticDecision
from xiaohe.contracts import ServiceError
from xiaohe.graph import _safe_model_text
from xiaohe.reception.fields import map_member_size, validate_fields


def _claims_business_success(text: str) -> bool:
    # shortcut: 覆盖常见状态声明；真实模型评测发现新句式时补充，不能替代语义评测。
    def quoted(match):
        prefix, suffix = text[max(0, match.start() - 24) : match.start()], text[match.end() :]
        if re.search(r"示例|例如|比如|样例|引用|原话|example", prefix, re.I) or re.match(
            r"\s*(?:不代表|并不表示|的意思|是指)", suffix
        ):
            return ""
        return match.group()

    text = re.sub(r'“[^”]*”|‘[^’]*’|"[^"\n]*"|「[^」]*」', quoted, text)
    pattern = re.compile(
        r"(?:已(?:经)?|成功)(?:(?:为您|帮您|替您|成功|正式|被)\s*){0,3}"
        r"(?:保存|提交|受理|安排|转交|审核|开通|注册|登录|绑定|通过)"
        r"|(?:提交|受理|保存|审核|开通|注册|登录|绑定)(?:成功|完成|通过)"
        r"|\b(?:has|have|is|are)\s+(?:now\s+)?(?:been\s+)?"
        r"(?:accepted|approved|submitted|saved|enabled|registered|assigned)\b",
        re.I,
    )
    for clause in re.split(r"[。！？；，,!?;\n]", text):
        for match in pattern.finditer(clause):
            prefix, suffix = clause[: match.start()], clause[match.end() :]
            if re.search(
                r"(?:尚未|未|没有|并未|不代表|不能|不会|并非|还没|如果|假设|例如|比如|当|若).{0,16}$",
                prefix,
            ):
                continue
            if re.search(r"\b(?:if|unless|not|never|example)\b.{0,60}$", prefix, re.I):
                continue
            if re.match(r"(?:后|时|才能|才可)|(?:用户|会员|企业)(?:可|能|应|需|通常)", suffix):
                continue
            return True
    return False


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


def validate_candidates(decision: SemanticDecision, frame: AgentFrame) -> SemanticDecision:
    context = _safe_context(frame)
    if set(decision.candidates) - set(frame.model_context.allowed_fields):
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
