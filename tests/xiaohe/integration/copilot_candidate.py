"""本次 Copilot 隔离候选入口；复用受控图、产品 worker 与 Owner 私有认证配置。"""

import asyncio
import hashlib
import json
import os
import ssl
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.engine import make_url

from integration.triad_runtime import _read_private
from xiaohe.settings import Settings

DATABASE = "xiaohe_copilot_candidate_20261105"
BUCKET = "xiaohe-copilot-candidate-20261105"


def load_settings(path):
    path = Path(path)
    data = _read_private(path)
    aliases = {
        name: field.validation_alias
        for name, field in Settings.model_fields.items()
        if name.startswith("sso_bridge_")
    }
    if any(
        (key.startswith("sso_bridge_") and key != "sso_bridge_enabled") or key in aliases.values()
        for key in data
    ):
        raise ValueError("candidate Bridge values require the owner's private mount")
    enabled = data.pop("sso_bridge_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("candidate SSO opt-in must be an explicit boolean")
    bridge = {name: None for name in aliases if name != "sso_bridge_enabled"}
    if enabled:
        source, ca = path.with_name("sso-bridge.json"), path.with_name("public-ca.pem")
        if not source.is_file() or not ca.is_file():
            raise ValueError("candidate requires private Bridge and public CA mounts")
        bridge = _read_private(source)
        if (
            set(bridge)
            != {
                "sso_bridge_base_url",
                "sso_bridge_redirect_uri",
                "sso_bridge_application_id",
                "sso_bridge_secret",
                "sso_bridge_ca_file",
            }
            or bridge["sso_bridge_base_url"] != "https://sso.lvh.me"
            or bridge["sso_bridge_application_id"] != "31555063-bbe3-4907-8745-c553208fff23"
            or bridge["sso_bridge_redirect_uri"] != "https://portal.lvh.me/portal/auth/callback"
            or not isinstance(bridge["sso_bridge_secret"], str)
            or not bridge["sso_bridge_secret"].strip()
        ):
            raise ValueError("candidate Bridge must match the existing registration")
        try:
            ssl.create_default_context(cafile=str(ca))
        except (OSError, ValueError):
            raise ValueError("candidate public CA is invalid or unreadable") from None
        bridge["sso_bridge_ca_file"] = str(ca)
    # 显式提供所有受保护别名，禁止 BaseSettings 从继承环境补入认证配置。
    data.update(
        {
            alias: enabled if name == "sso_bridge_enabled" else bridge[name]
            for name, alias in aliases.items()
        }
    )
    settings = Settings.model_validate(data)
    target = make_url(settings.database_url.get_secret_value())
    if (
        (target.drivername, target.host, target.port, target.database)
        != ("postgresql+psycopg", "127.0.0.1", 35427, DATABASE)
        or target.query
        or settings.minio_endpoint != "http://127.0.0.1:35428"
        or settings.minio_bucket != BUCKET
        or settings.toolkit_base_url != "http://127.0.0.1:18111"
        or settings.trusted_proxy_cidrs != ["127.0.0.1/32"]
    ):
        raise ValueError("candidate requires its dedicated resources")
    return settings


def create_candidate_app(settings):
    from integration.triad_runtime import create_triad_app

    app = create_triad_app(settings)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        from test_reception_graph import model

        from agents.agents import agents
        from xiaohe.contracts import AgentOutcome, WriteFence
        from xiaohe.graph import build_graph

        async with original(instance):
            previous = agents["xiaohe"].graph_like

            async def controlled(state):
                text = str(state["messages"][-1].content)
                if "需要补充" in text:
                    return AgentOutcome(
                        parts=[
                            {
                                "type": "input_request",
                                "requestId": "candidate-question",
                                "prompt": "请补充需求",
                            }
                        ],
                        wait_for_input=True,
                        artifact_version_ids=[],
                    )
                if "延迟" in text:
                    await asyncio.sleep(8)
                content = f"隔离候选文件：{state['direction']}".encode()
                fence = WriteFence.model_validate(state["write_fence"])
                artifacts = app.state.xiaohe_artifacts
                async with artifacts.factory.begin() as db:
                    version = await artifacts.stage_artifact(
                        db, fence, "候选说明.txt", "text/plain", hashlib.sha256(content).hexdigest()
                    )
                await artifacts.write_artifact_object(version.id, content)
                async with artifacts.factory.begin() as db:
                    await artifacts.finish_artifact(db, fence, version.id)
                return AgentOutcome(
                    parts=[{"type": "text", "text": "受控候选回复，未调用付费模型。"}],
                    wait_for_input=False,
                    artifact_version_ids=[version.id],
                )

            graph = build_graph(
                factory=app.state.xiaohe_factory,
                model=model(intent="professional"),
                handlers={name: controlled for name in ("buyer", "organization", "supplier")},
            )
            graph.checkpointer, graph.store = previous.checkpointer, previous.store
            agents["xiaohe"].graph_like = graph
            yield

    app.router.lifespan_context = lifespan
    return app


async def initialize(settings):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from xiaohe.jobs import build_job_app
    from xiaohe.models import System

    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    with engine.begin() as connection:
        config = Config("/app/alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with Session(engine) as db, db.begin():
        if db.get(System, "portal") is None:
            db.add(
                System(
                    system_id="portal",
                    tenant_id=1,
                    portal_client_id="candidate-only",
                    allowed_origin="https://portal.lvh.me",
                    issuer="https://sso.lvh.me",
                )
            )
        if db.get(System, "other") is None:
            db.add(
                System(
                    system_id="other",
                    tenant_id=2,
                    portal_client_id="candidate-other",
                    allowed_origin="https://portal.lvh.me",
                    issuer="https://sso.lvh.me",
                )
            )
    engine.dispose()

    async def never_execute(run_id):
        raise AssertionError("initialization cannot execute jobs")

    async with build_job_app(settings.database_url, never_execute).open_async() as jobs:
        await jobs.schema_manager.apply_schema_async()


async def main():
    kind, path = sys.argv[1:]
    settings = load_settings(path)
    if kind == "init":
        await initialize(settings)
        return
    if kind == "worker":
        # 产品 worker 仍从原有 Settings 环境入口读取；只注入本候选已验证配置。
        for name, field in Settings.model_fields.items():
            value = getattr(settings, name)
            if value is not None:
                if hasattr(value, "get_secret_value"):
                    value = value.get_secret_value()
                os.environ[str(field.validation_alias)] = (
                    json.dumps(value) if isinstance(value, (bool, list)) else str(value)
                )
        from xiaohe.worker import amain

        await amain()
        return
    if kind != "api":
        raise ValueError("expected init, api or worker")
    import dotenv

    original_find = dotenv.find_dotenv
    dotenv.find_dotenv = lambda *args, **kwargs: ""
    try:
        from core.settings import DatabaseType
        from core.settings import settings as upstream
    finally:
        dotenv.find_dotenv = original_find
    target = make_url(settings.database_url.get_secret_value())
    from pydantic import SecretStr

    upstream.DATABASE_TYPE = DatabaseType.POSTGRES
    upstream.POSTGRES_USER, upstream.POSTGRES_PASSWORD = target.username, SecretStr(target.password)
    upstream.POSTGRES_HOST, upstream.POSTGRES_PORT, upstream.POSTGRES_DB = (
        target.host,
        target.port,
        target.database,
    )
    upstream.AUTH_SECRET = settings.auth_secret
    import uvicorn

    await uvicorn.Server(
        uvicorn.Config(
            create_candidate_app(settings),
            host="127.0.0.1",
            port=18111,
            proxy_headers=False,
            access_log=False,
        )
    ).serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"candidate failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
