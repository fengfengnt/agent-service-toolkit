import asyncio
import hashlib
import io
import json
import mimetypes
import re
from uuid import UUID, uuid4

import anyio
from sqlalchemy import func, select, text

from xiaohe.contracts import ArtifactView, ServiceError, WriteFence
from xiaohe.conversations import require_conversation
from xiaohe.events import record_event
from xiaohe.models import Artifact, ArtifactVersion, Conversation, System, User
from xiaohe.runs import require_write
from xiaohe.server_files import ServerFileClient, guest_owner, user_owner

_MAX_BYTES = 32 * 1024 * 1024
_PREVIEW_BYTES = 128 * 1024


class _DownloadStream:
    def __init__(self, chunks, body):
        self.chunks, self.body = chunks, body
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        try:
            return await anext(self.chunks)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        with anyio.CancelScope(shield=True):
            try:
                await self.chunks.aclose()
            finally:
                await asyncio.to_thread(self.body.close)


def _view(row):
    ready = row.status == "ready"
    return ArtifactView(
        artifact_id=str(row.artifact_id),
        version=row.version,
        title=row.title,
        media_type=row.media_type,
        status=row.status,
        preview=row.preview if ready else {"format": "download_only"},
        download_path=f"/ai-api/v1/artifacts/{row.artifact_id}/versions/{row.version}/download"
        if ready
        else None,
    )


async def referenced_parts(db, fence, version_ids, parts):
    run = await require_write(db, fence)
    verified = {}
    for version_id in dict.fromkeys(version_ids):
        row = await db.scalar(
            select(ArtifactVersion)
            .join(Artifact)
            .where(
                ArtifactVersion.id == version_id,
                Artifact.conversation_id == run.conversation_id,
                Artifact.system_id == run.system_id,
                Artifact.tenant_id == run.tenant_id,
                ArtifactVersion.status == "ready",
            )
        )
        if row is None:
            raise ServiceError("FORBIDDEN", 403)
        verified[(str(row.artifact_id), row.version)] = {
            "type": "artifact_ref",
            "artifactId": str(row.artifact_id),
            "version": row.version,
            "title": row.title,
            "mediaType": row.media_type,
            "status": "ready",
        }
    for part in parts:
        if part["type"] == "artifact_ref" and (part["artifactId"], part["version"]) not in verified:
            raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
    return [part for part in parts if part["type"] != "artifact_ref"] + list(verified.values())


