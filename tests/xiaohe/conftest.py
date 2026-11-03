import asyncio
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio


@pytest.fixture
def contract_fixture():
    def load(name):
        return json.loads((Path(__file__).parent / "contracts" / f"{name}.json").read_text("utf-8"))

    return load


def pytest_asyncio_loop_factories(config, item):
    if item.get_closest_marker("pg") is not None:
        return {"postgres": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture
def pg_url():
    from sqlalchemy.engine import make_url

    value = os.environ.get("XIAOHE_TEST_DATABASE_URL")
    if not value or not (make_url(value).database or "").startswith("test_xiaohe_"):
        pytest.fail("pg 测试必须显式提供 XIAOHE_TEST_DATABASE_URL，数据库名以 test_xiaohe_ 开头")
    return value


@pytest_asyncio.fixture
async def pg_factory(pg_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from xiaohe.database import create_database_engine
    from xiaohe.models import Base, System

    engine = create_engine(pg_url, hide_parameters=True)
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        tables = ", ".join(f'"xiaohe"."{table.name}"' for table in Base.metadata.sorted_tables)
        connection.execute(text("TRUNCATE " + tables))
    engine.dispose()
    engine = create_database_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as db:
        db.add_all(
            [
                System(
                    system_id="portal",
                    tenant_id=1,
                    portal_client_id="app_localportal",
                    allowed_origin="https://portal.lvh.me:9444",
                    issuer="https://sso.lvh.me:8443",
                ),
                System(
                    system_id="other",
                    tenant_id=2,
                    portal_client_id="app_other",
                    allowed_origin="https://other.example",
                    issuer="https://sso.lvh.me:8443",
                ),
            ]
        )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def job_app(pg_factory, pg_url):
    from sqlalchemy import text

    from xiaohe.jobs import build_job_app

    async def execute(run_id):
        raise AssertionError("fixture must not execute business tasks")

    app = build_job_app(pg_url, execute)
    async with app.open_async():
        async with pg_factory.begin() as db:
            exists = await db.scalar(text("SELECT to_regclass('public.procrastinate_jobs')"))
        if not exists:
            await app.schema_manager.apply_schema_async()
        async with pg_factory.begin() as db:
            await db.execute(
                text(
                    "TRUNCATE public.procrastinate_events, public.procrastinate_periodic_defers, "
                    "public.procrastinate_jobs, public.procrastinate_workers"
                )
            )
            await db.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS public.xiaohe_probe_effects "
                    "(run_id uuid PRIMARY KEY, invocations int NOT NULL)"
                )
            )
            await db.execute(text("TRUNCATE public.xiaohe_probe_effects"))
        yield app
