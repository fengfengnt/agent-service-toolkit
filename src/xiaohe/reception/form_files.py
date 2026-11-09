import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from xiaohe.contracts import ServiceError
from xiaohe.models import System, User
from xiaohe.reception.contracts import UploadView
from xiaohe.reception.files import MAX_BYTES, _filename, _ref, validate_material
from xiaohe.reception.forms import authority, form_scope
from xiaohe.reception.models import ReceptionFormFile, ReceptionFormSubmission
from xiaohe.reception.store import _uuid


async def owned_form_file(db, actor, form_id, file_id):
    await authority(db, actor)
    scope = form_scope(actor)
    row = await db.scalar(select(ReceptionFormFile).where(ReceptionFormFile.id == _uuid(file_id)).with_for_update())
    if row is None or (row.system_id, row.tenant_id, row.user_id, row.form_id) != (*scope, _uuid(form_id)) or row.detached:
        raise ServiceError("FORBIDDEN", 403)
    return row


async def init_form_file(factory, actor, form_id, request):
    _filename(request.filename, request.media_type)
    scope = form_scope(actor)
    file_id = uuid4()
    raw = request.model_dump(mode="json", by_alias=True)
    async with factory.begin() as db:
        await authority(db, actor)
        await db.execute(insert(ReceptionFormFile).values(id=file_id, system_id=scope[0], tenant_id=scope[1], user_id=scope[2],
            form_id=_uuid(form_id), client_upload_id=request.client_upload_id, upload_request=raw,
            category=request.category, filename=request.filename, media_type=request.media_type, bytes=request.bytes,
            object_key=f"xiaohe/reception/forms/{file_id}/initial", object_keys=[], status="pending", detached=False).on_conflict_do_nothing())
        row = await db.scalar(select(ReceptionFormFile).where(ReceptionFormFile.system_id == scope[0],
            ReceptionFormFile.tenant_id == scope[1], ReceptionFormFile.user_id == scope[2],
            ReceptionFormFile.form_id == _uuid(form_id), ReceptionFormFile.client_upload_id == request.client_upload_id).with_for_update())
        if row.upload_request != raw or row.detached:
            raise ServiceError("DRAFT_CONFLICT", 409)
        return UploadView(file=_ref(row), upload_path=f"/ai-api/v1/reception/application-form/{form_id}/files/{row.id}")


async def write_form_file(factory, actor, form_id, file_id, stream, *, artifacts):
    key = f"xiaohe/reception/forms/{_uuid(file_id)}/{uuid4()}"
    async with factory.begin() as db:
        row = await owned_form_file(db, actor, form_id, file_id)
        expected = _ref(row)
        row.object_keys = row.object_keys + [key]
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
        row = await owned_form_file(db, actor, form_id, file_id)
        if row.status == "ready":
            if row.sha256 != digest:
                raise ServiceError("DRAFT_CONFLICT", 409)
            return _ref(row)
    await artifacts.write_reception_object(key, content, digest)
    async with factory.begin() as db:
        row = await owned_form_file(db, actor, form_id, file_id)
        if row.status == "ready" and row.sha256 != digest:
            raise ServiceError("DRAFT_CONFLICT", 409)
        if row.status != "ready":
            row.status, row.sha256, row.object_key = "ready", digest, key
        return _ref(row)


async def referenced(db, row):
    return await db.scalar(select(ReceptionFormSubmission.client_submission_id).where(
        ReceptionFormSubmission.system_id == row.system_id, ReceptionFormSubmission.tenant_id == row.tenant_id,
        ReceptionFormSubmission.user_id == row.user_id, ReceptionFormSubmission.status.in_(("pending", "accepted")),
        ReceptionFormSubmission.payload["files"].contains([{"id": str(row.id)}])).limit(1)) is not None


async def detach_form_file(factory, actor, form_id, file_id):
    async with factory.begin() as db:
        row = await owned_form_file(db, actor, form_id, file_id)
        if await referenced(db, row):
            raise ServiceError("DRAFT_CONFLICT", 409)
        row.detached = True


async def abandon_form(factory, actor, form_id):
    async with factory.begin() as db:
        await authority(db, actor)
        scope = form_scope(actor)
        rows = (await db.scalars(select(ReceptionFormFile).where(ReceptionFormFile.system_id == scope[0],
            ReceptionFormFile.tenant_id == scope[1], ReceptionFormFile.user_id == scope[2],
            ReceptionFormFile.form_id == _uuid(form_id)).order_by(ReceptionFormFile.id).with_for_update())).all()
        for row in rows:
            if not await referenced(db, row):
                row.detached = True


async def download_form_file(factory, actor, file_id, *, artifacts):
    async def authorized(db):
        await authority(db, actor)
        row = await db.get(ReceptionFormFile, _uuid(file_id))
        if row is None or (row.system_id, row.tenant_id, row.user_id) != form_scope(actor) or row.detached or row.status != "ready":
            raise ServiceError("FORBIDDEN", 403)
        return _ref(row), row.object_key
    async with factory.begin() as db:
        ref, key = await authorized(db)
    content = await artifacts.read_reception_object(key, ref.bytes, ref.sha256)
    async with factory.begin() as db:
        await authorized(db)
    return ref, content


async def download_operational_form_file(factory, system_id, file_id, application_id, owner_ai_user_id, *, artifacts):
    async def authorized(db):
        user, system = await db.get(User, owner_ai_user_id), await db.get(System, system_id)
        if user is None or not user.enabled or user.system_id != system_id or system is None or not system.enabled:
            raise ServiceError("FORBIDDEN", 403)
        submission = await db.scalar(select(ReceptionFormSubmission).where(ReceptionFormSubmission.system_id == system_id,
            ReceptionFormSubmission.tenant_id == user.tenant_id, ReceptionFormSubmission.user_id == user.user_id,
            ReceptionFormSubmission.status == "accepted", ReceptionFormSubmission.receipt["applicationId"].astext == application_id,
            ReceptionFormSubmission.payload["files"].contains([{"id": str(file_id)}])).limit(1))
        row = await db.get(ReceptionFormFile, _uuid(file_id))
        if submission is None or row is None or row.status != "ready" or (row.system_id, row.tenant_id, row.user_id) != (system_id, user.tenant_id, user.user_id):
            raise ServiceError("FORBIDDEN", 403)
        expected = next(f for f in submission.payload["files"] if f["id"] == str(file_id))
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


async def purge_form_files(factory, artifacts, limit=100):
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    async with factory.begin() as db:
        rows = (await db.scalars(select(ReceptionFormFile).where(ReceptionFormFile.created_at < cutoff)
            .order_by(ReceptionFormFile.cleanup_checked_at.asc().nulls_first(), ReceptionFormFile.id)
            .limit(limit).with_for_update(skip_locked=True))).all()
        for row in rows:
            protected = await referenced(db, row)
            if not protected:
                row.detached = True
            for key in row.object_keys:
                if not protected or key != row.object_key:
                    await artifacts.delete_expired_object(key)
            # 保留定位记录，后续扫描继续回收 DB 提交失败或迟到写入的对象。
            row.cleanup_checked_at = datetime.now(UTC)
