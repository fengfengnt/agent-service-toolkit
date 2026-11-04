from collections.abc import Awaitable, Callable
from uuid import UUID

from procrastinate import App, BaseRetryStrategy, PsycopgConnector, RetryDecision
from procrastinate.jobs import Job, Status
from psycopg import AsyncConnection
from psycopg.rows import tuple_row
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import ServiceError

RUN_TASK = "xiaohe.execute_run"
RUN_QUEUE = "xiaohe"
MAINTENANCE_QUEUE = "xiaohe-maintenance"
RECOVERY_TASK = "xiaohe.recover_stalled"
RECOVERY_CRON = "* * * * * */30"
HEARTBEAT_SECONDS = 10
STALLED_SECONDS = 120
MAX_EXECUTIONS = 3

_defer_app = App(connector=PsycopgConnector())


class RunRetryStrategy(BaseRetryStrategy):
    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if (
            isinstance(exception, ServiceError)
            and exception.retryable
            and job.attempts < MAX_EXECUTIONS - 1
        ):
            return RetryDecision(retry_in={"seconds": 2**job.attempts})
        return None


async def defer_run(db: AsyncSession, run_id: UUID, lock_key: str) -> int:
    if not db.in_transaction():
        raise ValueError("defer_run requires a caller-owned business transaction")
    if not isinstance(run_id, UUID) or not lock_key.strip():
        raise ValueError("trusted run UUID and nonblank execution lock required")
    await db.flush()
    connection = await db.connection()
    raw = await connection.get_raw_connection()
    driver = raw.driver_connection
    if not isinstance(driver, AsyncConnection) or driver.autocommit:
        raise ValueError("defer_run requires the caller's transactional psycopg AsyncConnection")
    return await _defer_app.configure_task(
        RUN_TASK,
        queue=RUN_QUEUE,
        lock=lock_key,
        connection=driver,
    ).defer_async(run_id=str(run_id))


async def cancel_run_job(db: AsyncSession, job_id: int) -> bool:
    await db.flush()
    raw = await (await db.connection()).get_raw_connection()
    return await _defer_app.job_manager.cancel_job_by_id_async(
        job_id, connection=raw.driver_connection
    )


async def recover_stalled_jobs(
    app: App,
    *,
    seconds_since_heartbeat: float = STALLED_SECONDS,
    on_exhausted: Callable[[UUID], Awaitable[bool]] | None = None,
) -> int:
    # A transaction mutex releases on death; a stalled scan must not lock its successor.
    async with app.connector.pool.connection() as guard, guard.transaction():
        async with guard.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('xiaohe.recovery.scan',0))"
            )
            if not (await cursor.fetchone())[0]:
                return 0
        maintenance = await app.job_manager.get_stalled_jobs(
            queue=MAINTENANCE_QUEUE,
            task_name=RECOVERY_TASK,
            seconds_since_heartbeat=seconds_since_heartbeat,
        )
        for job in maintenance:
            await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
        recovered = 0
        jobs = await app.job_manager.get_stalled_jobs(
            queue=RUN_QUEUE,
            task_name=RUN_TASK,
            seconds_since_heartbeat=seconds_since_heartbeat,
        )
        strategy = RunRetryStrategy()
        for job in jobs:
            decision = strategy.get_retry_decision(
                exception=ServiceError("WORKER_LOST", 503, True),
                job=job,
            )
            if decision is None:
                if on_exhausted is not None and not await on_exhausted(
                    UUID(job.task_kwargs["run_id"])
                ):
                    continue
                await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
            else:
                await app.job_manager.retry_job(job, retry_at=decision.retry_at)
                recovered += 1
        return recovered


def build_job_app(
    database_url: str | SecretStr,
    execute_run: Callable[[UUID], Awaitable[None]],
    *,
    on_exhausted: Callable[[UUID], Awaitable[bool]] | None = None,
    on_maintenance: Callable[[], Awaitable[None]] | None = None,
) -> App:
    value = database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("job database requires postgresql+psycopg")
    connector = PsycopgConnector(
        conninfo=url.set(drivername="postgresql").render_as_string(hide_password=False),
        min_size=1,
        max_size=5,
    )
    app = App(
        connector=connector,
        worker_defaults={
            "queues": [RUN_QUEUE, MAINTENANCE_QUEUE],
            "update_heartbeat_interval": HEARTBEAT_SECONDS,
            "stalled_worker_timeout": STALLED_SECONDS,
            "fetch_job_polling_interval": 5,
        },
    )

    @app.task(name=RUN_TASK, queue=RUN_QUEUE, retry=RunRetryStrategy())
    async def run(run_id: str):
        await execute_run(UUID(run_id))

    @app.periodic(cron=RECOVERY_CRON)
    @app.task(name=RECOVERY_TASK, queue=MAINTENANCE_QUEUE, queueing_lock="xiaohe.recovery.scan")
    async def recovery(timestamp: int):
        await recover_stalled_jobs(app, on_exhausted=on_exhausted)
        if on_maintenance is not None:
            await on_maintenance()

    return app
