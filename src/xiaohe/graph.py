import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.admission import _safe_text
from xiaohe.contracts import (
    AgentOutcome,
    ChatMessage,
    ReceptionContext,
    ReceptionDecision,
    RoutingAnswer,
    ServiceError,
    WriteFence,
)
from xiaohe.models import Command, Conversation, Message
from xiaohe.professional import dispatch_direction
from xiaohe.runs import require_write

_MODEL_TIMEOUT = 30
_POLICY = Path(__file__).resolve().parents[2] / "resources/xiaohe/reception-policy.md"
_DIRECTIONS = {"buyer": "采购方", "organization": "协会等组织机构", "supplier": "供应商"}


class GraphState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str
    command_id: str
    output_message_id: str
    direction: str
    outcome: dict | None
    pending_request: dict | None
    write_fence: dict


def _safe_model_text(text: str) -> str:
    try:
        _safe_text(text)
    except ServiceError:
        return "[认证信息已省略，请使用认证卡片]"
    return text[:10000]


async def build_reception_context(db: AsyncSession, fence: WriteFence) -> ReceptionContext:
    run = await require_write(db, fence)
    conversation = await db.get(Conversation, run.conversation_id)
    command = await db.scalar(
        select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
    )
    cutoff = await db.get(Message, command.message_id)
    rows = (
        await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == run.conversation_id,
                Message.system_id == run.system_id,
                Message.tenant_id == run.tenant_id,
                Message.created_at <= cutoff.created_at,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(20)
        )
    ).all()
    recent = [
        ChatMessage(
            id=str(row.id),
            role=row.role,
            created_at=row.created_at,
            parts=[{"type": "text", "text": _safe_model_text(visible_text(row.parts))}],
        )
        for row in reversed(rows)
    ]
    return ReceptionContext(
        recent_messages=recent,
        direction=conversation.direction,
        pending_request=command.question,
        confirmed_facts={},
    )


def reception_model() -> BaseChatModel:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from core import get_model
    from core.settings import settings
    from schema.models import FakeModelName

    if settings.DEFAULT_MODEL is None or settings.DEFAULT_MODEL == FakeModelName.FAKE:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    if settings.DEFAULT_MODEL not in settings.AVAILABLE_MODELS:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    result = None
    try:
        result = get_model(settings.DEFAULT_MODEL)
    except Exception:
        pass
    if result is None or isinstance(result, FakeListChatModel):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    return result


def _explicit_direction(text):
    if re.search(r"不要|不想|不是|不切换|保持当前", text):
        return None
    for direction, label in _DIRECTIONS.items():
        aliases = {
            "buyer": "采购方|买方",
            "organization": "协会(?:组织)?|组织机构|社群组织",
            "supplier": "供应商|供方",
        }[direction]
        if (
            text.strip() in (direction, label)
            or re.fullmatch(aliases, text.strip())
            or re.search(rf"(?:我是|我代表|切换到|选择|当前按)\s*(?:{aliases})", text)
        ):
            return direction
    return None


def _auth_intent(text):
    if re.search(r"不要|不想|不需要|不愿|暂不|暂时不|以后|再说|关闭|取消|拒绝", text):
        return None
    for intent, word in (("lead", "留资"), ("register", "注册"), ("login", "登录")):
        if re.search(rf"(?:我要|我想|我希望|请帮我|帮我)\s*{word}", text):
            return intent
    return None


async def generate_reception_decision(
    context: ReceptionContext, model: BaseChatModel
) -> ReceptionDecision:
    messages = [
        SystemMessage(
            content=_POLICY.read_text("utf-8")
            + "\n当前已确认方向："
            + json.dumps(context.direction)
        )
    ]
    for message in context.recent_messages[-20:]:
        cls = HumanMessage if message.role == "user" else AIMessage
        messages.append(
            cls(
                content=_safe_model_text(
                    visible_text(message.model_dump(mode="json", by_alias=True)["parts"])
                )
            )
        )
    result = None
    try:
        async with asyncio.timeout(_MODEL_TIMEOUT):
            raw = await model.with_structured_output(RoutingAnswer).ainvoke(
                messages, config={"tags": ["xiaohe_internal"]}
            )
            result = RoutingAnswer.model_validate(raw)
    except Exception:
        pass
    if result is None:
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
    latest = next(
        (message for message in reversed(context.recent_messages) if message.role == "user"), None
    )
    text = visible_text(latest.model_dump(mode="json", by_alias=True)["parts"]) if latest else ""
    explicit = _explicit_direction(text)
    direction = explicit or (
        context.direction if context.direction in _DIRECTIONS else result.direction
    )
    return ReceptionDecision(
        **{**result.model_dump(), "direction": direction},
        target_direction=explicit or result.direction,
        auth_intent=_auth_intent(text),
    )


