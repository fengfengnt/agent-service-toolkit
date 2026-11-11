from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select

from xiaohe.deep_runtime import bundles
from xiaohe.workflows.models import WorkflowValidationError

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"
pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_runtime_record_is_immutable(pg_factory):
    assert hasattr(bundles, "persist_runtime_bundle"), "native snapshot persistence missing"
    from xiaohe.models import DeepRuntimeBundleRecord

    bundle = bundles.load_runtime_bundle(ROOT)
    async with pg_factory.begin() as db:
        assert await bundles.persist_runtime_bundle(db, bundle) == bundle.bundle_id
        assert await bundles.persist_runtime_bundle(db, bundle) == bundle.bundle_id
    async with pg_factory.begin() as db:
        assert await bundles.load_runtime_bundle_record(db, bundle.bundle_id) == bundle
        row = await db.scalar(select(DeepRuntimeBundleRecord))
        row.payload = row.payload | {"resources": {}}
    with pytest.raises(WorkflowValidationError):
        async with pg_factory() as db:
            await bundles.load_runtime_bundle_record(db, bundle.bundle_id)


async def test_missing_runtime_record_does_not_use_latest(pg_factory):
    assert hasattr(bundles, "load_runtime_bundle_record"), "native snapshot loading missing"
    with pytest.raises(WorkflowValidationError):
        async with pg_factory() as db:
            await bundles.load_runtime_bundle_record(db, "0" * 64)


async def test_native_records_prevent_incompatible_downgrade(pg_factory, pg_url):
    assert hasattr(bundles, "persist_runtime_bundle"), "native snapshot persistence missing"
    async with pg_factory.begin() as db:
        await bundles.persist_runtime_bundle(db, bundles.load_runtime_bundle(ROOT))
    engine = create_engine(pg_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                with pytest.raises(RuntimeError, match="native"):
                    command.downgrade(config, "0013")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
