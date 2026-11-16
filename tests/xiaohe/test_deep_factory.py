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
    assert "answer.source_ids" in prompt
    assert "允许编号：" + ", ".join(binding.skills) in prompt
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_id", ["xiaohe-reception", "xiaohe-buyer", "xiaohe-organization", "xiaohe-supplier"]
)
@pytest.mark.parametrize("authenticated", [False, True])
async def test_all_bindings_receive_full_shared_source(agent_id, authenticated):
    from xiaohe.knowledge.sources import load_sources
    from xiaohe.skill_schema import AGENT_IDS

    bundle = load_runtime_bundle(ROOT)
    binding = select_binding(bundle, actor(authenticated), agent_id)
    model = make_script_model([final_response(wait_for_input=True)])
    graph = factory().build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(
        {"messages": [HumanMessage(content="介绍平台")]},
        {"configurable": {"thread_id": "shared-" + agent_id + str(authenticated)}},
    )
    source = next(
        s
        for s in load_sources(ROOT / "knowledge", {a: set() for a in AGENT_IDS})
        if s.id == "platform-common"
    )
    prompt = model.requests[0][0].text
    assert source.text in prompt
    assert "shared:platform-common" in prompt
    assert "具体说明、适用条件与交付范围尚未确认" in prompt


@pytest.mark.asyncio
async def test_historical_bundle_never_reads_current_shared_file():
    from xiaohe.knowledge.shared import SHARED_PATH, SHARED_SOURCE_ID

    bundle = load_runtime_bundle(ROOT)
    bundle = bundle.model_copy(
        update={"resources": {k: v for k, v in bundle.resources.items() if k != SHARED_PATH}}
    )
    binding = select_binding(bundle, actor(), "xiaohe-buyer")
    model = make_script_model([final_response(wait_for_input=True)])
    graph = factory().build_business_agent(
        bundle,
        DeepExecution(bundle_id=bundle.bundle_id),
        binding,
        model=model,
        tools=stub_tools(binding),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(
        {"messages": [HumanMessage(content="介绍平台")]},
        {"configurable": {"thread_id": "historical-shared"}},
    )
    assert SHARED_SOURCE_ID not in model.requests[0][0].text
