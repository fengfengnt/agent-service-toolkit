from types import SimpleNamespace

import httpx
import pytest_asyncio
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import SecretStr

from xiaohe.contracts import AgentOutcome
from xiaohe.graph import build_graph
from xiaohe.settings import Settings
from xiaohe.toolkit_client import ToolkitClient
from xiaohe.toolkit_runtime import create_execution_router
from xiaohe.worker import RunWorker


@pytest_asyncio.fixture
async def runtime_env(pg_factory, job_app, pg_url, monkeypatch):
    from agents.agents import Agent, agents
    from core.settings import settings as upstream

    calls = []

    async def handle(state):
        calls.append(state["command_id"])
        return AgentOutcome(
            parts=[{"type": "text", "text": "模拟答复"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )

    graph = build_graph(handler=handle)
    dsn = pg_url.replace("postgresql+psycopg:", "postgresql:", 1)
    async with (
        await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as saver_conn,
        await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as store_conn,
    ):
        saver, store = AsyncPostgresSaver(saver_conn), AsyncPostgresStore(store_conn)
        await saver.setup()
        await store.setup()
        graph.checkpointer, graph.store = saver, store
        monkeypatch.setitem(
            agents, "xiaohe", Agent(description="isolated protocol fixture", graph_like=graph)
        )
        monkeypatch.setattr(upstream, "AUTH_SECRET", SecretStr("fixture-auth"))
        settings = Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth")
        app = FastAPI()
        app.include_router(create_execution_router(pg_factory, settings))

        @app.get("/internal/xiaohe/health/ready")
        async def ready():
            return {"status": "ready"}

        client = ToolkitClient(settings, transport=httpx.ASGITransport(app=app))
        worker = RunWorker(pg_factory, client)
        yield SimpleNamespace(
            app=app, graph=graph, client=client, worker=worker, calls=calls, settings=settings
        )
