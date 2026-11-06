from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict, cast, get_args

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from xiaohe.workflows.models import (
    AnswerNode,
    DecideNode,
    HandoffNode,
    NodeDefinition,
    NodeType,
    OperationNode,
    ResultNode,
    RouteEvent,
    UnderstandNode,
    WorkflowDefinition,
    WorkflowValidationError,
)


class AgentState(TypedDict, total=False):
    frame: dict
    decision: dict
    event: str
    answer: dict
    operation_result: dict
    outcome: dict
    handoff: str | None


NodeExecutor = Callable[[NodeDefinition, AgentState, RunnableConfig], Awaitable[dict[str, Any]]]
ORGANIZATION_OPERATIONS = frozenset({"record_organization_intent", "start_onboarding"})
READ_ONLY_OPERATIONS = frozenset({"query_progress"})


def _successors(node: NodeDefinition) -> tuple[str, ...]:
    if isinstance(node, DecideNode):
        return tuple(set(node.routes.values()) | {node.fallback})
    if isinstance(node, (UnderstandNode, AnswerNode, OperationNode)):
        return (node.next,)
    return ()


def validate_graph(definition: WorkflowDefinition) -> None:
    nodes = {node.id: node for node in definition.nodes}
    if len(nodes) != len(definition.nodes) or definition.entry not in nodes:
        raise WorkflowValidationError("duplicate node or missing entry")
    if not isinstance(nodes[definition.entry], (UnderstandNode, DecideNode)):
        raise WorkflowValidationError("entry must understand or validate an action")
    for node in nodes.values():
        if any(target not in nodes for target in _successors(node)):
            raise WorkflowValidationError("missing route target")
        if isinstance(node, DecideNode) and not isinstance(nodes[node.fallback], ResultNode):
            raise WorkflowValidationError("unrouted intent must return a controlled result")
        if isinstance(node, HandoffNode) and (
            definition.agent_id in node.targets or len(node.targets) != len(set(node.targets))
        ):
            raise WorkflowValidationError("invalid handoff targets")
        if isinstance(node, OperationNode) and (
            definition.agent_id == "xiaohe-reception"
            or (
                node.operation in ORGANIZATION_OPERATIONS
                and definition.agent_id != "xiaohe-organization"
            )
        ):
            raise WorkflowValidationError("operation is not allowed for this role")

    active = set()
    seen = set()
    reached = set()

    def visit(node_id: str, guarded: bool, mutated: bool) -> None:
        if node_id in active:
            raise WorkflowValidationError("internal workflow cycles are not supported")
        key = (node_id, guarded, mutated)
        if key in seen:
            return
        node = nodes[node_id]
        if mutated and isinstance(node, (UnderstandNode, AnswerNode)):
            raise WorkflowValidationError("required model call follows a side effect")
        if isinstance(node, UnderstandNode):
            guarded = False
        elif isinstance(node, DecideNode):
            guarded = True
        elif isinstance(node, OperationNode):
            if not guarded:
                raise WorkflowValidationError("business operation requires a validated decision")
            mutated = mutated or node.operation not in READ_ONLY_OPERATIONS
        active.add(node_id)
        reached.add(node_id)
        for target in _successors(node):
            visit(target, guarded, mutated)
        active.remove(node_id)
        seen.add(key)

    visit(definition.entry, False, False)
    if reached != set(nodes):
        raise WorkflowValidationError("unreachable workflow node")


def _bind(node: NodeDefinition, executor: NodeExecutor):
    async def run(state: AgentState, config: RunnableConfig):
        return await executor(node, state, config)

    return run


def _router(node: DecideNode):
    def route(state: AgentState):
        event = state.get("event")
        if event not in get_args(RouteEvent):
            raise WorkflowValidationError("invalid runtime route event")
        return node.routes.get(cast(RouteEvent, event), node.fallback)

    return route


def compile_workflow(
    definition: WorkflowDefinition, executors: Mapping[NodeType, NodeExecutor]
) -> CompiledStateGraph:
    try:
        definition = WorkflowDefinition.model_validate(definition.model_dump())
    except ValidationError:
        raise WorkflowValidationError("invalid workflow definition") from None
    validate_graph(definition)
    if any(node.type not in executors for node in definition.nodes):
        raise WorkflowValidationError("missing registered node executor")
    graph = StateGraph(AgentState)
    for node in definition.nodes:
        graph.add_node(node.id, _bind(node, executors[node.type]))
    graph.add_edge(START, definition.entry)
    for node in definition.nodes:
        if isinstance(node, DecideNode):
            graph.add_conditional_edges(
                node.id, _router(node), {target: target for target in _successors(node)}
            )
        elif isinstance(node, (ResultNode, HandoffNode)):
            graph.add_edge(node.id, END)
        else:
            graph.add_edge(node.id, node.next)
    return graph.compile(name=definition.agent_id, checkpointer=False)