async def reception_outcome(
    context: ReceptionContext, state: GraphState, model: BaseChatModel, handlers
) -> AgentOutcome:
    decision = await generate_reception_decision(context, model)
    state["direction"] = decision.direction
    if decision.auth_intent:
        return AgentOutcome(
            parts=[
                {"type": "text", "text": "请在认证卡片中继续，手机号和验证码无需发送到聊天。"},
                {"type": "auth_request", "intent": decision.auth_intent},
            ],
            wait_for_input=False,
            artifact_version_ids=[],
        )
    if decision.intent == "off_topic":
        return AgentOutcome(
            parts=[
                {
                    "type": "text",
                    "text": "我可以协助了解采购方、组织机构和供应商相关服务，请说明本次业务需求。",
                }
            ],
            wait_for_input=False,
            artifact_version_ids=[],
        )
    if decision.intent == "clarify" or decision.target_direction == "unknown":
        from uuid import uuid4

        return AgentOutcome(
            parts=[
                {
                    "type": "choice",
                    "requestId": str(uuid4()),
                    "prompt": "您希望了解哪个服务方向？",
                    "options": [{"id": key, "label": label} for key, label in _DIRECTIONS.items()],
                }
            ],
            wait_for_input=True,
            artifact_version_ids=[],
        )
    if decision.intent == "professional":
        result = await dispatch_direction(
            decision.target_direction, {**state, "direction": decision.target_direction}, handlers
        )
        return result.model_copy(
            update={"parts": [part for part in result.parts if part.type != "auth_request"]}
        )
    answer = decision.answer
    if re.search(
        r"费用|收费|免费|分成|返佣|交付|报价|多久|几天|价格",
        visible_text(context.recent_messages[-1].model_dump(mode="json", by_alias=True)["parts"]),
    ) or re.search(
        r"免费|分成|返佣|收费|价格|报价|交付|保证|首个|第一|[¥￥]|\d+(?:\.\d+)?\s*(?:元|%|天|小时)",
        answer,
    ):
        answer = "费用、分成和交付时间需顾问根据正式政策及具体需求确认。"
    if re.search(
        r"(?:已|成功)(?:保存|提交|发送|注册|登录|绑定|转交)|(?:注册|登录|保存|提交|转交)成功",
        answer,
    ):
        answer = "相关办理状态需要以实际业务接口结果为准，我可以先介绍服务。"
    if re.search(r"(?:提供|输入|发送|告诉我).{0,16}(?:手机号|验证码|短信码)", answer):
        answer = "手机号和验证码请通过认证卡片处理，聊天中可以先咨询服务。"
    return AgentOutcome(
        parts=[{"type": "text", "text": _safe_model_text(answer)}],
        wait_for_input=False,
        artifact_version_ids=[],
    )


def visible_text(parts: list[dict]) -> str:
    values = []
    for part in parts:
        if part["type"] == "text":
            values.append(part["text"])
        elif part["type"] in ("choice", "input_request"):
            values.append(part["prompt"])
            values.extend(option["label"] for option in part.get("options", []))
        elif part["type"] == "artifact_ref":
            values.append(part["title"])
    return "\n".join(values)


def build_graph(
    *,
    handler: Callable[[GraphState], Awaitable[AgentOutcome]] | None = None,
    factory=None,
    model: BaseChatModel | None = None,
    handlers=None,
    business_client=None,
):
    async def respond(state: GraphState, config: RunnableConfig):
        if handler is None:
            if factory is None:
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
            metadata = config.get("metadata", {})
            fence = None
            try:
                fence = WriteFence(
                    run_id=metadata.get("business_run_id"),
                    attempt=metadata.get("attempt"),
                    ownership_epoch=metadata.get("ownership_epoch"),
                )
            except ValidationError:
                pass
            if fence is None:
                raise ServiceError("FORBIDDEN", 403)
            state["write_fence"] = fence.model_dump(mode="json")
            if handlers is None:
                from xiaohe.reception.flow import run_reception_step

                outcome = await run_reception_step(
                    factory, fence, model, business_client=business_client
                )
            else:
                async with factory.begin() as db:
                    context = await build_reception_context(db, fence)
                outcome = await reception_outcome(
                    context, state, model or reception_model(), handlers
                )
            async with factory.begin() as db:
                run = await require_write(db, fence)
                conversation = await db.get(Conversation, run.conversation_id)
                if handlers is None:
                    state["direction"] = conversation.direction
                else:
                    conversation.direction = state["direction"]
        else:
            outcome = await handler(state)
        result = outcome.model_dump(mode="json", by_alias=True)
        questions = [
            part for part in result["parts"] if part["type"] in ("choice", "input_request")
        ]
        if outcome.wait_for_input and len(questions) != 1:
            raise ServiceError("INVALID_WAIT_STATE", 502)
        return {
            "outcome": result,
            "direction": state.get("direction", "unknown"),
            "pending_request": questions[0] if outcome.wait_for_input else None,
            "messages": [
                AIMessage(id=state["output_message_id"], content=visible_text(result["parts"]))
            ],
        }

    async def await_input(state: GraphState):
        approved = interrupt(state["pending_request"])
        if not isinstance(approved, dict) or approved.get("run_id") != state["run_id"]:
            raise ServiceError("INVALID_RESUME", 403)
        return {
            "command_id": approved["command_id"],
            "output_message_id": approved["output_message_id"],
            "messages": [HumanMessage(id=approved["message_id"], content=approved["text"])],
            "outcome": None,
            "pending_request": None,
        }

    builder = StateGraph(GraphState)
    builder.add_node("respond", respond)
    builder.add_node("await_input", await_input)
    builder.add_edge(START, "respond")
    builder.add_conditional_edges(
        "respond", lambda state: "await_input" if state["outcome"]["wait_for_input"] else END
    )
    builder.add_edge("await_input", "respond")
    return builder.compile()
