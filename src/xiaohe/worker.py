import asyncio
from contextlib import aclosing, suppress
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.artifacts import referenced_parts
from xiaohe.contracts import ChatMessage, ServiceError, ToolkitExecution
from xiaohe.database import create_database_engine
from xiaohe.events import persist_reply_chunk, record_event
from xiaohe.jobs import wake_claims
from xiaohe.models import Command, Conversation, Message, Run, RunAttempt, Session
from xiaohe.runs import (
    TERMINAL,
    _authorized,
    _locked,
    _status_event,
    acquire_run,
    reply_id,
    require_write,
)
from xiaohe.settings import Settings
from xiaohe.toolkit_client import ToolkitClient


class RunWorker:
    def __init__(self, factory, client: ToolkitClient):
        self.factory, self.client = factory, client

    async def finish_claim(self, claim_id):
        from xiaohe.claim import finish_pending_claim

        return await finish_pending_claim(self.factory, claim_id)

    async def maintain(self):
        from xiaohe.database import database_now
        from xiaohe.expiry import expire_guests

        async with self.factory.begin() as db:
            if await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended('xiaohe.expiry.scan',0))")
            ):
                await expire_guests(db, await database_now(db), 100)
        from xiaohe.jobs import defer_claim
        from xiaohe.models import GuestClaim

        async with self.factory() as db:
            pending = (
                await db.scalars(
                    select(GuestClaim.id)
                    .where(GuestClaim.status == "pending")
                    .order_by(GuestClaim.updated_at, GuestClaim.id)
                    .limit(100)
                )
            ).all()
        for claim_id in pending:
            await self.finish_claim(claim_id)
            async with self.factory.begin() as db:
                claim = await db.get(GuestClaim, claim_id)
                if claim.status == "pending":
                    claim.job_id = await defer_claim(db, claim)

    async def _save(self, fence, command_id, event, chunk_key=None):
        async with self.factory.begin() as db:
            if event.kind == "delta":
                await persist_reply_chunk(db, fence, event.message_id, chunk_key, event.delta)
                return
            run = await require_write(db, fence)
            conversation = await db.get(Conversation, run.conversation_id)
            command = await db.get(Command, command_id)
            message_id = reply_id(run.id, command.id)
            message = await db.get(Message, message_id)
            if event.kind == "error":
                raise ServiceError(event.code, 503, event.retryable)
            if message is None:
                message = Message(
                    id=message_id,
                    conversation_id=run.conversation_id,
                    system_id=run.system_id,
                    tenant_id=run.tenant_id,
                    role="assistant",
                    parts=[],
                )
                db.add(message)
                await db.flush()
            if event.kind == "outcome":
                outcome = event.outcome.model_dump(mode="json", by_alias=True)
                outcome["parts"] = await referenced_parts(
                    db, fence, event.outcome.artifact_version_ids, outcome["parts"]
                )
                questions = [
                    part for part in outcome["parts"] if part["type"] in ("choice", "input_request")
                ]
                if event.outcome.wait_for_input and len(questions) != 1:
                    raise ServiceError("INVALID_WAIT_STATE", 502)
                message.parts = outcome["parts"]
                record_event(
                    db,
                    conversation,
                    "message.upsert",
                    {
                        "message": ChatMessage(
                            id=str(message.id),
                            role="assistant",
                            created_at=message.created_at,
                            parts=message.parts,
                        ).model_dump(mode="json", by_alias=True)
                    },
                    run.id,
                )
                # Flush content under the same locked fence before the terminal transition.
                await db.flush()
                await require_write(db, fence)
                run.outcome = outcome
                run.pending_request = questions[0] if event.outcome.wait_for_input else None
                run.status = "waiting_input" if event.outcome.wait_for_input else "completed"
                run.holds_slot = event.outcome.wait_for_input
                command.completed_at = await db.scalar(select(func.clock_timestamp()))
                attempt = await db.get(RunAttempt, (run.id, run.attempt))
                attempt.status = run.status
                record_event(
                    db,
                    conversation,
                    "run.updated",
                    {"run": {"id": str(run.id), "status": run.status}},
                    run.id,
                )
                await wake_claims(db, conversation.id)
            await db.flush()

    async def _consume(self, fence, command_id, frame):
        chunks = 0
        async with aclosing(self.client.stream_execution(frame)) as stream:
            async for event in stream:
                if event.kind == "delta":
                    chunks += 1
                await self._save(fence, command_id, event, str(chunks))

    async def _watch(self, fence, guard):
        while True:
            await asyncio.sleep(1)
            await guard.execute(text("SELECT 1"))
            async with self.factory.begin() as db:
                await require_write(db, fence)

    async def _failed(self, fence, error):
        async with self.factory.begin() as db:
            try:
                run = await require_write(db, fence)
            except ServiceError as lost:
                run = await db.get(Run, fence.run_id)
                if (
                    run is None
                    or run.attempt != fence.attempt
                    or run.ownership_epoch != fence.ownership_epoch
                    or lost.code == "WRITE_FENCE_STALE"
                    or run.status in TERMINAL
                    or run.status == "waiting_input"
                ):
                    return False
                # Loss of authority only closes this attempt; it cannot publish content.
                run.status, run.holds_slot = "expired", False
                return False
            command = await db.scalar(
                select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
            )
            retry = error.retryable and command is not None and command.attempts < 3
            run.status, run.holds_slot = ("queued", True) if retry else ("failed", False)
            attempt = await db.get(RunAttempt, (run.id, run.attempt))
            attempt.status = "failed"
            if not retry and command is not None:
                command.completed_at = await db.scalar(select(func.clock_timestamp()))
            conversation = await db.get(Conversation, run.conversation_id)
            record_event(
                db,
                conversation,
                "run.updated",
                {"run": {"id": str(run.id), "status": run.status}},
                run.id,
            )
            if not retry:
                await wake_claims(db, conversation.id)
            return retry

    async def finalize_lost_run(self, run_id: UUID) -> bool:
        engine = self.factory.kw["bind"]
        async with engine.connect() as guard, guard.begin():
            if not await guard.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "xiaohe.run:" + str(run_id)},
            ):
                return False
            async with self.factory.begin() as db:
                run, conversation, _ = await _locked(db, run_id)
                if run.status in TERMINAL or run.status == "waiting_input":
                    await wake_claims(db, conversation.id)
                    return True
                run.status, run.holds_slot = "failed", False
                command = await db.scalar(
                    select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
                )
                if command is not None:
                    command.completed_at = await db.scalar(select(func.clock_timestamp()))
                attempt = await db.get(RunAttempt, (run.id, run.attempt))
                if attempt is not None:
                    attempt.status = "failed"
                record_event(
                    db,
                    conversation,
                    "run.updated",
                    {"run": {"id": str(run.id), "status": run.status}},
                    run.id,
                )
                await wake_claims(db, conversation.id)
            return True

    async def execute_run(self, run_id: UUID) -> None:
        engine = self.factory.kw["bind"]
        async with engine.connect() as guard, guard.begin():
            while not await guard.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "xiaohe.run:" + str(run_id)},
            ):
                async with self.factory.begin() as db:
                    row, conversation, session = await _locked(db, run_id)
                    if row.status in TERMINAL or row.status == "waiting_input":
                        return
                    try:
                        await _authorized(db, row, conversation, session)
                    except ServiceError as error:
                        row.status = "expired" if error.code == "EXECUTION_EXPIRED" else "failed"
                        row.holds_slot = False
                        command = await db.scalar(
                            select(Command).where(
                                Command.run_id == run_id, Command.completed_at.is_(None)
                            )
                        )
                        if command is not None:
                            command.completed_at = await db.scalar(select(func.clock_timestamp()))
                        _status_event(db, conversation, row)
                        await wake_claims(db, conversation.id)
                        return
                # A healthy waiter keeps its framework heartbeat; contention is not completion.
                await asyncio.sleep(1)
            async with self.factory.begin() as db:
                fence = await acquire_run(db, run_id, recover=True)
                if fence is None:
                    row = await db.get(Run, run_id)
                    await wake_claims(db, row.conversation_id)
                    return
                run = await db.get(Run, run_id)
                command = await db.scalar(
                    select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
                )
                session = await db.get(Session, run.session_id)
                frame = ToolkitExecution(
                    run_id=run.id,
                    attempt=fence.attempt,
                    ownership_epoch=fence.ownership_epoch,
                    user_id=run.user_id or session.toolkit_user_id,
                    thread_id=run.thread_id,
                    mode="recover"
                    if command.delivered_at is not None
                    else ("resume" if command.kind == "action" else "start"),
                )
                command_id = command.id
            consumer = asyncio.create_task(self._consume(fence, command_id, frame))
            watcher = asyncio.create_task(self._watch(fence, guard))
            try:
                done, _ = await asyncio.wait(
                    [consumer, watcher], return_when=asyncio.FIRST_COMPLETED
                )
                if consumer in done:
                    await consumer
                else:
                    await watcher
            except ServiceError as error:
                if await self._failed(fence, error):
                    raise
            except asyncio.CancelledError:
                raise
            except Exception:
                error = ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
                if await self._failed(fence, error):
                    raise error from None
            finally:
                for task in (consumer, watcher):
                    task.cancel()
                for task in (consumer, watcher):
                    with suppress(asyncio.CancelledError, Exception):
                        await task


async def amain():
    from xiaohe.jobs import build_job_app

    settings = Settings()
    if settings.database_url is None:
        raise ValueError("XIAOHE_DATABASE_URL is required")
    engine = create_database_engine(settings.database_url)
    worker = RunWorker(async_sessionmaker(engine, expire_on_commit=False), ToolkitClient(settings))
    try:
        async with build_job_app(
            settings.database_url,
            worker.execute_run,
            on_exhausted=worker.finalize_lost_run,
            on_maintenance=worker.maintain,
            on_claim=worker.finish_claim,
        ).open_async() as app:
            await app.run_worker_async()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(amain())
