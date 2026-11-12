"""显式维护入口：仅用于用户授权的 WSL 旧会话清理，不由应用启动调用。"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.artifacts import ArtifactService
from xiaohe.database import create_database_engine
from xiaohe.settings import Settings


async def delete_objects(artifacts, keys):
    if any(not key.startswith("xiaohe/") for key in keys):
        raise ValueError("object ownership is not proven")
    if keys:
        await artifacts.verify_unversioned_cleanup()
    for key in keys:
        await artifacts.delete_expired_object(key)
        await artifacts.verify_object_absent(key)


async def purge_legacy_data(factory, artifacts, *, expected_database, manifest_path):
    if expected_database != "xiaohe_triad_local_20261104" and not expected_database.startswith(
        "test_xiaohe_"
    ):
        raise ValueError("database is outside the authorized WSL/test scope")
    async with factory.begin() as db:
        if await db.scalar(text("SELECT current_database()")) != expected_database:
            raise ValueError("database identity mismatch")
        if not await db.scalar(
            text(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('xiaohe.standard-only-cleanup',0))"
            )
        ):
            raise ValueError("another cleanup owns the database")
        # 所有小鹤写服务须先停止；表锁防止清单生成后继续接纳新消息。
        await db.execute(
            text(
                "LOCK TABLE xiaohe.conversations, xiaohe.runs, xiaohe.commands, xiaohe.reception_drafts, xiaohe.reception_files, xiaohe.reception_form_files, xiaohe.reception_form_submissions, xiaohe.artifact_versions IN ACCESS EXCLUSIVE MODE"
            )
        )
        native = await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM xiaohe.runs WHERE deep_runtime_bundle_id IS NOT NULL) OR EXISTS(SELECT 1 FROM xiaohe.commands WHERE payload ? 'deepExecution')"
            )
        )
        if native:
            raise ValueError("new native data exists; redefine the legacy scope")
        formal = await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM xiaohe.reception_form_submissions) OR EXISTS(SELECT 1 FROM xiaohe.reception_submissions WHERE status<>'failed' AND jsonb_array_length(coalesce(payload->'files','[]'::jsonb))>0)"
            )
        )
        if formal:
            raise ValueError("formal application data or shared attachment reference exists")
        conversations = list(
            await db.scalars(text("SELECT id FROM xiaohe.conversations ORDER BY id"))
        )
        runs = list(await db.scalars(text("SELECT id FROM xiaohe.runs ORDER BY id")))
        claims = list(await db.scalars(text("SELECT id FROM xiaohe.guest_claims ORDER BY id")))
        operations = list(await db.scalars(text("SELECT operation_id FROM xiaohe.guest_claims")))
        threads = set(
            await db.scalars(
                text(
                    "SELECT thread_id FROM xiaohe.conversations UNION SELECT thread_id FROM xiaohe.runs UNION SELECT thread_id FROM xiaohe.run_attempts"
                )
            )
        ) - {None}
        objects = set(
            await db.scalars(
                text(
                    "SELECT object_key FROM xiaohe.artifact_versions UNION SELECT object_key FROM xiaohe.reception_files UNION SELECT object_key FROM xiaohe.reception_form_files"
                )
            )
        )
        for value in await db.scalars(
            text(
                "SELECT object_keys FROM xiaohe.reception_files UNION ALL SELECT object_keys FROM xiaohe.reception_form_files"
            )
        ):
            objects.update(value)
        jobs = []
        if await db.scalar(text("SELECT to_regclass('public.procrastinate_jobs')")):
            jobs = list(
                await db.scalars(
                    text(
                        "SELECT id FROM public.procrastinate_jobs WHERE (task_name='xiaohe.execute_run' AND args->>'run_id'=ANY(:runs)) OR (task_name='xiaohe.finish_claim' AND args->>'claim_id'=ANY(:claims))"
                    ),
                    {"runs": [str(v) for v in runs], "claims": [str(v) for v in claims]},
                )
            )
        manifest = {
            "database": expected_database,
            "conversations": [str(v) for v in conversations],
            "runs": [str(v) for v in runs],
            "claims": [str(v) for v in claims],
            "threads": sorted(threads),
            "objects": sorted(objects),
            "jobs": sorted(jobs),
        }
        if any(not key.startswith("xiaohe/") for key in objects):
            raise ValueError("object ownership is not proven")
        path = Path(manifest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8"
        ) as stream:
            json.dump(manifest, stream, indent=2)
        await delete_objects(artifacts, sorted(objects))
        if await db.scalar(text("SELECT to_regclass('public.checkpoints')")):
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await db.execute(
                    text(f"DELETE FROM public.{table} WHERE thread_id=ANY(:threads)"),
                    {"threads": sorted(threads)},
                )
        if jobs:
            for table in ("procrastinate_events", "procrastinate_periodic_defers"):
                await db.execute(
                    text(f"DELETE FROM public.{table} WHERE job_id=ANY(:jobs)"), {"jobs": jobs}
                )
            await db.execute(
                text("DELETE FROM public.procrastinate_jobs WHERE id=ANY(:jobs)"), {"jobs": jobs}
            )
        await db.execute(
            text(
                "UPDATE xiaohe.sessions SET merge_operation_id=NULL WHERE merge_operation_id=ANY(:operations)"
            ),
            {"operations": operations},
        )
        await db.execute(
            text("DELETE FROM xiaohe.guest_object_tombstones WHERE object_key=ANY(:objects)"),
            {"objects": sorted(objects)},
        )
        for table in (
            "guest_claims",
            "reception_form_files",
            "reception_submissions",
            "reception_files",
            "reception_flows",
            "reception_drafts",
            "artifact_versions",
            "artifacts",
            "events",
            "event_tombstones",
            "commands",
            "run_attempts",
            "runs",
            "messages",
            "conversations",
        ):
            await db.execute(text(f"DELETE FROM xiaohe.{table}"))
        if await db.scalar(text("SELECT to_regclass('xiaohe.workflow_definitions')")):
            await db.execute(text("DELETE FROM xiaohe.workflow_definitions"))
        await db.execute(text("DELETE FROM xiaohe.deep_runtime_bundles"))
        await db.execute(text("DELETE FROM xiaohe.lead_policies"))
        return {key: len(value) for key, value in manifest.items() if isinstance(value, list)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--confirm-delete-legacy", action="store_true", required=True)
    args = parser.parse_args()
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    artifacts = ArtifactService(factory, settings)
    try:
        result = await purge_legacy_data(
            factory,
            artifacts,
            expected_database=args.expected_database,
            manifest_path=args.manifest,
        )
        print(json.dumps({"deleted": result}))
    finally:
        await artifacts.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
