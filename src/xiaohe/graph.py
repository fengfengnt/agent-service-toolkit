from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from xiaohe.contracts import AgentOutcome, ServiceError


class GraphState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str
    command_id: str
    output_message_id: str
    direction: str
    outcome: dict | None
    pending_request: dict | None


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


def build_graph(*, handler: Callable[[GraphState], Awaitable[AgentOutcome]] | None = None):
    async def respond(state: GraphState):
        if handler is None:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
        outcome = await handler(state)
        result = outcome.model_dump(mode="json", by_alias=True)
        questions = [
            part for part in result["parts"] if part["type"] in ("choice", "input_request")
        ]
        if outcome.wait_for_input and len(questions) != 1:
            raise ServiceError("INVALID_WAIT_STATE", 502)
        return {
            "outcome": result,
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
