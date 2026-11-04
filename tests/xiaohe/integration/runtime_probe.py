"""真实 HTTP/持久队列硬杀探针；屏障只存在于测试进程。"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.contracts import AgentOutcome
from xiaohe.database import create_database_engine
from xiaohe.graph import build_graph
from xiaohe.jobs import build_job_app
from xiaohe.settings import Settings
from xiaohe.toolkit_client import ToolkitClient
from xiaohe.worker import RunWorker


async def main():
    kind, mode, port = sys.argv[1:4]
    url = os.environ["XIAOHE_TEST_DATABASE_URL"]
    engine = create_database_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(toolkit_base_url=f"http://127.0.0.1:{port}", auth_secret="fixture-auth")
    if kind == "worker":

        class Client(ToolkitClient):
            async def stream_execution(self, command):
                async for event in super().stream_execution(command):
                    if event.kind == "outcome" and mode == "gate":
                        print("GATED", flush=True)
                        await asyncio.Event().wait()
                    yield event

        worker = RunWorker(factory, Client(settings))
        async with build_job_app(
            url, worker.execute_run, on_exhausted=worker.finalize_lost_run
        ).open_async() as app:
            print("READY", flush=True)
            await app.run_worker_async(
                queues=["xiaohe", "xiaohe-maintenance"] if mode == "recovery" else ["xiaohe"],
                wait=mode == "recovery",
                listen_notify=False,
                fetch_job_polling_interval=5 if mode == "recovery" else 0.1,
                install_signal_handlers=False,
            )
        await engine.dispose()
        return

    from agents.agents import Agent, agents
    from core.settings import settings as upstream
    from xiaohe import toolkit_runtime

    upstream.AUTH_SECRET = SecretStr("fixture-auth")
    if kind == "composition":
        from sqlalchemy.engine import make_url

        from core.settings import DatabaseType
        from schema import AgentInfo
        from service import service
        from xiaohe.main import create_app

        target = make_url(url)
        if not target.database.startswith("test_xiaohe_"):
            raise ValueError("composition probe requires isolated test database")
        upstream.DATABASE_TYPE = DatabaseType.POSTGRES
        upstream.POSTGRES_USER = target.username
        upstream.POSTGRES_PASSWORD = SecretStr(target.password or "fixture-only")
        upstream.POSTGRES_HOST = target.host
        upstream.POSTGRES_PORT = target.port
        upstream.POSTGRES_DB = target.database
        # Keep this process probe scoped to the new graph, with original memory/lifespan.
        service.get_all_agent_info = lambda: [AgentInfo(key="xiaohe", description="probe")]
        configured = Settings(
            database_url=url,
            toolkit_base_url=f"http://127.0.0.1:{port}",
            auth_secret="fixture-auth",
        )
        composed = create_app(configured)
        async with composed.router.lifespan_context(composed):
            print("READY", flush=True)
            await asyncio.Event().wait()
        return

    async def handler(state):
        async with factory.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO xiaohe_probe_effects VALUES (:id,1) "
                    "ON CONFLICT (run_id) DO UPDATE SET invocations=xiaohe_probe_effects.invocations+1"
                ),
                {"id": state["run_id"]},
            )
        if mode == "question" and len([m for m in state["messages"] if m.type == "human"]) == 1:
            return AgentOutcome(
                parts=[{"type": "input_request", "requestId": "Q1", "prompt": "请补充"}],
                wait_for_input=True,
                artifact_version_ids=[],
            )
        return AgentOutcome(
            parts=[{"type": "text", "text": "检查点答复"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handler)
    if mode == "document":
        import hashlib
        import json
        from pathlib import Path

        from xiaohe.artifacts import ArtifactService
        from xiaohe.contracts import WriteFence

        # Reuse the structured model fixture; this process must never call a provider.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_reception_graph import model

        data = json.loads(Path(os.environ["XIAOHE_TEST_MINIO_CONFIG"]).read_text("utf-8"))
        if data["container"] != "xiaohe-agent-test-minio" or not data["bucket"].startswith(
            "test-xiaohe-"
        ):
            raise ValueError("document probe requires owned isolated bucket")
        artifact_service = ArtifactService(
            factory,
            Settings(
                toolkit_base_url=settings.toolkit_base_url,
                auth_secret="fixture-auth",
                minio_endpoint=data["endpoint"],
                minio_bucket=data["bucket"],
                minio_access_key=data["access_key"],
                minio_secret_key=data["secret_key"],
            ),
        )

        async def document(state):
            await handler(state)
            payload = "受控进程旅程文件".encode()
            fence = WriteFence.model_validate(state["write_fence"])
            async with factory.begin() as db:
                version = await artifact_service.stage_artifact(
                    db, fence, "受控文件", "text/plain", hashlib.sha256(payload).hexdigest()
                )
            await artifact_service.write_artifact_object(version.id, payload)
            async with factory.begin() as db:
                await artifact_service.finish_artifact(db, fence, version.id)
            return AgentOutcome(
                parts=[{"type": "text", "text": "已生成受控文件"}],
                wait_for_input=False,
                artifact_version_ids=[version.id],
            )

        graph = build_graph(
            factory=factory,
            model=model(intent="professional"),
            handlers={"buyer": document, "organization": document, "supplier": document},
        )
    agents["xiaohe"] = Agent(description="process probe", graph_like=graph)
    if mode == "marker":
        original = toolkit_runtime._drive

        async def gate(*args):
            print("GATED", flush=True)
            await asyncio.Event().wait()
            async for event in original(*args):
                yield event

        toolkit_runtime._drive = gate

    @asynccontextmanager
    async def lifespan(app):
        dsn = url.replace("postgresql+psycopg:", "postgresql:", 1)
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as first,
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as second,
        ):
            saver, store = AsyncPostgresSaver(first), AsyncPostgresStore(second)
            await saver.setup()
            await store.setup()
            graph.checkpointer, graph.store = saver, store
            print("READY", flush=True)
            yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(toolkit_runtime.create_execution_router(factory, settings))

    @app.get("/internal/xiaohe/health/ready")
    async def ready():
        return {"status": "ready"}

    await uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=int(port), log_level="error")
    ).serve()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
