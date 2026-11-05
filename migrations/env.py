import os

from alembic import context
from sqlalchemy import create_engine, text

from xiaohe.models import Base
from xiaohe.reception import models as reception_models  # noqa: F401

config = context.config


def migrate(connection):
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS xiaohe"))
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema="xiaohe",
        include_schemas=True,
        include_name=lambda name, type_, parent: name == "xiaohe" if type_ == "schema" else True,
    )
    with context.begin_transaction():
        context.run_migrations()


if config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    url = os.environ.get("XIAOHE_DATABASE_URL")
    if not url:
        raise ValueError("XIAOHE_DATABASE_URL is required")
    engine = create_engine(url, hide_parameters=True)
    with engine.begin() as connection:
        migrate(connection)
    engine.dispose()
