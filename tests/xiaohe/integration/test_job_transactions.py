import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from xiaohe.contracts import ServiceError
from xiaohe.jobs import RunRetryStrategy, build_job_app, defer_run, recover_stalled_jobs
from xiaohe.models import System

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("commit", [True, False])
async def test_business_and_job_commit_or_rollback_together(pg_factory, job_app, commit):
    run_id = uuid4()
    async with pg_factory() as db:
        async with db.begin():
            db.add(
                System(
                    system_id=str(run_id),
                    tenant_id=999,
                    portal_client_id="probe",
                    allowed_origin="https://probe.example",
                    issuer="https://issuer.example",
                )
            )
            job_id = await defer_run(db, run_id, "probe-conversation")
            driver = (await (await db.connection()).get_raw_connection()).driver_connection
            pid = driver.info.backend_pid
            assert await db.scalar(text("SELECT pg_backend_pid()")) == pid
            assert (
                await db.scalar(
                    text("SELECT id FROM procrastinate_jobs WHERE id=:id"), {"id": job_id}
                )
                == job_id
            )
            async with pg_factory() as observer:
                assert await observer.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == 0
                assert (
                    await observer.scalar(
                        select(func.count())
                        .select_from(System)
                        .where(System.system_id == str(run_id))
                    )
                    == 0
                )
            if not commit:
                await db.rollback()
    async with pg_factory() as db:
        assert await db.scalar(
            select(func.count()).select_from(System).where(System.system_id == str(run_id))
        ) == int(commit)
        assert await db.scalar(text("SELECT count(*) FROM procrastinate_jobs")) == int(commit)
        if commit:
            row = (
                await db.execute(text("SELECT args,lock,queueing_lock FROM procrastinate_jobs"))
            ).one()
            assert row.args == {"run_id": str(run_id)}
            assert row.lock == "probe-conversation" and row.queueing_lock is None


async def test_defer_requires_active_caller_transaction(pg_factory, job_app):
    async with pg_factory() as db:
        with pytest.raises(ValueError):
            await defer_run(db, uuid4(), "probe")


async def test_notification_loss_still_polls(pg_factory, job_app, pg_url):
    finished = asyncio.Event()

    async def execute(run_id):
        finished.set()

    app = build_job_app(pg_url, execute)
    async with app.open_async():
        worker = asyncio.create_task(
            app.run_worker_async(
                queues=["xiaohe"],
                listen_notify=False,
                fetch_job_polling_interval=0.05,
                install_signal_handlers=False,
            )
        )
        try:
            async with pg_factory.begin() as db:
                await defer_run(db, uuid4(), "probe")
            await asyncio.wait_for(finished.wait(), 5)
            async with pg_factory() as db:

                async def succeeded():
                    while (
                        await db.scalar(
                            text(
                                "SELECT status FROM procrastinate_jobs WHERE task_name='xiaohe.execute_run'"
                            )
                        )
                        != "succeeded"
                    ):
                        await db.rollback()

                await asyncio.wait_for(succeeded(), 5)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def test_retryable_errors_have_three_total_attempts(job_app):
    from procrastinate.jobs import Job

    strategy = RunRetryStrategy()
    for previous in range(4):
        job = Job(
            queue="xiaohe",
            lock="probe",
            queueing_lock=None,
            task_name="xiaohe.execute_run",
            attempts=previous,
        )
        retry = strategy.get_retry_decision(
            exception=ServiceError("UPSTREAM_UNAVAILABLE", 503, True), job=job
        )
        assert (retry is not None) == (previous < 2)
        for error in (
            ServiceError("FORBIDDEN", 403),
            ServiceError("VISITOR_EXPIRED", 410),
            ServiceError("CANCELLED", 409),
            RuntimeError("nonretryable"),
        ):
            assert strategy.get_retry_decision(exception=error, job=job) is None


async def test_recovery_respects_live_heartbeat_and_execution_budget(pg_factory, job_app):
    async with pg_factory.begin() as db:
        job_id = await defer_run(db, uuid4(), "crash-budget")
    worker_id = await job_app.job_manager.register_worker()
    for previous in range(3):
        job = await job_app.job_manager.fetch_job(queues=["xiaohe"], worker_id=worker_id)
        assert job is not None and job.attempts == previous
        assert await recover_stalled_jobs(job_app) == 0
        assert await recover_stalled_jobs(job_app, seconds_since_heartbeat=0) == (
            1 if previous < 2 else 0
        )
        async with pg_factory.begin() as db:
            if previous < 2:
                await db.execute(
                    text(
                        "UPDATE procrastinate_jobs SET scheduled_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": job_id},
                )
            else:
                assert (
                    await db.scalar(
                        text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": job_id}
                    )
                    == "failed"
                )


async def test_recovery_scan_is_not_blocked_by_a_stalled_scan(pg_factory, job_app):
    queue = "xiaohe-maintenance"
    old_id = await job_app.configure_task("xiaohe.recover_stalled").defer_async(timestamp=1)
    old_worker = await job_app.job_manager.register_worker()
    assert (await job_app.job_manager.fetch_job(queues=[queue], worker_id=old_worker)).id == old_id
    async with pg_factory.begin() as db:
        await db.execute(
            text(
                "UPDATE procrastinate_workers SET last_heartbeat=clock_timestamp()-interval '121 seconds' WHERE id=:id"
            ),
            {"id": old_worker},
        )
    new_id = await job_app.configure_task("xiaohe.recover_stalled").defer_async(timestamp=2)
    new_worker = await job_app.job_manager.register_worker()
    current = await job_app.job_manager.fetch_job(queues=[queue], worker_id=new_worker)
    assert current is not None and current.id == new_id
    assert await recover_stalled_jobs(job_app) == 0
    async with pg_factory() as db:
        assert (
            await db.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": old_id}
            )
            == "failed"
        )


async def test_concurrent_recovery_scans_do_not_retry_the_same_job_twice(pg_factory, job_app):
    async with pg_factory.begin() as db:
        await defer_run(db, uuid4(), "concurrent-recovery")
    worker = await job_app.job_manager.register_worker()
    await job_app.job_manager.fetch_job(queues=["xiaohe"], worker_id=worker)
    result = await asyncio.gather(
        recover_stalled_jobs(job_app, seconds_since_heartbeat=0),
        recover_stalled_jobs(job_app, seconds_since_heartbeat=0),
    )
    assert sum(result) == 1
