import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from xiaohe.contracts import ServiceError
from xiaohe.server_files import ServerFileClient
from xiaohe.settings import Settings

pytestmark = pytest.mark.asyncio
OWNER = {"systemId": "portal", "issuer": "https://sso.test", "sourceTenantId": 1, "subject": "member:1:7"}
CONTENT = b"test-only"


def client(handler):
    return ServerFileClient(
        Settings(toolkit_base_url="http://runtime", auth_secret="test-auth",
                 reception_base_url="https://server.test", reception_application_id="test-app",
                 reception_secret="test-service-secret-0123456789abcdef"), transport=httpx.MockTransport(handler)
    )


def view(file_id):
    return {"id": file_id, "filename": "file.txt", "mediaType": "text/plain", "bytes": len(CONTENT),
            "sha256": hashlib.sha256(CONTENT).hexdigest(), "purpose": "artifact", "status": "ready",
            "preview": {"format": "text", "text": CONTENT.decode()}}


async def test_all_file_operations_use_authenticated_server_without_storage_credentials():
    file_id = str(uuid4())
    requests = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-service-secret-0123456789abcdef"
        assert request.headers["x-reception-application"] == "test-app"
        requests.append(request)
        if request.url.path.endswith("/content"):
            assert request.content == CONTENT
            return httpx.Response(200, json={"code": 0, "data": view(file_id)})
        body = json.loads(request.content)
        assert body["owner"] == OWNER
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=CONTENT)
        data = True if body["operation"] in ("remove", "transfer", "health") else view(file_id)
        return httpx.Response(200, json={"code": 0, "data": data})

    files = client(handler)
    result = await files.write(OWNER, file_id, "file.txt", "text/plain", CONTENT, "artifact")
    assert result["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert await files.read(OWNER, file_id) == CONTENT
    await files.remove(OWNER, file_id)
    assert len(requests) == 5


@pytest.mark.parametrize("status", [301, 403, 409, 422, 503])
async def test_remote_failures_are_bounded_and_never_follow_redirects(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.test"}, content=b"private error detail")

    with pytest.raises(ServiceError) as caught:
        await client(handler).view(OWNER, str(uuid4()))
    assert len(calls) == 1
    assert "private error" not in str(caught.value)


async def test_digest_mismatch_and_foreign_metadata_are_not_accepted():
    file_id = str(uuid4())

    def handler(request):
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=b"corrupted")
        return httpx.Response(200, json={"code": 0, "data": view(file_id)})

    with pytest.raises(ServiceError):
        await client(handler).read(OWNER, file_id)
    with pytest.raises(ServiceError):
        await client(handler).view(OWNER, str(uuid4()))


async def test_missing_server_configuration_does_not_fall_back_to_a_bucket():
    files = ServerFileClient(Settings(toolkit_base_url="http://runtime", auth_secret="test-auth"))
    with pytest.raises(ServiceError):
        await files.view(OWNER, str(uuid4()))


async def test_deployment_uses_server_files_without_object_storage_configuration():
    from types import SimpleNamespace

    from pydantic import SecretStr

    from xiaohe.deployment import validate_runtime

    settings = Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth",
        database_url="postgresql+psycopg://test:test@postgres/test_xiaohe_files",
        trusted_proxy_cidrs=["127.0.0.1/32"], reception_base_url="https://server.test",
        reception_application_id="test-app", reception_secret="fixture-server-file-credential-0123456789")
    upstream = SimpleNamespace(USE_FAKE_MODEL=False, LANGCHAIN_TRACING_V2=False,
        DEFAULT_MODEL="configured-model", AUTH_SECRET=SecretStr("fixture-auth"), DATABASE_TYPE="postgres")
    validate_runtime(settings, upstream)
