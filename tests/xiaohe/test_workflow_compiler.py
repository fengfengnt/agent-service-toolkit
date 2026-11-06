import copy

import pytest
import yaml
from workflow_support import AGENT_IDS, workflow_data

from xiaohe.workflows.compiler import compile_workflow, validate_graph
from xiaohe.workflows.loader import parse_workflow
from xiaohe.workflows.models import WorkflowValidationError


def definition(data):
    return parse_workflow(yaml.safe_dump(data))


def executors(trace, event="consultation"):
    async def execute(node, state, config):
        trace.append(node.id)
        if node.type == "decide":
            return {"event": event}
        if node.type == "result":
            return {"outcome": {"parts": [{"type": "text", "text": "受控回复"}]}}
        return {}

    return dict.fromkeys(
        ("understand", "decide", "operation", "answer", "handoff", "result"), execute
    )


@pytest.mark.asyncio
async def test_compiles_declared_path():
    trace = []
    graph = compile_workflow(definition(workflow_data()), executors(trace))
    result = await graph.ainvoke({"frame": {}})
    assert trace == ["understand", "decide", "answer", "result"]
    assert result["outcome"]["parts"][0]["text"] == "受控回复"


@pytest.mark.asyncio
async def test_known_unrouted_event_clarifies_but_unknown_event_is_rejected():
    trace = []
    graph = compile_workflow(definition(workflow_data()), executors(trace, "unclear"))
    await graph.ainvoke({"frame": {}})
    assert trace == ["understand", "decide", "clarify"]
    graph = compile_workflow(definition(workflow_data()), executors([], "arbitrary-code"))
    with pytest.raises(WorkflowValidationError):
        await graph.ainvoke({"frame": {}})


@pytest.mark.parametrize(
    "invalid", ["duplicate", "entry", "target", "unreachable", "cycle", "fallback"]
)
def test_invalid_topology_is_rejected(invalid):
    data = workflow_data()
    if invalid == "duplicate":
        data["nodes"].append(copy.deepcopy(data["nodes"][-1]))
    elif invalid == "entry":
        data["entry"] = "missing"
    elif invalid == "target":
        data["nodes"][0]["next"] = "missing"
    elif invalid == "unreachable":
        data["nodes"].append(
            {"id": "unused", "name": "不可达", "type": "result", "wait_for_input": False}
        )
    elif invalid == "cycle":
        data["nodes"][2]["next"] = "understand"
    else:
        del data["nodes"][1]["fallback"]
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(data))


def operation_workflow(operation, *, answer_after=False, agent_id="xiaohe-buyer"):
    data = workflow_data(agent_id)
    data["nodes"][1]["routes"]["consultation"] = "operate"
    data["nodes"].append(
        {
            "id": "operate",
            "name": "受控业务操作",
            "type": "operation",
            "operation": operation,
            "next": "answer" if answer_after else "result",
        }
    )
    if not answer_after:
        data["nodes"] = [node for node in data["nodes"] if node["id"] != "answer"]
    return data


def test_mutation_cannot_precede_required_llm():
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(operation_workflow("confirm_submission", answer_after=True)))
    validate_graph(definition(operation_workflow("query_progress", answer_after=True)))
    validate_graph(definition(operation_workflow("confirm_submission")))


@pytest.mark.parametrize("agent_id", ["xiaohe-reception", "xiaohe-buyer", "xiaohe-supplier"])
def test_role_cannot_request_organization_operations(agent_id):
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(operation_workflow("start_onboarding", agent_id=agent_id)))
    validate_graph(
        definition(operation_workflow("start_onboarding", agent_id="xiaohe-organization"))
    )


def test_guard_is_required_on_every_path_and_fallback_cannot_mutate():
    data = operation_workflow("confirm_submission")
    data["nodes"][0]["next"] = "operate"
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(data))


def test_new_understanding_invalidates_an_earlier_decision_guard():
    data = operation_workflow("confirm_submission")
    data["nodes"][1]["routes"]["consultation"] = "reinterpret"
    data["nodes"].append(
        {
            "id": "reinterpret",
            "name": "新的模型判断",
            "type": "understand",
            "prompt": "intent",
            "next": "operate",
        }
    )
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(data))
    data = operation_workflow("confirm_submission")
    data["nodes"][1]["fallback"] = "operate"
    with pytest.raises(WorkflowValidationError):
        validate_graph(definition(data))


def test_missing_executor_is_rejected_before_running():
    with pytest.raises(WorkflowValidationError):
        compile_workflow(definition(workflow_data()), {})


@pytest.mark.asyncio
async def test_four_graphs_do_not_share_state_or_later_definition_edits():
    graphs = {}
    traces = {}
    for agent_id in AGENT_IDS:
        traces[agent_id] = []
        source = definition(workflow_data(agent_id))
        graphs[agent_id] = compile_workflow(source, executors(traces[agent_id]))
        source.nodes[1].routes["consultation"] = "clarify"
    for agent_id, graph in graphs.items():
        await graph.ainvoke({"frame": {"agent_id": agent_id}})
        assert traces[agent_id] == ["understand", "decide", "answer", "result"]
