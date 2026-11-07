import asyncio
import hashlib
from datetime import timedelta
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from xiaohe import identity
from xiaohe.contracts import ServiceError, TrustedSystem, VerifiedCompletion
from xiaohe.conversations import load_snapshot, require_conversation
from xiaohe.identity import (
    SESSION_COOKIE,
    bootstrap_session,
    create_user_session,
    logout_session,
    resolve_actor,
    resolve_system,
)
from xiaohe.models import Conversation, GuestBudget, Message, Project, Session, System, User
from xiaohe.settings import Settings

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def request(
    token=None,
    *,
    method="POST",
    system="portal",
    origin="https://portal.lvh.me:9444",
    peer="127.0.0.1",
    csrf=None,
    headers=(),
):
    values = [(b"x-xiaohe-system-id", system.encode())]
    if origin is not None:
        values.append((b"origin", origin.encode()))
    if token is not None:
        values.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    if csrf is not None:
        values.append((b"x-xiaohe-csrf", csrf.encode()))
    settings = Settings(
        toolkit_base_url="http://127.0.0.1:18090",
        auth_secret="fixture-auth",
        trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "method": method,
            "path": "/ai-api/v1/session",
            "query_string": b"",
            "headers": values + list(headers),
            "client": (peer, 12345),
            "server": ("portal.lvh.me", 9444),
            "app": SimpleNamespace(state=SimpleNamespace(xiaohe_settings=settings)),
        }
    )


def system(name="portal"):
    return TrustedSystem(
        system_id=name,
        tenant_id=1 if name == "portal" else 2,
        portal_client_id="app_localportal" if name == "portal" else "app_other",
        allowed_origin="https://portal.lvh.me:9444"
        if name == "portal"
        else "https://other.example",
    )


def completion(name="portal", sub="member:1:fixture", **changes):
    value = {
        "issuer": "https://sso.lvh.me:8443",
        "sub": sub,
        "sourceTenantId": 1,
        "authenticatedAt": "2026-10-30T00:00:00Z",
        "portalClientId": system(name).portal_client_id,
        "transactionId": "fixture-transaction",
    }
    return VerifiedCompletion.model_validate({**value, **changes})


async def bootstrap(factory, token=None, **kwargs):
    response = Response()
    async with factory.begin() as db:
        snapshot = await bootstrap_session(request(token, **kwargs), response, db)
    if token is None:
        cookie = SimpleCookie()
        for header in response.headers.getlist("set-cookie"):
            cookie.load(header)
        token = cookie[SESSION_COOKIE].value
    return snapshot, token, response


async def login(factory, sub="member:1:fixture"):
    async with factory.begin() as db:
        record, token = await create_user_session(db, system(), completion(sub=sub))
    return record, token.get_secret_value()


async def actor(factory, token, **kwargs):
    async with factory.begin() as db:
        req = request(token, method="GET", **kwargs)
        trusted = await resolve_system(req, db)
        return await resolve_actor(req, trusted, db)


async def test_private_default_conversation_and_system_isolation(pg_factory):
    first, token, response = await bootstrap(pg_factory)
    second, _, _ = await bootstrap(pg_factory, token)
    assert first == second
    assert (
        "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    )
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Max-Age=31536000" in response.headers["set-cookie"]
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await resolve_actor(
                request(token, method="GET", system="other", origin="https://other.example"),
                system("other"),
                db,
            )
        assert exc.value.code == "FORBIDDEN"
    record, user_token = await login(pg_factory)
    original, _, _ = await bootstrap(pg_factory, user_token)
    reopened, _, _ = await bootstrap(pg_factory, user_token)
    assert original.conversation_id == reopened.conversation_id
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Project)) == 1
        assert record.user_id == original.viewer.user_id
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 2


