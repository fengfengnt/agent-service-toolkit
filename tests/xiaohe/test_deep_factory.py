import importlib
from pathlib import Path

import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from test_deep_bundles import actor

from xiaohe.deep_runtime.bundles import load_runtime_bundle, select_binding, skill_files
from xiaohe.deep_runtime.models import ChildBinding, DeepExecution

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"


def factory():
    assert importlib.util.find_spec("xiaohe.deep_runtime.factory") is not None, (
        "native factory missing"
    )
    return importlib.import_module("xiaohe.deep_runtime.factory")


def final_response(schema="DeepTurnResult", **fields):
    return AIMessage(content="", tool_calls=[dict(name=schema, args=fields, id="final-result")])


def stub_tools(binding):
    return [
        StructuredTool.from_function(lambda: "fixture", name=name, description=name)
        for name in binding.tools
    ]


@pytest.mark.asyncio
async def test_business_agent_combines_skills_with_readonly_filesystem():
    module = factory()
    bundle = load_runtime_bundle(ROOT)
    binding = select_binding(bundle, actor(), "xiaohe-buyer")
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="read_file",
                        args={"file_path": "/skills/buyer-security/references/answer.md"},
                        id="security",
                    )
                ],
            ),
            final_response(
                answer=dict(
                    text="资料用途需核实，也可以继续填写。",
                    basis="needs_confirmation",
                    source_ids=[],
                ),
                wait_for_input=True,
            ),
        ]
    )
    graph = module.build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="安全吗？开始填吧")],
            "files": skill_files(bundle, binding),
        },
        {"configurable": {"thread_id": "native-factory"}},
    )
    assert result["structured_response"].wait_for_input is True
    prompt = model.requests[0][0].text
    assert bundle.resources["skills/buyer-lead-capture/SKILL.md"] in prompt
    assert bundle.resources["skills/buyer-analysis/references/answer.md"] not in prompt
    assert not {"write_file", "edit_file", "delete", "execute", "task"} & set(
        model.bound_tool_names
    )
    assert any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "security"
        and "资料" in message.content
        for message in result["messages"]
    )


@pytest.mark.asyncio
async def test_guessed_other_role_file_is_not_readable():
    module = factory()
    bundle = load_runtime_bundle(ROOT)
    binding = select_binding(bundle, actor(), "xiaohe-buyer")
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="read_file",
                        args={"file_path": "/skills/supplier-security/SKILL.md"},
                        id="denied",
                    )
                ],
            ),
            final_response(wait_for_input=True),
        ]
    )
    graph = module.build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="test")], "files": skill_files(bundle, binding)},
        {"configurable": {"thread_id": "denied-skill"}},
    )
    tool_result = next(
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.tool_call_id == "denied"
    )
    assert "not found" in str(tool_result.content).lower()
    assert "供应商方向留资" not in str(tool_result.content)


@pytest.mark.asyncio
async def test_parent_delegates_to_explicit_readonly_child():
    module = factory()
    bundle = load_runtime_bundle(ROOT)
    binding = select_binding(bundle, actor(), "xiaohe-buyer")
    binding = binding.model_copy(
        update={
            "children": [
                ChildBinding(
                    name="checker",
                    description="检查资料安全说明",
                    skills=["buyer-security"],
                    tools=[],
                )
            ]
        }
    )
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="task",
                        args={"description": "检查资料安全说明", "subagent_type": "checker"},
                        id="delegate",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="read_file",
                        args={"file_path": "/skills/buyer-security/SKILL.md"},
                        id="child-read",
                    )
                ],
            ),
            final_response("ChildResult", summary="可以按资料用途继续核对。"),
            final_response(wait_for_input=True),
        ]
    )
    graph = module.build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="请检查")], "files": skill_files(bundle, binding)},
        {"configurable": {"thread_id": "explicit-child"}},
    )
    returned = next(
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.tool_call_id == "delegate"
    )
    assert "可以按资料用途继续核对" in returned.content
    child_prompt = model.requests[1][0].text
    assert "buyer-security" in child_prompt
    assert "buyer-lead-capture" not in child_prompt
    assert result["structured_response"].wait_for_input is True
