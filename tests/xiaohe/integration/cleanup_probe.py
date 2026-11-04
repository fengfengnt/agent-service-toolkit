"""仅隔离测试使用：真实对象已删除、PG事务未提交时等待硬杀。"""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from xiaohe.artifacts import ArtifactService
from xiaohe.database import create_database_engine
from xiaohe.expiry import purge_expired_guest
from xiaohe.settings import Settings


async def main():
    url = os.environ["XIAOHE_TEST_DATABASE_URL"]
    data = json.loads(Path(os.environ["XIAOHE_TEST_MINIO_CONFIG"]).read_text("utf-8"))
    if (
        make_url(url).database != "test_xiaohe_agent"
        or data["container"] != "xiaohe-agent-test-minio"
        or not data["bucket"].startswith("test-xiaohe-")
        or not data["endpoint"].startswith("http://127.0.0.1:")
    ):
        raise ValueError("cleanup probe requires owned isolated storage")
    engine = create_database_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ArtifactService(
        factory,
        Settings(
            toolkit_base_url="http://runtime",
            auth_secret="fixture-auth",
            minio_endpoint=data["endpoint"],
            minio_bucket=data["bucket"],
            minio_access_key=data["access_key"],
            minio_secret_key=data["secret_key"],
        ),
    )
    remove = service.delete_expired_object

    async def gated(key):
        await remove(key)
        print("DELETED", flush=True)
        await asyncio.Event().wait()

    service.delete_expired_object = gated
    try:
        await purge_expired_guest(factory, UUID(sys.argv[1]), service)
    finally:
        await service.close()
        await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
