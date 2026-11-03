from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from integration.test_runtime_crashes import line, start, stop

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_two_composed_processes_initialize_original_pools_on_fresh_database(
    pg_url, monkeypatch
):
    target = make_url(pg_url)
    name = "test_xiaohe_startup_" + uuid4().hex
    control = target.set(drivername="postgresql").render_as_string(hide_password=False)
    processes = []
    async with await psycopg.AsyncConnection.connect(control, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            fresh = target.set(database=name).render_as_string(hide_password=False)
            monkeypatch.setenv("XIAOHE_TEST_DATABASE_URL", fresh)
            processes = [start("composition", "normal", 0), start("composition", "normal", 0)]
            for process in processes:
                await line(process, "READY")
            async with await psycopg.AsyncConnection.connect(
                target.set(drivername="postgresql", database=name).render_as_string(
                    hide_password=False
                ),
                autocommit=True,
            ) as fresh_connection:
                row = await (
                    await fresh_connection.execute("""SELECT
                    to_regclass('public.checkpoints') IS NOT NULL,
                    to_regclass('public.store') IS NOT NULL,
                    has_table_privilege(current_user,'public.checkpoints','SELECT,INSERT,UPDATE'),
                    has_table_privilege(current_user,'public.store','SELECT,INSERT,UPDATE')""")
                ).fetchone()
                assert row == (True, True, True, True)
            assert all(process.poll() is None for process in processes)
        finally:
            for process in processes:
                await stop(process)
            await connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
