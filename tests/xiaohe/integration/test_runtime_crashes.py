import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, text
from test_admission import send, visitor

from xiaohe.admission import accept_action
from xiaohe.jobs import recover_stalled_jobs
from xiaohe.models import Command, Message, Run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def start(kind, mode, port):
    log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("runtime_probe.py")), kind, mode, str(port)],
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "PYTHONPATH": str(Path("src").resolve()),
            "OPENAI_API_KEY": "sk-fake-openai-key",
        },
    )
    process.probe_log = log
    return process


async def line(process, expected):
    try:
        value = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 30)
    except TimeoutError:
        process.probe_log.seek(0)
        raise AssertionError(
            ("probe readiness timeout", process.poll(), process.probe_log.read()[-2500:])
        ) from None
    process.probe_log.seek(0)
    assert value.strip() == expected, (value, process.poll(), process.probe_log.read()[-2500:])


async def stop(process):
    if process.poll() is None:
        process.kill()
    await asyncio.to_thread(process.wait, 10)
    process.stdout.close()
    process.probe_log.close()


async def ready_api(mode, port):
    process = start("api", mode, port)
    try:
        await line(process, "READY")
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(100):
                try:
                    await client.get(f"http://127.0.0.1:{port}/openapi.json")
                    return process
                except httpx.ConnectError:
                    await asyncio.sleep(0.02)
        raise AssertionError("probe API did not listen")
    except BaseException:
        await stop(process)
        raise


@pytest.mark.parametrize("window", ["before-input", "after-checkpoint", "after-action"])
async def test_kill_after_checkpoint_recovers_same_run(pg_factory, job_app, window):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    processes = []
    who, conversation = await visitor(pg_factory)
    accepted = await send(pg_factory, who, conversation, "initial")
    run_id = UUID(accepted.run.id)
    try:
        api = await ready_api(
            "marker"
            if window == "before-input"
            else ("question" if window == "after-action" else "normal"),
            port,
        )
        processes.append(api)
        worker = start("worker", "normal" if window != "after-checkpoint" else "gate", port)
        processes.append(worker)
        await line(worker, "READY")
        if window == "after-action":
            assert await asyncio.wait_for(asyncio.to_thread(worker.wait), 30) == 0
            async with pg_factory.begin() as db:
                run = await db.get(Run, run_id)
                assert run.status == "waiting_input"
                await accept_action(db, who, conversation, "reply", "Q1", None, "补充内容")
            worker = start("worker", "gate", port)
            processes.append(worker)
            await line(worker, "READY")
        await line(api if window == "before-input" else worker, "GATED")
        async with pg_factory() as db:
            pending = await db.scalar(
                select(Command).where(Command.run_id == run_id, Command.completed_at.is_(None))
            )
            assert pending.delivered_at is not None
            before_attempt = (await db.get(Run, run_id)).attempt
        await stop(worker)
        processes.remove(worker)
        if window == "before-input":
            await stop(api)
            processes.remove(api)
            api = await ready_api("normal", port)
            processes.append(api)
        killed_at = time.monotonic()
        # Only after confirmed death: exercise the framework scan without the 120s wait.
        real_timing = os.environ.get("XIAOHE_RUNTIME_REAL_TIMING") == "1"
        if not real_timing:
            assert await recover_stalled_jobs(job_app, seconds_since_heartbeat=0) == 1
        replacement = start("worker", "recovery" if real_timing else "normal", port)
        processes.append(replacement)
        await line(replacement, "READY")
        if real_timing:

            async def completed():
                while True:
                    async with pg_factory() as db:
                        if (await db.get(Run, run_id)).status == "completed":
                            return
                    await asyncio.sleep(0.1)

            await asyncio.wait_for(completed(), 180)
            Path(".superpowers/sdd/2026-11-03-xiaohe-ai-backend/p4-real-recovery.json").write_text(
                json.dumps(
                    {
                        "window": window,
                        "elapsed_seconds": time.monotonic() - killed_at,
                        "heartbeat_seconds": 10,
                        "stalled_seconds": 120,
                        "scan_seconds": 30,
                        "target_seconds": 180,
                    }
                ),
                encoding="utf-8",
            )
        else:
            assert await asyncio.wait_for(asyncio.to_thread(replacement.wait), 30) == 0
        async with pg_factory() as db:
            run = await db.get(Run, run_id)
            assert run.status == "completed" and run.attempt == before_attempt + 1
            expected = 2 if window == "after-action" else 1
            assert (
                await db.scalar(
                    select(func.count()).select_from(Message).where(Message.role == "assistant")
                )
                == expected
            )
            assert (
                await db.scalar(
                    text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:id"),
                    {"id": run_id},
                )
                == expected
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Command)
                    .where(Command.completed_at.is_not(None))
                )
                == expected
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs WHERE status='succeeded' AND task_name='xiaohe.execute_run'"
                    )
                )
                == expected
            )
        assert time.monotonic() - killed_at < 180
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from xiaohe.graph import build_graph

        dsn = os.environ["XIAOHE_TEST_DATABASE_URL"].replace(
            "postgresql+psycopg:", "postgresql:", 1
        )
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            recovered = build_graph()
            recovered.checkpointer = saver
            state = await recovered.aget_state({"configurable": {"thread_id": run.thread_id}})
        humans = [message for message in state.values["messages"] if message.type == "human"]
        assert len(humans) == expected and len({message.id for message in humans}) == expected
    finally:
        for process in reversed(processes):
            await stop(process)
