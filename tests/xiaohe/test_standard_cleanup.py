import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def legacy_factory(pg_url, request):
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from xiaohe.database import create_database_engine
    from xiaohe.deep_runtime.runtime import publish_runtime
    from xiaohe.jobs import build_job_app
    from xiaohe.models import System
    from xiaohe.resources import RESOURCE_ROOT

    database = "test_xiaohe_cleanup_" + uuid4().hex
    source = make_url(pg_url)
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql('CREATE DATABASE "' + database + '"')
    url = source.set(database=database).render_as_string(hide_password=False)
    sync = create_engine(url)
    with sync.begin() as conn:
        config = Config("alembic.ini")
        config.attributes["connection"] = conn
        command.upgrade(config, "0014")
        # 在旧结构中构造新入口记录，供“发现新原生数据就拒绝清理”的测试使用。
        conn.exec_driver_sql(
            "ALTER TABLE xiaohe.runs ALTER COLUMN runtime_version SET DEFAULT 'deepagent-v1'"
        )
    sync.dispose()
    engine = create_database_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as db:
        db.add(
            System(
                system_id="portal",
                tenant_id=1,
                portal_client_id="app_localportal",
                allowed_origin="https://portal.lvh.me:9444",
                issuer="https://sso.lvh.me:8443",
            )
        )
    if getattr(request, "param", True):
        await publish_runtime(factory, RESOURCE_ROOT)
    app = build_job_app(url, lambda _: None)
    async with app.open_async():
        await app.schema_manager.apply_schema_async()
    try:
        yield factory, url, database
    finally:
        await engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql('DROP DATABASE "' + database + '" WITH (FORCE)')
        admin.dispose()


async def seed_old_chat(factory):
    from uuid import UUID

    from sqlalchemy import text
    from test_admission import send, visitor

    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.models import ReceptionFile
    from xiaohe.reception.store import create_draft

    who, conversation = await visitor(factory)
    accepted = await send(factory, who, conversation, "旧会话")
    async with factory.begin() as db:
        await db.execute(
            text(
                "UPDATE xiaohe.runs SET deep_runtime_bundle_id=NULL, runtime_version='legacy',thread_id='owned-old-thread'"
            )
        )
        await db.execute(text("UPDATE xiaohe.commands SET payload=payload-'deepExecution'"))
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="old", kind="service", direction="buyer"),
        )
        db.add(
            ReceptionFile(
                client_upload_id="old",
                upload_request={},
                draft_id=UUID(draft.id),
                conversation_id=conversation,
                system_id="portal",
                tenant_id=1,
                category="material",
                filename="old.pdf",
                media_type="application/pdf",
                bytes=3,
                status="ready",
                object_key="xiaohe/reception/old",
                object_keys=["xiaohe/reception/old"],
            )
        )
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            await db.execute(text(f"CREATE TABLE public.{table}(thread_id text)"))
            await db.execute(
                text(
                    f"INSERT INTO public.{table}(thread_id) VALUES('owned-old-thread'),('unrelated-thread')"
                )
            )
    return accepted


@pytest.mark.pg
@pytest.mark.asyncio
async def test_database_cleanup_preserves_shared_state_and_repeats(legacy_factory, tmp_path):
    from sqlalchemy import text

    from xiaohe.maintenance import purge_legacy_data

    factory, _, database = legacy_factory
    await seed_old_chat(factory)

    class Objects:
        keys = {"xiaohe/reception/old", "unrelated/keep"}

        async def verify_unversioned_cleanup(self):
            pass

        async def delete_expired_object(self, key):
            self.keys.discard(key)

        async def verify_object_absent(self, key):
            assert key not in self.keys

    objects = Objects()
    async with factory() as db:
        identities = await db.scalar(text("SELECT count(*) FROM xiaohe.sessions"))
        jobs = await db.scalar(text("SELECT count(*) FROM public.procrastinate_jobs"))
    result = await purge_legacy_data(
        factory, objects, expected_database=database, manifest_path=tmp_path / "manifest.json"
    )
    assert result["runs"] == 1 and result["objects"] == 1
    assert objects.keys == {"unrelated/keep"}
    async with factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM xiaohe.sessions")) == identities
        for table in (
            "conversations",
            "messages",
            "commands",
            "runs",
            "reception_drafts",
            "reception_files",
            "lead_policies",
            "deep_runtime_bundles",
        ):
            assert await db.scalar(text(f"SELECT count(*) FROM xiaohe.{table}")) == 0
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            assert list(await db.scalars(text(f"SELECT thread_id FROM public.{table}"))) == [
                "unrelated-thread"
            ]
        assert await db.scalar(text("SELECT count(*) FROM public.procrastinate_jobs")) == jobs - 1
    repeated = await purge_legacy_data(
        factory, objects, expected_database=database, manifest_path=tmp_path / "repeat.json"
    )
    assert all(value == 0 for value in repeated.values())


