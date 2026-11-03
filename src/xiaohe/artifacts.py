import asyncio
import hashlib
import json
import re
from uuid import UUID

import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import func, select

from xiaohe.contracts import ArtifactView, ServiceError, WriteFence
from xiaohe.conversations import require_conversation
from xiaohe.events import record_event
from xiaohe.models import Artifact, ArtifactVersion, Conversation
from xiaohe.runs import require_write

_MAX_BYTES = 32 * 1024 * 1024
_PREVIEW_BYTES = 128 * 1024


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
        self._s3 = None
        if all(
            (
                settings.minio_endpoint,
                settings.minio_bucket,
                settings.minio_access_key,
                settings.minio_secret_key,
            )
        ):
            self._s3 = boto3.client(
                "s3",
                endpoint_url=settings.minio_endpoint,
                region_name="us-east-1",
                aws_access_key_id=settings.minio_access_key.get_secret_value(),
                aws_secret_access_key=settings.minio_secret_key.get_secret_value(),
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"total_max_attempts": 1},
                    proxies={},
                ),
            )

    async def _io(self, operation):
        if self._s3 is None:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
        failed = False
        result = None
        try:
            result = await asyncio.to_thread(operation)
        except Exception:
            failed = True
        if failed:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        return result

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
            row = ArtifactVersion(
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

    def _upload(self, row, content):
        bucket = self.settings.minio_bucket
        try:
            self._s3.put_object(
                Bucket=bucket,
                Key=row.object_key,
                Body=content,
                ContentType=row.media_type,
                Metadata={"sha256": row.content_digest},
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
        response = self._s3.get_object(Bucket=bucket, Key=row.object_key)
        body = response["Body"]
        checked = hashlib.sha256()
        size = 0
        preview = bytearray()
        try:
            while block := body.read(65536):
                size += len(block)
                if size > _MAX_BYTES:
                    raise ValueError("object exceeds internal bound")
                checked.update(block)
                if len(preview) <= _PREVIEW_BYTES:
                    preview.extend(block)
        finally:
            body.close()
        if checked.hexdigest() != row.content_digest or size != len(content):
            raise ValueError("object digest mismatch")
        view = {"format": "download_only"}
        if row.media_type in ("text/plain", "text/markdown") and size <= _PREVIEW_BYTES:
            text = None
            try:
                text = preview.decode("utf-8")
            except UnicodeDecodeError:
                pass
            if text is not None and "\x00" not in text:
                view = {"format": "text", "text": text}
        return size, view

    async def write_artifact_object(self, version_id: UUID, content: bytes):
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
            _, row = await self._owned_version(db, fence, version_id)
            if row.status == "ready":
                return
        size, preview = await self._io(lambda: self._upload(row, content))
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
            return _view(await self._read(db, actor, artifact_id, version))

    async def download_artifact(self, actor, artifact_id: UUID, version: int):
        async with self.factory.begin() as db:
            row = await self._read(db, actor, artifact_id, version)
            if row.status != "ready":
                raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
        response = await self._io(
            lambda: self._s3.get_object(Bucket=self.settings.minio_bucket, Key=row.object_key)
        )
        body = response["Body"]

        async def chunks():
            try:
                while block := await self._io(lambda: body.read(65536)):
                    async with self.factory.begin() as db:
                        await self._read(db, actor, artifact_id, version)
                    yield block
            finally:
                with anyio.CancelScope(shield=True):
                    await asyncio.to_thread(body.close)

        return chunks()

    async def close(self):
        if self._s3 is not None:
            await asyncio.to_thread(self._s3.close)
