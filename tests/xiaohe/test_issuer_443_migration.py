from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from test_identity_sessions import bootstrap, completion, login, system

from xiaohe.identity import bind_user
from xiaohe.models import Conversation, Session, System, User

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
OLD = "https://sso.lvh.me:8443"
NEW = "https://sso.lvh.me"
SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/xiaohe/migrations/20261104_local_issuer_443.sql"
)


async def migrate(db):
    driver = (await (await db.connection()).get_raw_connection()).driver_connection
    await driver.execute(SCRIPT.read_text(encoding="utf-8"), prepare=False)


async def state(factory):
    async with factory() as db:
        return (
            await db.scalar(select(System.issuer).where(System.system_id == "portal")),
            (await db.scalars(select(User.issuer))).all(),
            await db.scalar(
                text(
                    "SELECT tgenabled::text FROM pg_trigger WHERE tgrelid='xiaohe.users'::regclass AND tgname='guard_user_identity'"
                )
            ),
        )


async def test_migration_preserves_user_and_session_then_new_issuer_reuses_identity(pg_factory):
    session, token = await login(pg_factory)
    snapshot, _, _ = await bootstrap(pg_factory, token)
    async with pg_factory.begin() as db:
        await migrate(db)
    async with pg_factory.begin() as db:
        user = await bind_user(db, system(), completion(issuer=NEW))
        assert user.user_id == session.user_id and user.issuer == NEW
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert (await db.get(Conversation, UUID(snapshot.conversation_id))).user_id == user.user_id
        stored = await db.get(Session, session.id)
        assert (stored.created_at, stored.expires_at, stored.revoked_at) == (
            session.created_at,
            session.expires_at,
            session.revoked_at,
        )
    assert (await state(pg_factory)) == (NEW, [NEW], "O")
    with pytest.raises(IntegrityError, match="immutable user identity"):
        async with pg_factory.begin() as db:
            await db.execute(update(User).values(issuer=OLD))


async def test_migration_refuses_a_new_issuer_binding_without_merging(pg_factory):
    original, _ = await login(pg_factory)
    async with pg_factory.begin() as db:
        db.add(
            User(
                user_id=str(uuid4()),
                system_id="portal",
                tenant_id=1,
                issuer=NEW,
                sub="member:1:fixture",
                source_tenant_id=1,
            )
        )
    with pytest.raises(CheckViolation, match="issuer cutover precondition"):
        async with pg_factory.begin() as db:
            await migrate(db)
    issuer, users, enabled = await state(pg_factory)
    assert issuer == OLD and sorted(users) == sorted([OLD, NEW]) and enabled == "O"
    async with pg_factory() as db:
        assert (await db.get(User, original.user_id)).issuer == OLD


async def test_migration_transaction_rollback_restores_identity_and_guard(pg_factory):
    await login(pg_factory)
    with pytest.raises(RuntimeError, match="controlled abort"):
        async with pg_factory.begin() as db:
            await migrate(db)
            raise RuntimeError("controlled abort")
    assert (await state(pg_factory)) == (OLD, [OLD], "O")


async def test_migration_refuses_unexpected_user_count(pg_factory):
    await login(pg_factory, sub="first")
    await login(pg_factory, sub="second")
    with pytest.raises(CheckViolation, match="issuer cutover precondition"):
        async with pg_factory.begin() as db:
            await migrate(db)
    assert (await state(pg_factory)) == (OLD, [OLD, OLD], "O")
