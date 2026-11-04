"""只用于已授权的本地三方联调；不作为产品部署入口。"""

import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

from xiaohe.settings import Settings

DB = "xiaohe_triad_local_20261104"
BUCKET = "xiaohe-triad-local-20261104"
ISSUER = "https://sso.lvh.me"
CALLBACK = "https://portal.lvh.me:9444/portal/auth/callback"


def _read_private(path):
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("triad configuration must be private")
    return json.loads(path.read_text("utf-8"))


def runtime_settings(path):
    path = Path(path)
    data = _read_private(path)
    if data.get("sso_bridge_enabled"):
        raise ValueError("only the owner's private bridge file may enable SSO")
    bridge_path = path.with_name("sso-bridge.json")
    if bridge_path.is_file():
        bridge = _read_private(bridge_path)
        required = {
            "sso_bridge_base_url",
            "sso_bridge_redirect_uri",
            "sso_bridge_application_id",
            "sso_bridge_secret",
            "sso_bridge_ca_file",
        }
        if set(bridge) != required or not all(bridge.values()):
            raise ValueError("invalid owner bridge configuration")
        if bridge["sso_bridge_base_url"] != ISSUER or bridge["sso_bridge_redirect_uri"] != CALLBACK:
            raise ValueError("bridge must match the existing registration")
        data.update(bridge, sso_bridge_enabled=True)
    settings = Settings.model_validate(data)
    url = make_url(settings.database_url.get_secret_value())
    if (
        (url.drivername, url.host, url.port, url.database)
        != ("postgresql+psycopg", "127.0.0.1", 35417, DB)
        or url.query
        or settings.minio_bucket != BUCKET
        or settings.minio_endpoint != "http://127.0.0.1:35418"
        or settings.toolkit_base_url != "http://127.0.0.1:18090"
    ):
        raise ValueError("runtime is restricted to dedicated local triad resources")
    return settings


def create_triad_app(settings):
    from fastapi.responses import JSONResponse

    from xiaohe.main import create_app

    app = create_app(settings)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        from agents.agents import agents
        from schema import AgentInfo
        from service import service
        from xiaohe.contracts import AgentOutcome, WriteFence
        from xiaohe.graph import build_graph

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_reception_graph import model

        original_info = service.get_all_agent_info
        service.get_all_agent_info = lambda: [AgentInfo(key="xiaohe", description="isolated triad")]
        try:
            async with original_lifespan(instance):
                original_graph = agents["xiaohe"].graph_like
                factory = app.state.xiaohe_factory
                artifacts = app.state.xiaohe_artifacts

                async def document(state):
                    fence = WriteFence.model_validate(state["write_fence"])
                    content = (
                        f"# 隔离联调文件\n\n方向：{state['direction']}\n\n"
                        "由受控模型生成，仅验证持久化、流式与访问权限。\n"
                    ).encode()
                    async with factory.begin() as db:
                        version = await artifacts.stage_artifact(
                            db,
                            fence,
                            "隔离联调说明.md",
                            "text/markdown",
                            hashlib.sha256(content).hexdigest(),
                        )
                    await artifacts.write_artifact_object(version.id, content)
                    async with factory.begin() as db:
                        await artifacts.finish_artifact(db, fence, version.id)
                    return AgentOutcome(
                        parts=[
                            {"type": "text", "text": "已生成隔离联调文件；此回复来自受控模型。"}
                        ],
                        wait_for_input=False,
                        artifact_version_ids=[version.id],
                    )

                graph = build_graph(
                    factory=factory,
                    model=model(intent="professional"),
                    handlers={"buyer": document, "organization": document, "supplier": document},
                )
                graph.checkpointer, graph.store = original_graph.checkpointer, original_graph.store
                agents["xiaohe"].graph_like = graph
                yield
        finally:
            service.get_all_agent_info = original_info

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def no_sms(request, call_next):
        if request.method == "POST" and re.fullmatch(
            r"/ai-api/v1/auth/operations/[^/]+/sms", request.url.path
        ):
            return JSONResponse(
                {
                    "error": {
                        "code": "UPSTREAM_UNAVAILABLE",
                        "message": "隔离联调未启用短信发送。",
                        "retryable": False,
                    },
                    "requestId": str(uuid4()),
                },
                status_code=503,
                headers={"Cache-Control": "private, no-store"},
            )
        return await call_next(request)

    return app


async def main():
    kind, config_path = sys.argv[1:]
    if kind not in ("api", "worker"):
        raise ValueError("expected api or worker")
    settings = runtime_settings(config_path)
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
                await jobs.run_worker_async(shutdown_graceful_timeout=10)
        finally:
            await artifacts.close()
            await engine.dispose()
        return

    import dotenv
    import uvicorn

    # The isolated launcher must not discover or load another deployment's .env.
    original_find = dotenv.find_dotenv
    dotenv.find_dotenv = lambda *args, **kwargs: ""
    try:
        from core.settings import DatabaseType
        from core.settings import settings as upstream
    finally:
        dotenv.find_dotenv = original_find
    upstream.DATABASE_TYPE = DatabaseType.POSTGRES
    from pydantic import SecretStr

    url = make_url(settings.database_url.get_secret_value())
    upstream.POSTGRES_USER, upstream.POSTGRES_PASSWORD = url.username, SecretStr(url.password)
    upstream.POSTGRES_HOST, upstream.POSTGRES_PORT, upstream.POSTGRES_DB = (
        url.host,
        url.port,
        url.database,
    )
    upstream.AUTH_SECRET = settings.auth_secret
    await uvicorn.Server(
        uvicorn.Config(
            create_triad_app(settings),
            host="127.0.0.1",
            port=18090,
            proxy_headers=False,
            access_log=True,
            log_level="info",
        )
    ).serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"isolated triad startup failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
