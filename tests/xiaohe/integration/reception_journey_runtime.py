"""隔离联合验收进程；复用产品路由/图/worker，中心与抽取模型是显式替身。"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.engine import make_url

from xiaohe.settings import Settings


def configuration():
    if os.environ.get("RECEPTION_REAL_JOURNEY_ENABLED") != "1":
        raise ValueError("必须显式启用隔离接待验收")
    path = Path(os.environ["RECEPTION_JOURNEY_CONFIG"]).resolve()
    if ".superpowers" not in path.parts:
        raise ValueError("需要本次隔离配置")
    value = json.loads(path.read_text("utf-8"))
    settings = Settings.model_validate(value["settings"])
    target = make_url(settings.database_url.get_secret_value())
    if target.host != "127.0.0.1" or not target.database.startswith("test_xiaohe_reception_"):
        raise ValueError("禁止连接已有业务库")
    if not settings.minio_bucket.startswith("test-xiaohe-reception-"):
        raise ValueError("禁止连接已有桶")
    return value, settings


def api_app(config, settings):
    import dotenv

    # 不搜索其他部署的 .env；模型始终由本测试显式提供。
    original = dotenv.find_dotenv
    dotenv.find_dotenv = lambda *args, **kwargs: ""
    try:
        from agents.agents import Agent, agents
        from core.settings import settings as upstream
    finally:
        dotenv.find_dotenv = original
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from xiaohe.agents.runtime import load_agent_registry

    from xiaohe.agui import create_agui_router
    from xiaohe.api import create_public_router, install_error_handlers
    from xiaohe.artifacts import ArtifactService
    from xiaohe.auth_bridge import SsoBridgeClient
    from xiaohe.database import create_database_engine
    from xiaohe.graph import build_graph
    from xiaohe.reception.api import create_reception_file_router, create_reception_router
    from xiaohe.reception.business_client import ReceptionBusinessClient
    from xiaohe.toolkit_runtime import create_execution_router

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from langchain_core.runnables import RunnableLambda
    from test_reception_graph import DecisionModel

    class JourneyModel(DecisionModel):
        def with_structured_output(self, schema, **kwargs):
            async def respond(messages):
                if schema.__name__ != "SemanticDecision":
                    return {
                        "text": "当前具体安全承诺待确认，请以正式材料为准。",
                        "basis": "needs_confirmation",
                        "source_ids": [],
                    }
                context = json.loads(messages[-1].content)
                message = context["text"]
                scripted = {
                    "继续采集资料": {"action": "provide_details"},
                    "查看汇总": {"action": "show_summary"},
                    "好的": {"action": "unclear", "needs_clarification": True},
                    "确认提交当前汇总": {"action": "confirm_current_summary"},
                    "查询提交结果": {"action": "query_progress"},
                    "切换到供应商": {
                        "action": "switch_direction",
                        "target_direction": "supplier",
                        "explicit_switch": True,
                    },
                    "切换到采购商": {
                        "action": "switch_direction",
                        "target_direction": "buyer",
                        "explicit_switch": True,
                    },
                    "我们想优化采购寻源": {"action": "consultation", "target_direction": "buyer"},
                }
                return scripted.get(message, {"action": "consultation"})

            return RunnableLambda(respond)

    upstream.AUTH_SECRET = settings.auth_secret
    engine = create_database_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    artifacts = ArtifactService(factory, settings)
    business = ReceptionBusinessClient(settings)
    dsn = settings.database_url.get_secret_value().replace("postgresql+psycopg:", "postgresql:", 1)

    @asynccontextmanager
    async def lifespan(app):
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as saver_conn,
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as store_conn,
        ):
            saver, store = AsyncPostgresSaver(saver_conn), AsyncPostgresStore(store_conn)
            await saver.setup()
            await store.setup()
            model = JourneyModel(result={})
            registry = await load_agent_registry(
                factory, Path(config["resource_root"]), model, business
            )
            graph = build_graph(
                factory=factory,
                model=model,
                business_client=business,
                agent_registry=registry,
            )
            graph.checkpointer, graph.store = saver, store
            agents["xiaohe"] = Agent(description="隔离接待验收，未调用付费模型", graph_like=graph)
            try:
                yield
            finally:
                await artifacts.close()
                await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None)
    app.state.xiaohe_settings, app.state.xiaohe_factory = settings, factory
    app.state.xiaohe_artifacts = artifacts

    def center(request):
        path = request.url.path
        if path.endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "transactionId": str(uuid4()),
                        "stage": "CREATED",
                        "expiresAt": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                        "challengeId": None,
                        "credential": None,
                        "credentialExpiresAt": None,
                        "agreementVersion": None,
                        "agreementPath": None,
                    },
                },
            )
        if path.endswith("/claim"):
            transaction = path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "issuer": config["origin"],
                        "sub": "journey-user",
                        "sourceTenantId": 2,
                        "authenticatedAt": datetime.now(UTC).isoformat(),
                        "portalClientId": "fixture-portal",
                        "transactionId": transaction,
                    },
                },
            )
        return httpx.Response(503, json={"code": 503, "data": None})

    app.state.xiaohe_sso = SsoBridgeClient(settings, transport=httpx.MockTransport(center))

    @app.middleware("http")
    async def proxy(request, call_next):
        if (
            request.client.host == "127.0.0.1"
            and request.headers.get("x-forwarded-proto") == "https"
        ):
            request.scope["scheme"] = "https"
        return await call_next(request)

    @app.get("/internal/xiaohe/health/ready")
    async def health():
        return {"status": "ready"}

    install_error_handlers(app)
    app.include_router(create_public_router(factory))
    app.include_router(create_reception_router(factory, business))
    app.include_router(create_reception_file_router(factory, settings, artifacts))
    app.include_router(create_agui_router(factory))
    app.include_router(create_execution_router(factory, settings))
    return app


def gateway_app(config):
    app = FastAPI(docs_url=None, openapi_url=None)
    root = Path(config["portal_root"]).resolve()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
    async def gateway(request: Request, path: str):
        if path.startswith("ai-api/"):
            target = config["api_origin"]
        elif path.startswith("copilot-api/"):
            target = config["runtime_origin"]
        elif path.startswith("__test/") or path.startswith("admin-api/collaborator/reception/"):
            target = config["java_origin"]
        else:
            file = (
                (root / path.removeprefix("portal/")).resolve()
                if path.startswith("portal/assets/")
                else root / "index.html"
            )
            if not file.is_relative_to(root) or not file.is_file():
                return JSONResponse({"error": "not_found"}, status_code=404)
            return FileResponse(file)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            not in {
                "host",
                "content-length",
                "connection",
                "transfer-encoding",
                "x-xiaohe-system-id",
                "x-forwarded-proto",
                "x-portal-client-ip",
            }
        }
        headers.update(
            {
                "X-Xiaohe-System-Id": "portal",
                "X-Forwarded-Proto": "https",
                "X-Portal-Client-IP": "127.0.0.1",
            }
        )
        client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(20, read=None))
        try:
            upstream = await client.send(
                client.build_request(
                    request.method,
                    target
                    + request.url.path
                    + ("?" + request.url.query if request.url.query else ""),
                    headers=headers,
                    content=request.stream(),
                ),
                stream=True,
            )
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse(
                {
                    "error": {"code": "UPSTREAM_UNAVAILABLE", "retryable": True},
                    "requestId": "isolated-gateway",
                },
                status_code=503,
            )

        async def content():
            try:
                async for block in upstream.aiter_raw():
                    yield block
            finally:
                await upstream.aclose()
                await client.aclose()

        response = StreamingResponse(content(), status_code=upstream.status_code)
        response.raw_headers = [
            (key, value)
            for key, value in upstream.headers.raw
            if key.lower() not in {b"transfer-encoding", b"connection", b"content-encoding"}
        ]
        return response

    return app


async def main():
    import uvicorn

    config, settings = configuration()
    kind = sys.argv[1]
    if kind == "worker":
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from xiaohe.artifacts import ArtifactService
        from xiaohe.database import create_database_engine
        from xiaohe.jobs import build_job_app
        from xiaohe.toolkit_client import ToolkitClient
        from xiaohe.worker import RunWorker

        engine = create_database_engine(settings.database_url)
        artifacts = ArtifactService(async_sessionmaker(engine, expire_on_commit=False), settings)
        worker = RunWorker(artifacts.factory, ToolkitClient(settings), artifacts=artifacts)
        try:
            async with build_job_app(
                settings.database_url,
                worker.execute_run,
                on_exhausted=worker.finalize_lost_run,
                on_maintenance=worker.maintain,
                on_claim=worker.finish_claim,
            ).open_async() as jobs:
                await jobs.run_worker_async(shutdown_graceful_timeout=3)
        finally:
            await artifacts.close()
            await engine.dispose()
        return
    app = api_app(config, settings) if kind == "api" else gateway_app(config)
    port = config["api_port"] if kind == "api" else config["gateway_port"]
    tls = (
        {"ssl_certfile": config["certificate"], "ssl_keyfile": config["key"]}
        if kind == "gateway"
        else {}
    )
    await uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            proxy_headers=False,
            access_log=False,
            log_level="warning",
            **tls,
        )
    ).serve()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
