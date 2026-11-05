import hashlib
import io
import re
import zipfile
from uuid import uuid4

from sqlalchemy import select

from xiaohe.contracts import ServiceError
from xiaohe.models import Conversation, System, User
from xiaohe.reception.contracts import FileRef, UploadView
from xiaohe.reception.models import ReceptionFile, ReceptionSubmission
from xiaohe.reception.store import _uuid, draft_view, require_draft

MAX_BYTES = 32 * 1024 * 1024
EXTENSIONS = {
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (".pptx",),
}


def _filename(filename, media_type):
    if (
        media_type not in EXTENSIONS
        or not filename.lower().endswith(EXTENSIONS[media_type])
        or re.search(r"[\\/\x00-\x1f\x7f]", filename)
    ):
        raise ServiceError("FILE_INVALID", 422)


def validate_material(content: bytes, filename: str, media_type: str):
    if len(content) > MAX_BYTES:
        raise ServiceError("FILE_TOO_LARGE", 413)
    _filename(filename, media_type)
    valid = False
    if media_type == "application/pdf":
        valid = content.startswith(b"%PDF-")
    elif media_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    elif content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                required = (
                    "word/document.xml"
                    if filename.lower().endswith(".docx")
                    else "ppt/presentation.xml"
                )
                valid = "[Content_Types].xml" in names and required in names
        except (zipfile.BadZipFile, ValueError):
            pass
    if not valid:
        raise ServiceError("FILE_INVALID", 422)


def _ref(row):
    return FileRef(
        id=str(row.id),
        category=row.category,
        filename=row.filename,
        media_type=row.media_type,
        bytes=row.bytes,
        sha256=row.sha256,
        status=row.status,
    )


def _mutable(draft):
    if draft.state not in ("collecting", "review"):
        raise ServiceError("DRAFT_CONFLICT", 409)


def _changed(draft):
    draft.revision += 1
    draft.state, draft.summary_version = "collecting", None


async def init_file(db, actor, draft_id, request):
    draft = await require_draft(db, actor, draft_id)
    prior = await db.scalar(
        select(ReceptionFile).where(
            ReceptionFile.draft_id == draft.id,
            ReceptionFile.client_upload_id == request.client_upload_id,
        )
    )
    value = request.model_dump(mode="json", by_alias=True)
    if prior is not None:
        if prior.upload_request != value or prior.detached:
            raise ServiceError("DRAFT_CONFLICT", 409)
        return UploadView(
            file=_ref(prior), upload_path=f"/ai-api/v1/reception/drafts/{draft.id}/files/{prior.id}"
        )
    if draft.revision != request.expected_revision:
        raise ServiceError("DRAFT_CONFLICT", 409)
    _mutable(draft)
    _filename(request.filename, request.media_type)
    file_id = uuid4()
    row = ReceptionFile(
        id=file_id,
        draft_id=draft.id,
        conversation_id=draft.conversation_id,
        system_id=draft.system_id,
        tenant_id=draft.tenant_id,
        client_upload_id=request.client_upload_id,
        upload_request=value,
        category=request.category,
        filename=request.filename,
        media_type=request.media_type,
        bytes=request.bytes,
        status="pending",
        object_key=f"xiaohe/reception/{file_id}/initial",
    )
    db.add(row)
    _changed(draft)
    await db.flush()
    return UploadView(
        file=_ref(row), upload_path=f"/ai-api/v1/reception/drafts/{draft.id}/files/{row.id}"
    )


async def _owned_file(db, actor, draft_id, file_id):
    draft = await require_draft(db, actor, draft_id)
    row = await db.scalar(
        select(ReceptionFile)
        .where(ReceptionFile.id == _uuid(file_id), ReceptionFile.draft_id == draft.id)
        .execution_options(populate_existing=True)
    )
    if row is None or row.detached:
        raise ServiceError("FORBIDDEN", 403)
    return draft, row


