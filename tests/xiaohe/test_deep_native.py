from importlib.util import find_spec

import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool


def test_supported_native_exports():
    assert find_spec("deepagents") is not None, "DeepAgents is required for native skills"
    from deepagents import (
        CompiledSubAgent,
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    assert all(
        callable(value)
        for value in (
            CompiledSubAgent,
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            create_deep_agent,
            register_harness_profile,
            FilesystemMiddleware,
            AsyncPostgresSaver,
        )
    )


@pytest.mark.asyncio
async def test_native_skill_read_and_tool_loop():
    assert find_spec("deepagents") is not None, "DeepAgents is required for native skills"
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data

    probe_calls = []

    @tool
    def probe() -> str:
        """Return the result of the local capability probe."""
        probe_calls.append(True)
        return "probe-result"

    path = "/skills/probe-skill/SKILL.md"
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[dict(name="read_file", args={"file_path": path}, id="read-skill")],
            ),
            AIMessage(content="", tool_calls=[dict(name="probe", args={}, id="run-probe")]),
            AIMessage(content="probe-result"),
        ]
    )
    graph = create_deep_agent(model=model, backend=StateBackend(), skills=["/skills/"], tools=[probe])
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Use the probe skill.")],
            "files": {
                path: create_file_data(
                    "---\nname: probe-skill\ndescription: Run the local capability probe.\n---\n"
                    "Read this skill, call probe, then report its result.\n"
                )
            },
        }
    )
    trace = [
        call["name"]
        for message in result["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    assert trace == ["read_file", "probe"]
    assert probe_calls == [True]
    assert any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "read-skill"
        and "Read this skill, call probe" in message.content
        for message in result["messages"]
    )
    assert result["messages"][-1].content == "probe-result"