class ArtifactService:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.files = ServerFileClient(settings)

    @property
    def files(self):
        return self._files

    @files.setter
    def files(self, value):
        self._files = value
        if hasattr(self.factory, "kw"):
            self.factory.kw.setdefault("info", {})["server_files"] = value

    async def owner(self, db, conversation):
        if conversation.user_id is not None:
            user = await db.get(User, conversation.user_id)
            if user is None or user.system_id != conversation.system_id or not user.enabled:
                raise ServiceError("FORBIDDEN", 403)
            return user_owner(user)
        system = await db.get(System, conversation.system_id)
        if system is None or conversation.guest_id is None:
            raise ServiceError("FORBIDDEN", 403)
        return guest_owner(system, conversation.guest_id)

    async def stage_artifact(
        self, db, fence, title, media_type, content_digest, *, artifact_id=None
    ):
        async with db.begin_nested():
            run = await require_write(db, fence)
            if (
                not title.strip()
                or len(title) > 256
                or len(media_type) > 128
                or re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", media_type) is None
                or re.fullmatch(r"[0-9a-f]{64}", content_digest) is None
            ):
                raise ServiceError("INPUT_INVALID", 422)
            key = hashlib.sha256(
                json.dumps([str(run.id), title, media_type], ensure_ascii=False).encode()
            ).hexdigest()
            query = (
                select(Artifact).where(Artifact.id == artifact_id)
                if artifact_id is not None
                else select(Artifact).where(Artifact.creation_key == key)
            )
            artifact = await db.scalar(query.with_for_update())
            if artifact is None:
                if artifact_id is not None:
                    raise ServiceError("FORBIDDEN", 403)
                artifact = Artifact(
                    conversation_id=run.conversation_id,
                    system_id=run.system_id,
                    tenant_id=run.tenant_id,
                    creation_key=key,
                )
                db.add(artifact)
                await db.flush()
            if (artifact.conversation_id, artifact.system_id, artifact.tenant_id) != (
                run.conversation_id,
                run.system_id,
                run.tenant_id,
            ):
                raise ServiceError("FORBIDDEN", 403)
            existing = await db.scalar(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.artifact_id == artifact.id,
                    ArtifactVersion.run_id == run.id,
                    ArtifactVersion.content_digest == content_digest,
                )
                .with_for_update()
            )
            if existing is not None:
                if (existing.title, existing.media_type) != (title, media_type):
                    raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
                if existing.status != "ready":
                    existing.producer_attempt, existing.producer_epoch = (
                        fence.attempt,
                        fence.ownership_epoch,
                    )
                return existing
            artifact.latest_version += 1
            version_id = uuid4()
            row = ArtifactVersion(
                id=version_id, object_key=str(version_id),
                artifact_id=artifact.id,
                system_id=run.system_id,
                tenant_id=run.tenant_id,
                version=artifact.latest_version,
                run_id=run.id,
                producer_attempt=fence.attempt,
                producer_epoch=fence.ownership_epoch,
                title=title,
                media_type=media_type,
                content_digest=content_digest,
            )
            db.add(row)
            await db.flush()
            return row

    async def _owned_version(self, db, fence, version_id):
        run = await require_write(db, fence)
        locator = await db.get(ArtifactVersion, version_id)
        if locator is None:
            raise ServiceError("FORBIDDEN", 403)
        artifact = await db.scalar(
            select(Artifact)
            .where(
                Artifact.id == locator.artifact_id,
                Artifact.conversation_id == run.conversation_id,
                Artifact.system_id == run.system_id,
                Artifact.tenant_id == run.tenant_id,
            )
            .with_for_update()
        )
        if artifact is None:
            raise ServiceError("FORBIDDEN", 403)
        row = await db.scalar(
            select(ArtifactVersion)
            .where(ArtifactVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row.run_id != run.id or (
            row.status != "ready"
            and (row.producer_attempt, row.producer_epoch) != (fence.attempt, fence.ownership_epoch)
        ):
            raise ServiceError("WRITE_FENCE_STALE", 409)
        return run, row

    async def _upload(self, row, content, owner):
        filename = re.sub(r"[\\/\x00-\x1f\x7f]", "_", row.title)[:200]
        extension = mimetypes.guess_extension(row.media_type) or ".bin"
        if not filename.lower().endswith(extension):
            filename += extension
        value = await self.files.write(owner, str(row.id), filename, row.media_type, content, "artifact")
        return value["bytes"], {"format": "download_only"}

    async def write_artifact_object(self, version_id: UUID, content: bytes):
        engine = self.factory.kw["bind"]
        async with engine.connect() as guard, guard.begin():
            await guard.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "xiaohe.upload:" + str(version_id)},
            )
            async with self.factory.begin() as db:
                locator = await db.get(ArtifactVersion, version_id)
                if locator is None:
                    raise ServiceError("FORBIDDEN", 403)
                if (
                    len(content) > _MAX_BYTES
                    or hashlib.sha256(content).hexdigest() != locator.content_digest
                ):
                    raise ServiceError("INPUT_INVALID", 422)
                fence = WriteFence(
                    run_id=locator.run_id,
                    attempt=locator.producer_attempt,
                    ownership_epoch=locator.producer_epoch,
                )
                run, row = await self._owned_version(db, fence, version_id)
                owner = await self.owner(db, await db.get(Conversation, run.conversation_id))
                if row.status == "ready":
                    return
            upload = asyncio.create_task(self._upload(row, content, owner))
            try:
                size, preview = await asyncio.shield(upload)
            except asyncio.CancelledError:
                # A cancelled coroutine cannot stop the synchronous upload thread.
                with anyio.CancelScope(shield=True):
                    while not upload.done():
                        try:
                            await asyncio.shield(upload)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not upload.cancelled():
                        upload.exception()
                raise
            async with self.factory.begin() as db:
                _, current = await self._owned_version(db, fence, version_id)
                current.uploaded_at = await db.scalar(select(func.clock_timestamp()))
                current.verified_digest = hashlib.sha256(content).hexdigest()
                current.bytes_size, current.preview = size, preview

    async def finish_artifact(self, db, fence, version_id: UUID):
        async with db.begin_nested():
            run, row = await self._owned_version(db, fence, version_id)
            if row.status == "ready":
                return _view(row)
            if (
                row.uploaded_at is None
                or row.verified_digest != row.content_digest
                or row.bytes_size is None
            ):
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
            row.status, row.cleanup_pending = "ready", False
            conversation = await db.get(Conversation, run.conversation_id)
            record_event(
                db,
                conversation,
                "artifact.updated",
                {"artifactId": str(row.artifact_id), "version": row.version, "status": "ready"},
                run.id,
            )
            await db.flush()
            await require_write(db, fence)
            return _view(row)

    async def _read(self, db, actor, artifact_id, version):
        locator = await db.get(Artifact, artifact_id)
        if locator is None:
            raise ServiceError("FORBIDDEN", 403)
        conversation = await require_conversation(db, actor, locator.conversation_id)
        row = await db.scalar(
            select(ArtifactVersion)
            .join(Artifact)
            .where(
                Artifact.id == artifact_id,
                Artifact.conversation_id == conversation.id,
                Artifact.system_id == actor.system.system_id,
                Artifact.tenant_id == actor.system.tenant_id,
                ArtifactVersion.version == version,
            )
        )
        if row is None:
            raise ServiceError("FORBIDDEN", 403)
        return row

    async def read_artifact(self, actor, artifact_id: UUID, version: int):
        async with self.factory.begin() as db:
            row = await self._read(db, actor, artifact_id, version)
            result = _view(row)
            if row.status != "ready":
                return result
            artifact = await db.get(Artifact, row.artifact_id)
            owner = await self.owner(db, await db.get(Conversation, artifact.conversation_id))
        metadata = await self.files.view(owner, str(row.id))
        if metadata["sha256"] != row.content_digest or metadata["bytes"] != row.bytes_size:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return result.model_copy(update={"preview": metadata["preview"]})

    async def download_artifact(self, actor, artifact_id: UUID, version: int):
        async with self.factory.begin() as db:
            row = await self._read(db, actor, artifact_id, version)
            if row.status != "ready":
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
            artifact = await db.get(Artifact, row.artifact_id)
            owner = await self.owner(db, await db.get(Conversation, artifact.conversation_id))
        content = await self.files.read(owner, str(row.id))
        if len(content) != row.bytes_size or hashlib.sha256(content).hexdigest() != row.content_digest:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        body = io.BytesIO(content)

        async def chunks():
            while block := body.read(65536):
                async with self.factory.begin() as db:
                    await self._read(db, actor, artifact_id, version)
                yield block

        return _DownloadStream(chunks(), body)

    async def close(self):
        pass

    async def _descriptor(self, reference):
        from xiaohe.reception.models import ReceptionFile, ReceptionFormFile
        try:
            file_id = UUID(str(reference))
        except ValueError:
            raise ServiceError("FORBIDDEN", 403) from None
        async with self.factory.begin() as db:
            row = await db.get(ReceptionFormFile, file_id)
            if row is not None:
                user = await db.get(User, row.user_id)
                if user is None or not user.enabled or user.system_id != row.system_id:
                    raise ServiceError("FORBIDDEN", 403)
                return row, user_owner(user)
            row = await db.get(ReceptionFile, file_id)
            if row is not None:
                return row, await self.owner(db, await db.get(Conversation, row.conversation_id))
            version = await db.get(ArtifactVersion, file_id)
            if version is not None:
                artifact = await db.get(Artifact, version.artifact_id)
                return version, await self.owner(db, await db.get(Conversation, artifact.conversation_id))
        raise ServiceError("FORBIDDEN", 403)

    async def write_reception_object(self, reference, content, digest):
        row, owner = await self._descriptor(reference)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ServiceError("FILE_INVALID", 422)
        return await self.files.write(owner, str(row.id), row.filename, row.media_type, content, row.category)

    async def read_reception_object(self, reference, size, digest):
        row, owner = await self._descriptor(reference)
        content = await self.files.read(owner, str(row.id))
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        return content

    async def delete_expired_object(self, reference, *, owner=None):
        if owner is None:
            _, owner = await self._descriptor(reference)
        await self.files.remove(owner, str(UUID(str(reference))))

    async def verify_unversioned_cleanup(self):
        await self.files.health()

    async def verify_object_absent(self, reference, *, owner=None):
        if owner is None:
            _, owner = await self._descriptor(reference)
        if not await self.files.removed(owner, str(UUID(str(reference)))):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
