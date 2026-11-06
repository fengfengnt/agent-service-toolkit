"""由恢复测试启动并强杀的隔离工作进程，不访问真实模型或业务后台。"""

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_agent_handoff import AgentSequenceModel

from xiaohe.agents.contracts import AgentFrame, SemanticDecision
from xiaohe.agents.nodes import make_node_executors
from xiaohe.agents.runtime import AgentRegistry, run_agent_turn
from xiaohe.database import create_database_engine
from xiaohe.workflows.models import OperationNode
from xiaohe.workflows.store import load_persisted_bundle


async def main():
    data = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    factory = async_sessionmaker(
        create_database_engine(os.environ["XIAOHE_TEST_DATABASE_URL"]), expire_on_commit=False
    )
    frame = AgentFrame.model_validate(data["frame"])
    async with factory() as db:
        bundle = await load_persisted_bundle(db, frame.bundle_id)

    async def stopped():
        Path(data["ready"]).write_text("phase committed", "utf-8")
        await asyncio.Event().wait()

    if data["phase"] == "source":
        model = AgentSequenceModel(
            result={}, decisions=[{"action": "consultation", "target_direction": "buyer"}]
        )
        registry = AgentRegistry(bundle, factory, model, None)

        async def target(*args, **kwargs):
            await stopped()

        registry["xiaohe-buyer"].ainvoke = target
        await run_agent_turn(factory, frame.fence, bundle=bundle, model=model, registry=registry)
    else:

        class Backend:
            async def result(self, actor, key):
                from xiaohe.reception.contracts import SubmissionResult

                return SubmissionResult(status="not_found", receipt=None)

            async def submit(self, actor, payload):
                async with factory.begin() as db:
                    await db.execute(
                        text(
                            "INSERT INTO xiaohe_probe_effects(run_id,invocations) VALUES (:run,1) ON CONFLICT DO NOTHING"
                        ),
                        {"run": frame.fence.run_id},
                    )
                await stopped()

        nodes = make_node_executors(factory, model=None, business_client=Backend(), bundle=bundle)
        node = OperationNode(
            id="submit", name="提交", type="operation", operation="confirm_submission", next="done"
        )
        await nodes["operation"](
            node,
            {
                "frame": frame.model_dump(),
                "decision": SemanticDecision(action="confirm_current_summary").model_dump(),
            },
            {},
        )


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