async def test_global_user_has_one_tenant_and_system(pg_factory):
    one, _ = await login(pg_factory)
    again, _ = await login(pg_factory)
    assert one.user_id == again.user_id
    UUID(one.user_id)
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await create_user_session(db, system("other"), completion("other"))
        assert exc.value.code == "IDENTITY_MISMATCH"
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        user = await db.get(User, one.user_id)
        assert user.system_id == "portal" and user.tenant_id == 1
        db.add(
            User(
                user_id="opaque-existing-ai-user",
                system_id="portal",
                tenant_id=1,
                issuer="https://sso.lvh.me:8443",
                sub="fixture-existing",
                source_tenant_id=1,
            )
        )
    async with pg_factory.begin() as db:
        existing, _ = await create_user_session(db, system(), completion(sub="fixture-existing"))
        assert existing.user_id == "opaque-existing-ai-user"
    async with pg_factory.begin() as db:
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    update(User)
                    .where(User.user_id == "opaque-existing-ai-user")
                    .values(system_id="other", tenant_id=2)
                )


async def test_other_users_thread_rejected_before_toolkit_call(pg_factory):
    _, a_token = await login(pg_factory, "subject-a")
    _, b_token = await login(pg_factory, "subject-b")
    a, _, _ = await bootstrap(pg_factory, a_token)
    b_actor = await actor(pg_factory, b_token)
    internal = AsyncMock()
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            target = await require_conversation(db, b_actor, UUID(a.conversation_id))
            await internal(user_id=b_actor.user_id, thread_id=target.thread_id)
        assert exc.value.code == "FORBIDDEN"
    internal.assert_not_called()


async def test_session_expiry_keeps_budget_for_the_new_guest_episode(pg_factory):
    original, token, _ = await bootstrap(pg_factory)
    async with pg_factory.begin() as db:
        await db.execute(update(Session).values(message_count=99))
        await db.execute(update(GuestBudget).values(message_count=99))
    current, _, _ = await bootstrap(pg_factory, token)
    assert current.viewer.message_count == 99
    assert current.viewer.expires_at == original.viewer.expires_at
    async with pg_factory.begin() as db:
        await db.execute(
            update(Session).values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    reopened, _, _ = await bootstrap(pg_factory, token)
    assert reopened.viewer.message_count == 99
    assert reopened.viewer.guest_id != original.viewer.guest_id
    assert reopened.conversation_id != original.conversation_id
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 2
        assert await db.scalar(select(func.count()).select_from(GuestBudget)) == 1
        assert await db.scalar(select(Session.message_count)) == 99


async def test_user_session_absolute_28800_seconds_never_slides(pg_factory, monkeypatch):
    record, token = await login(pg_factory)
    assert record.expires_at - record.created_at == timedelta(seconds=28800)
    assert record.created_at > completion().authenticated_at
    assert record.authenticated_at == completion().authenticated_at
    stamp = record.created_at + timedelta(seconds=28799)
    clock = AsyncMock(return_value=stamp)
    monkeypatch.setattr(identity, "database_now", clock)
    original, _, _ = await bootstrap(pg_factory, token)
    await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        await resolve_actor(request(token, csrf=original.csrf.token), system(), db)
    again, _, _ = await bootstrap(pg_factory, token)
    assert original == again
    clock.return_value = record.created_at + timedelta(seconds=28800)
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, token)
    assert exc.value.code == "AUTH_REQUIRED" and exc.value.http_status == 401
    async with pg_factory.begin() as db:
        stored = await db.get(Session, record.id)
        assert stored.expires_at == record.expires_at
        assert await db.get(User, record.user_id) is not None
        assert await db.scalar(select(func.count()).select_from(Project)) == 1


@pytest.mark.parametrize("bad", ["", "forged", "x" * 43])
async def test_unknown_cookie_never_bootstraps_guest(pg_factory, bad):
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, bad)
    assert exc.value.code == "AUTH_REQUIRED"
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"peer": "198.51.100.1"},
        {"origin": "https://attacker.example"},
        {"origin": None},
        {"system": "unknown"},
        {"headers": [(b"x-xiaohe-system-id", b"other")]},
        {"headers": [(b"origin", b"https://attacker.example")]},
    ],
)
async def test_untrusted_ingress_rejected_before_creation(pg_factory, kwargs):
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, **kwargs)
    assert exc.value.code == "FORBIDDEN"


async def test_forwarded_ip_cannot_make_untrusted_peer_trusted(pg_factory):
    with pytest.raises(ServiceError):
        await bootstrap(
            pg_factory, peer="198.51.100.1", headers=[(b"x-forwarded-for", b"127.0.0.1")]
        )


