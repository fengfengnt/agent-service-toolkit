import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from xiaohe.artifacts import ArtifactService
from xiaohe.settings import Settings


@pytest_asyncio.fixture
async def minio_env(pg_factory, job_app):
    path = os.environ.get("XIAOHE_TEST_MINIO_CONFIG")
    if not path or not Path(path).is_file():
        pytest.fail("真实MinIO测试必须显式提供XIAOHE_TEST_MINIO_CONFIG指向本次隔离配置")
    data = json.loads(Path(path).read_text("utf-8"))
    endpoint = urlsplit(data["endpoint"])
    if (
        endpoint.hostname != "127.0.0.1"
        or not data["bucket"].startswith("test-xiaohe-")
        or data["container"] != "xiaohe-agent-test-minio"
    ):
        pytest.fail("MinIO测试配置不满足隔离边界")
    settings = Settings(
        toolkit_base_url="http://runtime",
        auth_secret="fixture-auth",
        minio_endpoint=data["endpoint"],
        minio_bucket=data["bucket"],
        minio_access_key=data["access_key"],
        minio_secret_key=data["secret_key"],
    )
    service = ArtifactService(pg_factory, settings)
    yield SimpleNamespace(
        service=service,
        settings=settings,
        bucket=data["bucket"],
        endpoint=data["endpoint"],
        container=data["container"],
    )
    await service.close()
