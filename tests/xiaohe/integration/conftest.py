import pytest_asyncio
from sqlalchemy import text

from xiaohe.jobs import build_job_app


@pytest_asyncio.fixture
async def job_app(pg_factory, pg_url):
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
