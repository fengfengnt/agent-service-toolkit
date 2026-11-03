import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from xiaohe.jobs import defer_run, recover_stalled_jobs

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def start_worker(mode, *args):
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("worker_probe.py")), mode, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )


async def line(process, expected):
    value = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
    assert value.strip() == expected, (value, process.poll())


async def stop(process):
    if process.poll() is None:
        process.kill()
    await asyncio.to_thread(process.wait, 10)
    for pipe in (process.stdin, process.stdout, process.stderr):
        pipe.close()


async def test_hard_kill_is_recoverable(pg_factory, job_app):
    run_id = uuid4()
    async with pg_factory.begin() as db:
        job_id = await defer_run(db, run_id, "recovery-probe")
    victim = start_worker("block")
    replacement = None
    try:
        await line(victim, "READY")
        await line(victim, f"STARTED:{run_id}")
        async with pg_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": job_id}
                )
                == "doing"
            )
        victim.kill()
        await asyncio.to_thread(victim.wait, 10)
        killed_at = time.monotonic()
        real_timing = os.environ.get("XIAOHE_PROBE_REAL_TIMING") == "1"
        if not real_timing:
            assert await recover_stalled_jobs(job_app, seconds_since_heartbeat=0) == 1
        replacement = start_worker("recover")
        await line(replacement, "READY")
        async with pg_factory() as db:

            async def completed():
                while True:
                    status = await db.scalar(
                        text("SELECT status FROM procrastinate_jobs WHERE id=:id"), {"id": job_id}
                    )
                    if status == "succeeded":
                        return
                    await db.rollback()
                    await asyncio.sleep(0.1)

            await asyncio.wait_for(completed(), 180 if real_timing else 15)
            elapsed = time.monotonic() - killed_at
            assert (
                await db.scalar(
                    text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:id"),
                    {"id": run_id},
                )
                == 1
            )
        if real_timing:
            Path(".superpowers/sdd/2026-11-03-xiaohe-ai-backend/p2-real-recovery.json").write_text(
                json.dumps(
                    {
                        "elapsed_seconds": elapsed,
                        "heartbeat_seconds": 10,
                        "stalled_seconds": 120,
                        "scan_seconds": 30,
                        "target_seconds": 180,
                    }
                ),
                encoding="utf-8",
            )
        assert elapsed < 180
    finally:
        await stop(victim)
        if replacement:
            await stop(replacement)


async def test_two_workers_with_same_lock_cannot_overlap(pg_factory, job_app):
    ids = [uuid4(), uuid4()]
    async with pg_factory.begin() as db:
        for run_id in ids:
            await defer_run(db, run_id, "one-conversation")
    first = start_worker("block")
    second = None
    try:
        await line(first, "READY")
        await line(first, f"STARTED:{ids[0]}")
        second = start_worker("finish")
        await line(second, "READY")
        assert await asyncio.wait_for(asyncio.to_thread(second.wait), 15) == 0
        async with pg_factory() as db:
            statuses = (
                (
                    await db.execute(
                        text(
                            "SELECT status FROM procrastinate_jobs WHERE task_name='xiaohe.execute_run' ORDER BY id"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert statuses == ["doing", "todo"]
        first.stdin.write("release\nrelease\n")
        first.stdin.flush()
        assert await asyncio.wait_for(asyncio.to_thread(first.wait), 15) == 0
        async with pg_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM xiaohe_probe_effects WHERE invocations=1")
                )
                == 2
            )
    finally:
        await stop(first)
        if second:
            await stop(second)


async def test_committed_admission_survives_process_exit_before_response(pg_factory, job_app):
    run_id = uuid4()
    process = start_worker("admit-crash", str(run_id))
    worker = None
    try:
        await line(process, f"COMMITTED:{run_id}")
        assert await asyncio.wait_for(asyncio.to_thread(process.wait), 15) == 91
        async with pg_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM xiaohe.systems WHERE system_id=:id"),
                    {"id": str(run_id)},
                )
                == 1
            )
            assert (
                await db.scalar(
                    text("SELECT status FROM procrastinate_jobs WHERE args->>'run_id'=:id"),
                    {"id": str(run_id)},
                )
                == "todo"
            )
        worker = start_worker("finish")
        assert await asyncio.wait_for(asyncio.to_thread(worker.wait), 15) == 0
        async with pg_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:id"),
                    {"id": run_id},
                )
                == 1
            )
    finally:
        await stop(process)
        if worker:
            await stop(worker)