async def test_session_initialization_requires_post_and_exact_origin(pg_factory):
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, method="GET", origin=None)
    assert exc.value.code == "FORBIDDEN"


async def test_csrf_and_logout_do_not_reset_guest(pg_factory):
    snapshot, token, _ = await bootstrap(pg_factory)
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await logout_session(request(token), Response(), db)
        assert exc.value.code == "CSRF_INVALID"
    async with pg_factory.begin() as db:
        await logout_session(request(token, csrf=snapshot.csrf.token), Response(), db)
    unchanged, _, _ = await bootstrap(pg_factory, token)
    assert unchanged == snapshot
    row, user_token = await login(pg_factory)
    user_snapshot, _, _ = await bootstrap(pg_factory, user_token)
    async with pg_factory.begin() as db:
        await logout_session(request(user_token, csrf=user_snapshot.csrf.token), Response(), db)
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, user_token)
    assert exc.value.code == "AUTH_REQUIRED"
    async with pg_factory.begin() as db:
        assert (await db.get(Session, row.id)).revoked_at is not None
        assert await db.get(User, row.user_id)


async def test_only_token_digest_persisted_and_guest_namespace_reserved(pg_factory):
    snapshot, token, _ = await bootstrap(pg_factory)
    async with pg_factory.begin() as db:
        row = await db.scalar(select(Session))
        assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
        assert row.csrf_token != token and token not in str(row.__dict__)
        assert row.toolkit_user_id == f"guest:{snapshot.viewer.guest_id}"


async def test_concurrent_login_reuses_global_user_and_default_objects(pg_factory):
    rows = await asyncio.gather(login(pg_factory), login(pg_factory))
    assert rows[0][0].user_id == rows[1][0].user_id
    snapshots = await asyncio.gather(*(bootstrap(pg_factory, token) for _, token in rows))
    assert snapshots[0][0].conversation_id == snapshots[1][0].conversation_id
    async with pg_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(Project)) == 1


async def test_snapshot_reads_private_persistent_messages_and_rechecks_revocation(pg_factory):
    row, token = await login(pg_factory)
    snapshot, _, _ = await bootstrap(pg_factory, token)
    current = await actor(pg_factory, token)
    async with pg_factory.begin() as db:
        db.add(
            Message(
                id=uuid4(),
                conversation_id=UUID(snapshot.conversation_id),
                system_id="portal",
                tenant_id=1,
                role="assistant",
                parts=[{"type": "text", "text": "本人历史"}],
            )
        )
    async with pg_factory.begin() as db:
        loaded = await load_snapshot(db, current, UUID(snapshot.conversation_id))
        assert loaded.messages[0] == snapshot.messages[0]
        assert len(loaded.messages) == len(snapshot.messages) + 1
        assert loaded.messages[-1].parts[0].text == "本人历史"
        await db.execute(
            update(Session).where(Session.id == row.id).values(revoked_at=func.clock_timestamp())
        )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await load_snapshot(db, current, UUID(snapshot.conversation_id))
        assert exc.value.code == "AUTH_REQUIRED"


async def test_system_registration_and_session_deadlines_are_database_constraints(pg_factory):
    record, _ = await login(pg_factory)
    async with pg_factory.begin() as db:
        for change in (
            {
                "created_at": record.created_at + timedelta(seconds=1),
                "expires_at": record.expires_at + timedelta(seconds=1),
            },
            {"expires_at": record.expires_at + timedelta(seconds=1)},
        ):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        update(Session).where(Session.id == record.id).values(**change)
                    )
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    System(
                        system_id="duplicate",
                        tenant_id=1,
                        portal_client_id="duplicate",
                        allowed_origin="https://duplicate.example",
                        issuer="https://issuer.example",
                    )
                )
                await db.flush()
        await db.execute(
            update(Session).where(Session.id == record.id).values(revoked_at=func.clock_timestamp())
        )
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    update(Session).where(Session.id == record.id).values(revoked_at=None)
                )


