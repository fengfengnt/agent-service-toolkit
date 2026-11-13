import re
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import select

from xiaohe.admission import _safe_text
from xiaohe.contracts import (
    AgentOutcome,
    ServiceError,
    WriteFence,
)
from xiaohe.models import Command, Conversation
from xiaohe.runs import require_write


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


def _auth_intent(text):
    if re.search(r"不要|不想|不需要|不愿|暂不|暂时不|以后|再说|关闭|取消|拒绝", text):
        return None
    for intent, word in (("lead", "留资"), ("register", "注册"), ("login", "登录")):
        if re.search(rf"(?:我要|我想|我希望|请帮我|帮我)\s*{word}", text):
            return intent
    return None


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
    business_client=None,
    knowledge=None,
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
            async with factory.begin() as db:
                await require_write(db, fence)
                command = await db.scalar(
                    select(Command).where(
                        Command.run_id == fence.run_id, Command.completed_at.is_(None)
                    )
                )
                native = command.payload.get("deepExecution") if command else None
            if not native:
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
            from xiaohe.deep_runtime.models import DeepExecution
            from xiaohe.deep_runtime.runtime import run_deep_turn

            outcome = await run_deep_turn(
                factory,
                fence,
                execution=DeepExecution.model_validate(native),
                model=model,
                business_client=business_client,
                checkpointer=graph.checkpointer,
                knowledge=knowledge,
            )
            async with factory.begin() as db:
                run = await require_write(db, fence)
                conversation = await db.get(Conversation, run.conversation_id)
                state["direction"] = conversation.direction
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
    graph = builder.compile()
    return graph
