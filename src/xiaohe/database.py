from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


def create_database_engine(database_url: str | SecretStr) -> AsyncEngine:
    value = database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("business database requires postgresql+psycopg")
    return create_async_engine(
        url, hide_parameters=True, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"}
    )


async def database_now(db: AsyncSession):
    return await db.scalar(select(func.clock_timestamp()))
