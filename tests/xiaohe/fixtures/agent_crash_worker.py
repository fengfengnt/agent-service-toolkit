"""隔离原生进程强杀探针，不访问真实模型或业务后台。"""

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.agents.contracts import AgentFrame
from xiaohe.database import create_database_engine
from xiaohe.deep_runtime.bundles import load_runtime_bundle_record, select_binding
from xiaohe.deep_runtime.tools import ToolContext, execute_effect
from xiaohe.models import Command
from xiaohe.reception.contracts import SubmissionResult


async def main():
    data = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    assert data["phase"] == "deep"
    factory = async_sessionmaker(
        create_database_engine(os.environ["XIAOHE_TEST_DATABASE_URL"]), expire_on_commit=False
    )
    frame = AgentFrame.model_validate(data["frame"])

    class Backend:
        async def result(self, actor, key):
            return SubmissionResult(status="not_found", receipt=None)

        async def submit(self, actor, payload):
            async with factory.begin() as db:
                await db.execute(
                    text(
                        "INSERT INTO xiaohe_probe_effects(run_id,invocations) VALUES (:run,1) ON CONFLICT DO NOTHING"
                    ),
                    {"run": frame.fence.run_id},
                )
            Path(data["ready"]).write_text("phase committed", "utf-8")
            await asyncio.Event().wait()

    async with factory() as db:
        command = await db.get(Command, frame.command_id)
        bundle = await load_runtime_bundle_record(db, command.payload["deepExecution"]["bundle_id"])
    context = ToolContext(
        factory,
        frame.actor,
        frame.fence,
        frame.command_id,
        frame.conversation_id,
        select_binding(bundle, frame.actor, frame.agent_id),
        bundle,
        frame.model_context.text,
        Backend(),
    )
    await execute_effect(
        context,
        "submit_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": frame.model_context.text}},
    )


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
