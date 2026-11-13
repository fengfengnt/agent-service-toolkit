import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from ipaddress import ip_address, ip_network

import anyio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.agui import create_agui_router
from xiaohe.api import create_public_router, install_error_handlers
from xiaohe.artifacts import ArtifactService
from xiaohe.database import create_database_engine
from xiaohe.graph import build_graph
from xiaohe.knowledge.runtime import EmbeddedKnowledge
from xiaohe.reception.api import create_reception_file_router, create_reception_router
from xiaohe.reception.business_client import ReceptionBusinessClient
from xiaohe.settings import Settings
from xiaohe.toolkit_runtime import create_execution_router

_active_app = None


def create_app(settings: Settings | None = None) -> FastAPI:
    from agents.agents import Agent, agents
    from core.settings import DatabaseType
    from core.settings import settings as upstream_settings
    from service.service import app as upstream_app

    settings = settings or Settings()
    if settings.database_url is None:
        raise ValueError("XIAOHE_DATABASE_URL is required")
    if upstream_settings.DATABASE_TYPE != DatabaseType.POSTGRES:
        raise ValueError("xiaohe requires the existing PostgreSQL saver/store configuration")
    engine = create_database_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reception_client = ReceptionBusinessClient(settings)
    internal = create_execution_router(factory, settings)

    @asynccontextmanager
    async def lifespan(app):
        global _active_app
        if _active_app is not None:
            raise RuntimeError("only one xiaohe app may own the upstream registry per process")
        _active_app = app
        previous = agents.get("xiaohe")
        knowledge = EmbeddedKnowledge(settings)
        try:
            # 后续独立部署只替换宿主认证和 BusinessBackend 装配；本期共用进程。
            from xiaohe.deep_runtime.runtime import publish_runtime
            from xiaohe.resources import RESOURCE_ROOT

            await publish_runtime(factory, RESOURCE_ROOT)
            await knowledge.start()
            app.state.xiaohe_knowledge = knowledge
            agents["xiaohe"] = Agent(
                description="小鹤执行与暂停外壳",
                graph_like=build_graph(
                    factory=factory, business_client=reception_client, knowledge=knowledge
                ),
            )
            async with AsyncExitStack() as stack:
                # An open transaction would block upstream CREATE INDEX CONCURRENTLY.
                async with engine.connect() as guard:
                    guard = await guard.execution_options(isolation_level="AUTOCOMMIT")
                    try:
                        while not await guard.scalar(
                            text(
                                "SELECT pg_try_advisory_lock(hashtextextended('xiaohe.startup',0))"
                            )
                        ):
                            await asyncio.sleep(0.1)
                        await stack.enter_async_context(
                            upstream_app.router.lifespan_context(upstream_app)
                        )
                    finally:
                        with anyio.CancelScope(shield=True):
                            try:
                                await guard.execute(
                                    text(
                                        "SELECT pg_advisory_unlock(hashtextextended('xiaohe.startup',0))"
                                    )
                                )
                            except BaseException:
                                await guard.invalidate()
                                raise
                yield
        finally:
            if previous is None:
                agents.pop("xiaohe", None)
            else:
                agents["xiaohe"] = previous
            _active_app = None
            try:
                await knowledge.aclose()
            finally:
                await app.state.xiaohe_artifacts.close()
                await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.xiaohe_settings = settings
    app.state.xiaohe_factory = factory
    app.state.xiaohe_engine = engine
    app.state.xiaohe_artifacts = ArtifactService(factory, settings)

    @app.middleware("http")
    async def proxy_scheme(request, call_next):
        peer = request.client.host if request.client else ""
        try:
            trusted = any(
                ip_address(peer) in ip_network(cidr) for cidr in settings.trusted_proxy_cidrs
            )
        except ValueError:
            trusted = False
        if trusted and request.headers.getlist("X-Forwarded-Proto") == ["https"]:
            request.scope["scheme"] = "https"
        return await call_next(request)

    @app.get("/internal/xiaohe/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/internal/xiaohe/health/ready")
    async def ready():
        from fastapi.responses import JSONResponse

        try:
            async with factory() as db:
                await db.execute(text("SELECT 1 FROM xiaohe.systems LIMIT 1"))
                await db.execute(text("SELECT 1 FROM procrastinate_jobs LIMIT 1"))
            artifacts = app.state.xiaohe_artifacts
            await artifacts._io(lambda: artifacts._s3.head_bucket(Bucket=settings.minio_bucket))
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    install_error_handlers(app)
    app.include_router(create_public_router(factory))
    app.include_router(create_reception_router(factory, reception_client))
    app.include_router(create_reception_file_router(factory, settings, app.state.xiaohe_artifacts))
    app.include_router(create_agui_router(factory))
    app.include_router(internal)
    app.mount("/internal/toolkit", upstream_app)
    return app
