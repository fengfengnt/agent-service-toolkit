"""仅用于真实子进程硬杀/互斥探针，不是产品 worker。"""

import asyncio
import os
import sys

import psycopg

from xiaohe.jobs import build_job_app, defer_run


async def main():
    url = os.environ["XIAOHE_TEST_DATABASE_URL"]
    mode = sys.argv[1]
    if mode == "admit-crash":
        from uuid import UUID

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from xiaohe.database import create_database_engine
        from xiaohe.models import System

        run_id = UUID(sys.argv[2])
        factory = async_sessionmaker(create_database_engine(url))
        async with factory.begin() as db:
            db.add(
                System(
                    system_id=str(run_id),
                    tenant_id=999,
                    portal_client_id="probe",
                    allowed_origin="https://probe.example",
                    issuer="https://issuer.example",
                )
            )
            await defer_run(db, run_id, "admission-crash-probe")
        print(f"COMMITTED:{run_id}", flush=True)
        os._exit(91)

    async def execute(run_id):
        print(f"STARTED:{run_id}", flush=True)
        if mode == "block":
            await asyncio.to_thread(sys.stdin.readline)
        async with await psycopg.AsyncConnection.connect(
            url.replace("postgresql+psycopg:", "postgresql:", 1), autocommit=True
        ) as conn:
            await conn.execute(
                "INSERT INTO xiaohe_probe_effects VALUES (%s,1) "
                "ON CONFLICT (run_id) DO UPDATE SET invocations=xiaohe_probe_effects.invocations+1",
                (run_id,),
            )
        print(f"FINISHED:{run_id}", flush=True)

    app = build_job_app(url, execute)
    async with app.open_async():
        print("READY", flush=True)
        await app.run_worker_async(
            queues=["xiaohe", "xiaohe-maintenance"] if mode == "recover" else ["xiaohe"],
            wait=(mode == "recover"),
            listen_notify=False,
            fetch_job_polling_interval=0.1 if mode != "recover" else 5,
            install_signal_handlers=False,
        )


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
