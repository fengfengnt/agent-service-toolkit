from deep_runtime_support import DeepScriptModel
from test_deep_factory import final_response
from test_reception_flow import install

from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.deep_runtime.runtime import publish_runtime
from xiaohe.resources import RESOURCE_ROOT


class AgentSequenceModel(DeepScriptModel):
    @property
    def agent_calls(self):
        return [str(messages[0].content) for messages in self.requests]


async def install_agents(
    factory, env, monkeypatch, decisions, root=RESOURCE_ROOT, business_client=None
):
    model = AgentSequenceModel(
        responses=[
            final_response(
                answer={
                    "text": "可先梳理范围和需求，费用以正式政策为准。",
                    "basis": "needs_confirmation",
                    "source_ids": [],
                },
                wait_for_input=True,
            )
        ],
        profile={"tool_calling": True},
    )
    await publish_runtime(factory, root)
    await install(factory, env, monkeypatch, model=model, business_client=business_client)
    return model, load_runtime_bundle(root)