@pytest.mark.pg
@pytest.mark.asyncio
async def test_cleanup_refuses_new_native_data(legacy_factory, tmp_path):
    from test_admission import send, visitor

    from xiaohe.maintenance import purge_legacy_data

    factory, _, database = legacy_factory
    who, conversation = await visitor(factory)
    await send(factory, who, conversation, "新原生会话")
    with pytest.raises(ValueError, match="new native data"):
        await purge_legacy_data(
            factory, None, expected_database=database, manifest_path=tmp_path / "no.json"
        )
    assert not (tmp_path / "no.json").exists()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_pending_attachment_is_protected_before_object_deletion(legacy_factory, tmp_path):
    from sqlalchemy import text

    from xiaohe.maintenance import purge_legacy_data

    factory, _, database = legacy_factory
    await seed_old_chat(factory)
    async with factory.begin() as db:
        await db.execute(
            text("""INSERT INTO xiaohe.reception_submissions
            (id,draft_id,conversation_id,system_id,tenant_id,draft_revision,summary_version,
             confirmation_message_id,submission_key,payload,status)
            SELECT gen_random_uuid(),d.id,d.conversation_id,d.system_id,d.tenant_id,d.revision,d.revision,
             c.message_id,'pending-attachment-probe','{"files":[{"id":"possibly-shared"}]}','pending'
            FROM xiaohe.reception_drafts d JOIN xiaohe.commands c ON c.conversation_id=d.conversation_id
        """)
        )

    class NoDeletion:
        async def verify_unversioned_cleanup(self):
            pass

        async def delete_expired_object(self, key):
            raise AssertionError("pending attachment must not be deleted")

    with pytest.raises(ValueError, match="formal application"):
        await purge_legacy_data(
            factory, NoDeletion(), expected_database=database, manifest_path=tmp_path / "no.json"
        )
    assert not (tmp_path / "no.json").exists()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_0015_refuses_old_data_then_removes_only_legacy_structure(legacy_factory, tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from xiaohe.maintenance import purge_legacy_data

    factory, url, database = legacy_factory
    await seed_old_chat(factory)
    engine = create_engine(url)
    with engine.connect() as conn:
        config = Config("alembic.ini")
        config.attributes["connection"] = conn
        with pytest.raises(RuntimeError, match="explicit legacy data cleanup"):
            command.upgrade(config, "head")
        conn.rollback()

    class Objects:
        async def verify_unversioned_cleanup(self):
            pass

        async def delete_expired_object(self, key):
            pass

        async def verify_object_absent(self, key):
            pass

    await purge_legacy_data(
        factory, Objects(), expected_database=database, manifest_path=tmp_path / "manifest.json"
    )
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        assert conn.scalar(text("SELECT version_num FROM xiaohe.alembic_version")) == "0015"
        assert conn.scalar(text("SELECT to_regclass('xiaohe.workflow_definitions')")) is None
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='xiaohe' AND ((table_name='runs' AND column_name IN('runtime_version','workflow_bundle_id')) OR (table_name='reception_drafts' AND column_name='definition_bundle_id'))"
                )
            )
            == 0
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_object_cleanup_retries_and_verifies_absence():
    from xiaohe.maintenance import delete_objects

    class Objects:
        def __init__(self):
            self.keys = {"xiaohe/one", "xiaohe/two", "unrelated/keep"}
            self.fail = True

        async def verify_unversioned_cleanup(self):
            pass

        async def delete_expired_object(self, key):
            if key == "xiaohe/two" and self.fail:
                self.fail = False
                raise OSError("transient delete failure")
            self.keys.discard(key)

        async def verify_object_absent(self, key):
            assert key not in self.keys

    objects = Objects()
    with pytest.raises(OSError):
        await delete_objects(objects, ["xiaohe/one", "xiaohe/two"])
    await delete_objects(objects, ["xiaohe/one", "xiaohe/two"])
    assert objects.keys == {"unrelated/keep"}


@pytest.mark.asyncio
async def test_object_cleanup_rejects_unowned_keys_before_deleting():
    from xiaohe.maintenance import delete_objects

    class NoDeletion:
        async def delete_expired_object(self, key):
            raise AssertionError("ownership validation must precede deletion")

    with pytest.raises(ValueError, match="object ownership"):
        await delete_objects(NoDeletion(), ["xiaohe/one", "shared/keep"])


@pytest.mark.asyncio
async def test_versioned_bucket_is_rejected_before_any_delete():
    from xiaohe.maintenance import delete_objects

    class Versioned:
        async def verify_unversioned_cleanup(self):
            raise ValueError("versioned bucket")

        async def delete_expired_object(self, key):
            raise AssertionError("must not delete versioned objects")

    with pytest.raises(ValueError, match="versioned bucket"):
        await delete_objects(Versioned(), ["xiaohe/hidden-by-delete-marker"])
