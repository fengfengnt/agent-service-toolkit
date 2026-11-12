from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from xiaohe.models import GuestBudget, GuestBudgetCredential, GuestBudgetReceipt


@pytest.mark.pg
def test_guest_budget_migration_adds_structure_without_guessing_legacy_identity(pg_url):
    engine = create_engine(pg_url, hide_parameters=True)
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        inspector = inspect(connection)
        for model in (GuestBudget, GuestBudgetCredential, GuestBudgetReceipt):
            assert inspector.has_table(model.__tablename__, schema="xiaohe")
        for table, column in (
            ("sessions", "budget_id"),
            ("conversations", "history_epoch"),
            ("messages", "membership_sequence"),
            ("auth_operations", "expected_identity"),
        ):
            assert column in {c["name"] for c in inspector.get_columns(table, schema="xiaohe")}
        assert connection.scalar(text("SELECT version_num FROM xiaohe.alembic_version")) == "0015"
    engine.dispose()


@pytest.mark.pg
def test_0009_to_0010_preserves_member_history_files_and_absolute_deadline(pg_url):
    url = make_url(pg_url)
    assert url.database.startswith("test_xiaohe_")
    database = "test_xiaohe_migration_" + uuid4().hex
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    engine = create_engine(url.set(database=database), hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        with engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "0009")
            old = MetaData(schema="xiaohe")
            old.reflect(connection)
            ids = {
                name: uuid4()
                for name in (
                    "session",
                    "project",
                    "conversation",
                    "message",
                    "run",
                    "artifact",
                    "version",
                )
            }
            stamp = datetime.now(UTC)
            scope = dict(system_id="migration-fixture", tenant_id=1)
            rows = {
                "systems": dict(
                    **scope,
                    portal_client_id="fixture",
                    allowed_origin="https://fixture.example",
                    issuer="https://sso.example",
                ),
                "users": dict(
                    **scope,
                    user_id="fixture-member",
                    issuer="https://sso.example",
                    sub="fixture-sub",
                    source_tenant_id=1,
                ),
                "sessions": dict(
                    **scope,
                    id=ids["session"],
                    user_id="fixture-member",
                    token_digest="a" * 64,
                    csrf_token="b" * 64,
                    toolkit_user_id="fixture-member",
                    authenticated_at=stamp,
                    created_at=stamp,
                    expires_at=stamp + timedelta(hours=8),
                ),
                "projects": dict(
                    **scope, id=ids["project"], user_id="fixture-member", is_default=True
                ),
                "conversations": dict(
                    **scope,
                    id=ids["conversation"],
                    project_id=ids["project"],
                    user_id="fixture-member",
                    thread_id="fixture-thread",
                ),
                "messages": dict(
                    **scope,
                    id=ids["message"],
                    conversation_id=ids["conversation"],
                    role="assistant",
                    parts=[{"type": "text", "text": "保留历史"}],
                ),
                "runs": dict(
                    **scope,
                    id=ids["run"],
                    conversation_id=ids["conversation"],
                    session_id=ids["session"],
                    user_id="fixture-member",
                    execution_expires_at=stamp + timedelta(hours=8),
                    initial_message_id=ids["message"],
                    status="completed",
                    holds_slot=False,
                ),
                "artifacts": dict(
                    **scope,
                    id=ids["artifact"],
                    conversation_id=ids["conversation"],
                    creation_key="fixture-artifact",
                    latest_version=1,
                ),
                "artifact_versions": dict(
                    **scope,
                    id=ids["version"],
                    artifact_id=ids["artifact"],
                    version=1,
                    run_id=ids["run"],
                    producer_attempt=0,
                    producer_epoch=0,
                    title="保留文件",
                    media_type="text/plain",
                    content_digest="c" * 64,
                    object_key="fixture/version-1",
                    status="ready",
                    uploaded_at=stamp,
                    verified_digest="c" * 64,
                    bytes_size=5,
                    cleanup_pending=False,
                ),
            }
            for name, row in rows.items():
                connection.execute(old.tables["xiaohe." + name].insert().values(**row))
            before = {
                name: connection.execute(select(old.tables["xiaohe." + name])).all()
                for name in rows
            }
            command.upgrade(config, "0010")
            for name in rows:
                assert (
                    connection.execute(select(old.tables["xiaohe." + name])).all() == before[name]
                )
            assert connection.scalar(text("SELECT count(*) FROM xiaohe.guest_budgets")) == 0
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM xiaohe.sessions WHERE budget_id IS NOT NULL")
                )
                == 0
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()
