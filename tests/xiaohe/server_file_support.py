import base64
import hashlib
import json

import httpx
import pytest_asyncio

from xiaohe.server_files import ServerFileClient
from xiaohe.settings import Settings


class FileServer:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def handle(self, request):
        self.calls.append(request.url.path)
        body = json.loads(base64.b64decode(request.headers["X-File-Metadata"])) if request.url.path.endswith("/content") else json.loads(request.content)
        owner, file_id = body.get("owner"), body.get("id")
        operation = body.get("operation")
        row = self.rows.get(file_id)
        if operation == "health":
            return httpx.Response(200, json={"code": 0, "data": True})
        if row and row["owner"] != owner and not (operation == "transfer" and row["owner"] == body.get("nextOwner") and row.get("previous") == owner):
            return httpx.Response(403)
        if operation == "initialize":
            if row and row["state"] == "removed":
                return httpx.Response(409)
            if row is None:
                row = self.rows[file_id] = {"id": file_id, "owner": owner, "state": "pending", **body["metadata"]}
        elif operation == "remove":
            if row is None:
                row = self.rows[file_id] = {"id": file_id, "owner": owner}
            if row.get("retained"):
                return httpx.Response(409)
            row["state"] = "removed"
            row.pop("content", None)
            return httpx.Response(200, json={"code": 0, "data": True})
        elif operation == "removed":
            return httpx.Response(200, json={"code": 0, "data": bool(row and row["state"] == "removed")})
        elif operation == "transfer":
            if row is None:
                return httpx.Response(403)
            row["previous"], row["owner"] = owner, body["nextOwner"]
            return httpx.Response(200, json={"code": 0, "data": True})
        if row is None or row["state"] == "removed":
            return httpx.Response(403)
        if request.url.path.endswith("/content"):
            digest = hashlib.sha256(request.content).hexdigest()
            if row["state"] == "ready" and digest != row["sha256"]:
                return httpx.Response(409)
            row.update(content=request.content, sha256=digest, state="ready")
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=row["content"])
        preview = {"format": "download_only"}
        if row.get("content") is not None and len(row["content"])<=131072 and row["mediaType"] in ("text/plain", "text/markdown"):
            preview = {"format": "text", "text": row["content"].decode()}
        data = {k: row.get(k) for k in ["id", "filename", "mediaType", "bytes", "sha256", "purpose"]}
        data.update(status=row["state"], preview=preview)
        return httpx.Response(200, json={"code": 0, "data": data})

    def client(self):
        return ServerFileClient(Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth",
            reception_base_url="https://server.test", reception_application_id="test-app",
            reception_secret="fixture-server-file-credential-0123456789"), transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def files_env(pg_factory, job_app):
    from types import SimpleNamespace

    from xiaohe.artifacts import ArtifactService

    server = FileServer()
    service = ArtifactService(pg_factory, Settings(toolkit_base_url="http://runtime", auth_secret="fixture-auth"))
    service.files = server.client()
    yield SimpleNamespace(service=service, server=server)
    await service.close()
