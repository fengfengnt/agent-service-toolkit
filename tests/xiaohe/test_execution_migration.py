from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from test_admission import visitor
from test_standard_cleanup import legacy_factory as legacy_factory

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("legacy_factory", [False], indirect=True)
async def test_execution_migration_backfills_existing_waiting_run(legacy_factory):
    pg_factory, pg_url, _ = legacy_factory
    who, conversation = await visitor(pg_factory)
    engine = create_engine(pg_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                command.downgrade(config, "0002")
                ids = {
                    "message": uuid4(),
                    "run": uuid4(),
                    "command": uuid4(),
                    "conversation": conversation,
                    "session": who.session_id,
                }
                connection.execute(
                    text("""INSERT INTO xiaohe.messages
                    (id,conversation_id,system_id,tenant_id,role,parts)
                    VALUES (:message,:conversation,'portal',1,'user','[]')"""),
                    ids,
                )
                connection.execute(
                    text("""INSERT INTO xiaohe.runs
                    (id,conversation_id,system_id,tenant_id,session_id,initial_message_id,status)
                    VALUES (:run,:conversation,'portal',1,:session,:message,'waiting_input')"""),
                    ids,
                )
                connection.execute(
                    text("""INSERT INTO xiaohe.commands
                    (id,conversation_id,system_id,tenant_id,kind,client_key,payload_digest,payload,message_id,run_id)
                    VALUES (:command,:conversation,'portal',1,'message','legacy','legacy','{}',:message,:run)"""),
                    ids,
                )
                command.upgrade(config, "0014")
                row = connection.execute(
                    text("""SELECT r.guest_id,r.user_id,
                    r.execution_expires_at=s.expires_at AS same_deadline,c.completed_at IS NOT NULL AS completed
                    FROM xiaohe.runs r JOIN xiaohe.sessions s ON s.id=r.session_id
                    JOIN xiaohe.commands c ON c.run_id=r.id WHERE r.id=:run"""),
                    ids,
                ).one()
                assert row.guest_id == who.guest_id and row.user_id is None
                assert row.same_deadline and row.completed
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
