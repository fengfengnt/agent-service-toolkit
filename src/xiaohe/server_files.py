"""通过 Server 管理私有文件；对话进程不持有对象存储权限。"""

import base64
import hashlib
import json
from uuid import UUID

import httpx

from xiaohe.contracts import ServiceError

MAX_BYTES = 32 * 1024 * 1024
BASE = "/internal-api/reception/v1/files"


class ServerFileClient:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def _request(self, path, *, body=None, content=None, metadata=None, limit=1048576):
        settings = self.settings
        if not all((settings.reception_base_url, settings.reception_application_id,
                    settings.reception_secret)):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        headers = {"X-Reception-Application": settings.reception_application_id,
                   "Authorization": "Bearer " + settings.reception_secret.get_secret_value()}
        if metadata is not None:
            headers["X-File-Metadata"] = base64.b64encode(
                json.dumps(metadata, separators=(",", ":")).encode()
            ).decode()
            headers["Content-Type"] = "application/octet-stream"
        try:
            async with httpx.AsyncClient(base_url=settings.reception_base_url,
                                        transport=self.transport, trust_env=False,
                                        follow_redirects=False, timeout=45) as client:
                async with client.stream("POST", BASE + path, headers=headers,
                                         json=body, content=content) as response:
                    if response.status_code not in (200, 201):
                        code, status = {403: ("FORBIDDEN", 403), 409: ("DRAFT_CONFLICT", 409),
                                        422: ("FILE_INVALID", 422)}.get(
                                            response.status_code, ("UPSTREAM_UNAVAILABLE", 503))
                        raise ServiceError(code, status, status == 503)
                    result = bytearray()
                    async for block in response.aiter_bytes():
                        if len(result) + len(block) > limit:
                            raise ValueError("oversized file response")
                        result.extend(block)
                    return bytes(result)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None

    async def _command(self, operation, owner, file_id=None, **extra):
        raw = await self._request("/metadata", body={"operation": operation, "owner": owner,
                                  "id": file_id, **extra})
        return self._data(raw)

    @staticmethod
    def _data(raw):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("code") != 0 or "data" not in value:
                raise ValueError("invalid response")
            return value["data"]
        except (ValueError, TypeError):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None

    @staticmethod
    def _view(value, file_id):
        if (not isinstance(value, dict) or value.get("id") != file_id
                or type(value.get("bytes")) is not int or not 0 <= value["bytes"] <= MAX_BYTES
                or value.get("status") not in ("pending", "ready")
                or not all(isinstance(value.get(k), str) for k in ("filename", "mediaType", "purpose"))
                or not isinstance(value.get("preview"), dict)
                or value["status"] == "ready" and (
                    not isinstance(value.get("sha256"), str) or len(value["sha256"]) != 64)):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return value

    async def view(self, owner, file_id):
        return self._view(await self._command("view", owner, file_id), file_id)

    async def initialize(self, owner, file_id, filename, media_type, size, purpose):
        if size > MAX_BYTES:
            raise ServiceError("FILE_TOO_LARGE", 413)
        value = self._view(await self._command("initialize", owner, file_id,
                           metadata={"filename": filename, "mediaType": media_type,
                                     "bytes": size, "purpose": purpose}), file_id)
        if any(value[key] != expected for key, expected in (
                ("filename", filename), ("mediaType", media_type), ("bytes", size),
                ("purpose", purpose))):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return value

    async def write(self, owner, file_id, filename, media_type, content, purpose):
        await self.initialize(owner, file_id, filename, media_type, len(content), purpose)
        digest = hashlib.sha256(content).hexdigest()
        raw = await self._request("/content", content=content,
                                  metadata={"owner": owner, "id": file_id, "sha256": digest})
        value = self._view(self._data(raw), file_id)
        if value["status"] != "ready" or value["sha256"] != digest or value["bytes"] != len(content):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return value

    async def read(self, owner, file_id):
        value = await self.view(owner, file_id)
        if value["status"] != "ready":
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        content = await self._request("/download", body={"owner": owner, "id": file_id},
                                      limit=value["bytes"])
        if len(content) != value["bytes"] or hashlib.sha256(content).hexdigest() != value["sha256"]:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return content

    async def remove(self, owner, file_id):
        if await self._command("remove", owner, file_id) is not True:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)

    async def removed(self, owner, file_id):
        value = await self._command("removed", owner, file_id)
        if type(value) is not bool:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return value

    async def transfer(self, previous, next_owner, file_id):
        if await self._command("transfer", previous, file_id, nextOwner=next_owner) is not True:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)

    async def health(self):
        if await self._command("health", None) is not True:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)


def user_owner(user):
    return {"systemId": user.system_id, "issuer": user.issuer,
            "sourceTenantId": user.source_tenant_id, "subject": user.sub}


def guest_owner(system, guest_id):
    return {"systemId": system.system_id, "issuer": system.issuer,
            "sourceTenantId": system.tenant_id, "subject": "guest:" + str(UUID(str(guest_id)))}


async def transfer_conversation_files(db, claim, source):
    from sqlalchemy import select, text

    from xiaohe.models import Artifact, ArtifactVersion, System, User
    from xiaohe.reception.models import ReceptionFile

    versions = (await db.scalars(select(ArtifactVersion).join(Artifact).where(
        Artifact.conversation_id == source.id).order_by(ArtifactVersion.id)
        .with_for_update(of=ArtifactVersion))).all()
    materials = (await db.scalars(select(ReceptionFile).where(
        ReceptionFile.conversation_id == source.id).order_by(ReceptionFile.id)
        .with_for_update())).all()
    if not versions and not materials:
        return True
    client = db.info.get("server_files")
    if client is None:
        return False
    previous = guest_owner(await db.get(System, claim.system_id), claim.guest_id)
    next_owner = user_owner(await db.get(User, claim.user_id))
    try:
        for version in versions:
            if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                                   {"key": "xiaohe.upload:" + str(version.id)}):
                return False
            if version.status == "ready":
                await client.transfer(previous, next_owner, str(version.id))
            else:
                await client.remove(previous, str(version.id))
                version.status, version.cleanup_pending = "failed", False
        for material in materials:
            try:
                await client.transfer(previous, next_owner, str(material.id))
            except ServiceError as failure:
                if failure.code != "FORBIDDEN":
                    raise
                await client.initialize(previous, str(material.id), material.filename,
                                        material.media_type, material.bytes, material.category)
                await client.transfer(previous, next_owner, str(material.id))
    except ServiceError:
        return False
    return True