async def write_file(factory, actor, draft_id, file_id, stream, *, artifacts):
    key = f"xiaohe/reception/{_uuid(file_id)}/{uuid4()}"
    async with factory.begin() as db:
        draft, row = await _owned_file(db, actor, draft_id, file_id)
        _mutable(draft)
        expected = _ref(row)
        if row.status != "ready":
            # shortcut: 登录用户的失败尝试随草稿保留，确定独立保留期限后再加入回收策略。
            row.object_keys = row.object_keys + [key]
    try:
        body = bytearray()
        async for chunk in stream:
            if len(body) + len(chunk) > MAX_BYTES:
                raise ServiceError("FILE_TOO_LARGE", 413)
            body.extend(chunk)
        content = bytes(body)
        if len(content) != expected.bytes:
            raise ServiceError("FILE_INVALID", 422)
        validate_material(content, expected.filename, expected.media_type)
        digest = hashlib.sha256(content).hexdigest()
        async with factory.begin() as db:
            draft, row = await _owned_file(db, actor, draft_id, file_id)
            _mutable(draft)
            if row.status == "ready":
                if row.sha256 != digest:
                    raise ServiceError("DRAFT_CONFLICT", 409)
                return _ref(row)
        await artifacts.write_reception_object(key, content, digest)
        async with factory.begin() as db:
            draft, row = await _owned_file(db, actor, draft_id, file_id)
            _mutable(draft)
            if row.status == "ready":
                if row.sha256 != digest:
                    raise ServiceError("DRAFT_CONFLICT", 409)
                return _ref(row)
            row.status, row.sha256, row.object_key = "ready", digest, key
            _changed(draft)
            return _ref(row)
    except Exception:
        try:
            async with factory.begin() as db:
                draft, row = await _owned_file(db, actor, draft_id, file_id)
                if row.status != "ready":
                    row.status = "failed"
        except ServiceError:
            pass
        raise


async def detach_file(db, actor, draft_id, file_id, expected_revision):
    draft = await require_draft(db, actor, draft_id, expected_revision)
    _mutable(draft)
    inherited = [file for file in draft.source_files if file["id"] != str(file_id)]
    if len(inherited) != len(draft.source_files):
        draft.source_files = inherited
    else:
        _, row = await _owned_file(db, actor, draft_id, file_id)
        referenced = await db.scalar(
            select(ReceptionSubmission.id)
            .where(
                ReceptionSubmission.status.in_(["pending", "accepted"]),
                ReceptionSubmission.payload["files"].contains([{"id": str(row.id)}]),
            )
            .limit(1)
        )
        if referenced:
            raise ServiceError("DRAFT_CONFLICT", 409)
        row.detached = True
    _changed(draft)
    await db.flush()
    return await draft_view(db, draft)


async def download_file(factory, actor, file_id, *, artifacts):
    async with factory.begin() as db:
        row = await db.get(ReceptionFile, _uuid(file_id))
        if row is None:
            raise ServiceError("FORBIDDEN", 403)
        await require_draft(db, actor, row.draft_id)
        if row.status != "ready" or row.detached:
            raise ServiceError("FORBIDDEN", 403)
        ref, key = _ref(row), row.object_key
    content = await artifacts.read_reception_object(key, ref.bytes, ref.sha256)
    async with factory.begin() as db:
        await require_draft(db, actor, row.draft_id)
    return ref, content


async def download_operational_file(
    factory, system_id, file_id, application_id, owner_ai_user_id, *, artifacts
):
    async def authorized(db):
        user = await db.get(User, owner_ai_user_id)
        system = await db.get(System, system_id)
        if (
            user is None
            or not user.enabled
            or user.system_id != system_id
            or system is None
            or not system.enabled
        ):
            raise ServiceError("FORBIDDEN", 403)
        submission = await db.scalar(
            select(ReceptionSubmission)
            .join(Conversation, Conversation.id == ReceptionSubmission.conversation_id)
            .where(
                ReceptionSubmission.system_id == system_id,
                Conversation.user_id == owner_ai_user_id,
                ReceptionSubmission.status == "accepted",
                ReceptionSubmission.receipt["applicationId"].astext == application_id,
                ReceptionSubmission.payload["files"].contains([{"id": str(file_id)}]),
            )
            .limit(1)
        )
        if submission is None:
            raise ServiceError("FORBIDDEN", 403)
        row = await db.get(ReceptionFile, _uuid(file_id))
        if row is None or row.status != "ready" or row.system_id != system_id:
            raise ServiceError("FORBIDDEN", 403)
        owner = await db.get(Conversation, row.conversation_id)
        if owner is None or owner.user_id != owner_ai_user_id or owner.tenant_id != user.tenant_id:
            raise ServiceError("FORBIDDEN", 403)
        expected = next(file for file in submission.payload["files"] if file["id"] == str(file_id))
        ref = _ref(row)
        if ref.model_dump(mode="json", by_alias=True) != expected:
            raise ServiceError("FORBIDDEN", 403)
        return ref, row.object_key

    async with factory.begin() as db:
        ref, key = await authorized(db)
    content = await artifacts.read_reception_object(key, ref.bytes, ref.sha256)
    async with factory.begin() as db:
        await authorized(db)
    return ref, content
