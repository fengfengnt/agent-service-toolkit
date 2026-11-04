from pathlib import Path

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy import select, update
from test_identity_sessions import actor, bootstrap, completion, system

from xiaohe.contracts import ServiceError
from xiaohe.identity import create_user_session
from xiaohe.models import Session, System, User

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]
OLD, NEW = "https://portal.lvh.me:9444", "https://portal.lvh.me"
ISSUER = "https://sso.lvh.me"
SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/xiaohe/migrations/20261104_local_portal_443.sql"
)


async def migrate(db):
    driver = (await (await db.connection()).get_raw_connection()).driver_connection
    await driver.execute(SCRIPT.read_text(encoding="utf-8"), prepare=False)


async def origin(factory):
    async with factory() as db:
        return await db.scalar(select(System.allowed_origin).where(System.system_id == "portal"))


async def test_origin_cutover_preserves_identity_history_and_absolute_session(pg_factory):
    async with pg_factory.begin() as db:
        await db.execute(update(System).where(System.system_id == "portal").values(issuer=ISSUER))
        original, token = await create_user_session(db, system(), completion(issuer=ISSUER))
    snapshot, _, _ = await bootstrap(pg_factory, token.get_secret_value())
    async with pg_factory.begin() as db:
        await migrate(db)
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await actor(pg_factory, token.get_secret_value(), origin=OLD)
    restored, _, response = await bootstrap(pg_factory, token.get_secret_value(), origin=NEW)
    assert restored == snapshot and "set-cookie" not in response.headers
    async with pg_factory() as db:
        user = await db.get(User, original.user_id)
        session = await db.get(Session, original.id)
        assert user.issuer == ISSUER
        assert (session.created_at, session.expires_at, session.user_id) == (
            original.created_at,
            original.expires_at,
            original.user_id,
        )
        assert (await db.get(System, "other")).allowed_origin == "https://other.example"


async def test_origin_cutover_rolls_back_in_caller_transaction(pg_factory):
    async with pg_factory.begin() as db:
        await db.execute(update(System).where(System.system_id == "portal").values(issuer=ISSUER))
    with pytest.raises(RuntimeError, match="controlled abort"):
        async with pg_factory.begin() as db:
            await migrate(db)
            raise RuntimeError("controlled abort")
    assert await origin(pg_factory) == OLD


@pytest.mark.parametrize("changes", [{}, {"issuer": ISSUER, "allowed_origin": NEW}])
async def test_origin_cutover_refuses_unexpected_registration(pg_factory, changes):
    async with pg_factory.begin() as db:
        if changes:
            await db.execute(update(System).where(System.system_id == "portal").values(**changes))
    with pytest.raises(CheckViolation, match="portal cutover precondition"):
        async with pg_factory.begin() as db:
            await migrate(db)
    assert await origin(pg_factory) == changes.get("allowed_origin", OLD)
