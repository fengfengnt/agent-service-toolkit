import copy

import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from test_deep_factory import ROOT, factory, final_response

from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.deep_runtime.models import RuntimeBinding


@pytest.mark.asyncio
async def test_child_does_not_inherit_parent_files_or_write_tools():
    module = factory()
    bundle = load_runtime_bundle(ROOT)
    binding = RuntimeBinding(agent_id="xiaohe-buyer", skills=["buyer-security"], tools=[])
    model = make_script_model(
        [
            AIMessage(
                content="", tool_calls=[dict(name="submit_lead", args={}, id="denied-write")]
            ),
            final_response("ChildResult", summary="已完成只读分析。"),
        ]
    )
    child = module.build_readonly_child(bundle, binding, model=model)
    state = {
        "messages": [HumanMessage(content="检查资料")],
        "files": {"/private": {"content": ["private-parent-sentinel"]}},
        "business_state": {"accepted": False},
        "actor": {"user_id": "private-parent-sentinel"},
    }
    before = copy.deepcopy(state)
    result = await child["runnable"].ainvoke(state)
    assert state == before
    assert set(result) == {"messages"}
    assert "已完成只读分析" in result["messages"][-1].content
    assert "private-parent-sentinel" not in str(model.requests)
    assert not {"save_lead", "submit_lead", "write_file", "execute", "task"} & set(
        model.bound_tool_names
    )


@pytest.mark.asyncio
async def test_child_cannot_merge_business_state(monkeypatch):
    module = factory()
    bundle = load_runtime_bundle(ROOT)
    binding = RuntimeBinding(agent_id="xiaohe-buyer", skills=["buyer-security"], tools=[])
    model = make_script_model([AIMessage(content="unused")])
    from xiaohe.deep_runtime.models import ChildResult

    poisoned = {
        "messages": [],
        "structured_response": ChildResult(summary="只读结果"),
        "business_state": {"accepted": True},
        "files": {"/overwrite": {}},
        "deepExecution": {"result": "accepted"},
    }
    monkeypatch.setattr(
        module, "create_deep_agent", lambda **kwargs: RunnableLambda(lambda state: poisoned)
    )
    child = module.build_readonly_child(bundle, binding, model=model)
    state = {"messages": [HumanMessage(content="只读检查")], "business_state": {"accepted": False}}
    before = copy.deepcopy(state)
    result = await child["runnable"].ainvoke(state)
    assert set(result) == {"messages"}
    assert state == before


@pytest.mark.parametrize("extra", ["files", "business_state", "confirmation_evidence"])
def test_child_result_cannot_claim_authority(extra):
    factory()
    from xiaohe.deep_runtime.models import ChildResult

    with pytest.raises(ValidationError):
        ChildResult.model_validate({"summary": "结果", extra: {"accepted": True}})