async def test_cross_owner_and_cross_system_foreign_keys(pg_factory):
    _, token_a = await login(pg_factory, "subject-a")
    _, token_b = await login(pg_factory, "subject-b")
    a, _, _ = await bootstrap(pg_factory, token_a)
    b, _, _ = await bootstrap(pg_factory, token_b)
    async with pg_factory.begin() as db:
        project_b = await db.scalar(select(Project).where(Project.user_id == b.viewer.user_id))
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    Conversation(
                        system_id="portal",
                        tenant_id=1,
                        user_id=a.viewer.user_id,
                        guest_id=None,
                        project_id=project_b.id,
                        purpose="private-other",
                    )
                )
                await db.flush()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    Message(
                        conversation_id=UUID(a.conversation_id),
                        system_id="other",
                        tenant_id=2,
                        role="assistant",
                        parts=[],
                    )
                )
                await db.flush()


@pytest.mark.parametrize("target", ["system", "user"])
async def test_disabled_registration_or_user_denies_existing_cookie(pg_factory, target):
    _, token = await login(pg_factory)
    await bootstrap(pg_factory, token)
    async with pg_factory.begin() as db:
        model = System if target == "system" else User
        await db.execute(update(model).values(enabled=False))
    with pytest.raises(ServiceError) as exc:
        await bootstrap(pg_factory, token)
    assert exc.value.code == ("FORBIDDEN" if target == "system" else "AUTH_REQUIRED")


async def test_duplicate_cookie_and_unconfigured_proxy_fail_closed(pg_factory):
    _, token, _ = await bootstrap(pg_factory)
    with pytest.raises(ServiceError) as exc:
        await bootstrap(
            pg_factory, token, headers=[(b"cookie", f"{SESSION_COOKIE}={token}".encode())]
        )
    assert exc.value.code == "AUTH_REQUIRED"
    req = request()
    req.app.state.xiaohe_settings.trusted_proxy_cidrs = []
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError) as exc:
            await bootstrap_session(req, Response(), db)
    assert exc.value.code == "FORBIDDEN"


async def test_expiry_is_checked_after_session_lock_wait(pg_factory):
    original, token, _ = await bootstrap(pg_factory)
    async with pg_factory.begin() as blocker:
        row = await blocker.scalar(select(Session).with_for_update())
        task = asyncio.create_task(bootstrap(pg_factory, token))
        async with pg_factory() as observer:

            async def wait_for_lock():
                while True:
                    waiting = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname=current_database() AND wait_event_type='Lock' "
                            "AND query LIKE '%sessions%'"
                        )
                    )
                    if waiting:
                        return
                    await observer.rollback()

            await asyncio.wait_for(wait_for_lock(), 10)
        row.expires_at = await blocker.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    reopened, _, _ = await task
    assert reopened.conversation_id != original.conversation_id
    assert reopened.viewer.guest_id != original.viewer.guest_id
    with pytest.raises(ServiceError) as exc:
        await actor(pg_factory, token)
    assert exc.value.code == "VISITOR_EXPIRED"


async def test_migration_roundtrip_preserves_real_checkpoint_and_store(pg_factory, pg_url):
    from alembic import command
    from alembic.config import Config
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from sqlalchemy import create_engine

    dsn = pg_url.replace("postgresql+psycopg:", "postgresql:", 1)
    async with await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        saver = AsyncPostgresSaver(conn)
        store = AsyncPostgresStore(conn)
        await saver.setup()
        await store.setup()
        checkpoint = empty_checkpoint()
        saved = await saver.aput(
            {"configurable": {"thread_id": "p1-protected", "checkpoint_ns": ""}},
            checkpoint,
            {"source": "input", "step": -1, "parents": {}},
            {},
        )
        await store.aput(("p1-protected",), "sentinel", {"keep": True})
        before = await (
            await conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        ).fetchall()
        engine = create_engine(pg_url, hide_parameters=True)
        with engine.begin() as connection:
            cfg = Config("alembic.ini")
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "base")
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='xiaohe' AND tablename <> 'alembic_version'"
                    )
                )
                == 0
            )
            command.upgrade(cfg, "head")
        engine.dispose()
        assert (await saver.aget(saved))["id"] == checkpoint["id"]
        assert (await store.aget(("p1-protected",), "sentinel")).value == {"keep": True}
        after = await (
            await conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        ).fetchall()
        assert before == after
