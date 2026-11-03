from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr

from xiaohe.main import create_app
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_upstream_lifespan_runs_once_and_binds_xiaohe(
    pg_factory, pg_url, runtime_env, monkeypatch
):
    from agents import get_agent
    from core.settings import DatabaseType
    from core.settings import settings as upstream
    from schema import AgentInfo
    from service import service

    calls = []

    @asynccontextmanager
    async def saver():
        calls.append("saver-open")
        yield runtime_env.graph.checkpointer
        calls.append("saver-close")

    @asynccontextmanager
    async def store():
        calls.append("store-open")
        yield runtime_env.graph.store
        calls.append("store-close")

    monkeypatch.setattr(service, "initialize_database", saver)
    monkeypatch.setattr(service, "initialize_store", store)
    monkeypatch.setattr(
        service, "get_all_agent_info", lambda: [AgentInfo(key="xiaohe", description="fixture")]
    )
    monkeypatch.setattr(upstream, "DATABASE_TYPE", DatabaseType.POSTGRES)
    settings = Settings(
        database_url=pg_url,
        toolkit_base_url="http://runtime",
        auth_secret="fixture-auth",
        trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert calls == ["saver-open", "store-open"]
        assert get_agent("xiaohe").checkpointer is runtime_env.graph.checkpointer
        assert get_agent("xiaohe").store is runtime_env.graph.store
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://portal.lvh.me:9444"
        ) as client:
            assert (await client.get("/internal/toolkit/info")).status_code == 401
            native = await client.get(
                "/internal/toolkit/info", headers={"Authorization": "Bearer fixture-auth"}
            )
            assert native.status_code == 200 and "default_agent" in native.json()
            public = await client.get(
                "/ai-api/v1/conversations/00000000-0000-0000-0000-000000000001",
                headers={"Authorization": "Bearer fixture-auth", "X-Xiaohe-System-Id": "portal"},
            )
            assert public.status_code == 401
            assert (await client.get("/docs")).status_code == 404
    assert calls == ["saver-open", "store-open", "store-close", "saver-close"]


async def test_composition_rejects_missing_key_and_non_postgres(
    pg_factory, pg_url, runtime_env, monkeypatch
):
    from core.settings import DatabaseType
    from core.settings import settings as upstream

    settings = Settings(
        database_url=pg_url, toolkit_base_url="http://runtime", auth_secret="fixture-auth"
    )
    monkeypatch.setattr(upstream, "AUTH_SECRET", None)
    with pytest.raises(ValueError):
        create_app(settings)
    monkeypatch.setattr(upstream, "AUTH_SECRET", SecretStr("fixture-auth"))
    monkeypatch.setattr(upstream, "DATABASE_TYPE", DatabaseType.SQLITE)
    with pytest.raises(ValueError):
        create_app(settings)
