from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from test_identity_sessions import actor, login
from test_reception_files import PDF

from xiaohe.contracts import ServiceError

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


class Objects:
    def __init__(self):
        self.values = {}

    async def write_reception_object(self, key, content, digest):
        self.values[key] = content

    async def read_reception_object(self, key, size, digest):
        return self.values[key]

    async def delete_expired_object(self, key):
        self.values.pop(key, None)


async def stream():
    yield PDF


async def test_abandoned_upload_cannot_be_attached_and_is_reaped(pg_factory):
    from xiaohe.reception.contracts import FormInitFile
    from xiaohe.reception.form_files import (
        abandon_form,
        init_form_file,
        owned_form_file,
        purge_form_files,
        write_form_file,
    )
    from xiaohe.reception.models import ReceptionFormFile
    _, token = await login(pg_factory)
    who, form_id, objects = await actor(pg_factory, token), uuid4(), Objects()
    upload = await init_form_file(pg_factory, who, form_id, FormInitFile(client_upload_id="upload-1",
        category="business_license", filename="资料.pdf", media_type="application/pdf", bytes=len(PDF)))
    ready = await write_form_file(pg_factory, who, form_id, upload.file.id, stream(), artifacts=objects)
    assert ready.status == "ready"
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            await owned_form_file(db, who, uuid4(), ready.id)
    await abandon_form(pg_factory, who, form_id)
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            await owned_form_file(db, who, form_id, ready.id)
        await db.execute(update(ReceptionFormFile).values(created_at=datetime.now(UTC)-timedelta(days=2)))
    await purge_form_files(pg_factory, objects)
    assert objects.values == {}


@pytest.mark.parametrize("status", ["pending", "accepted"])
async def test_abandoned_upload_cleanup_preserves_confirmed_or_pending_files(pg_factory, status):
    from xiaohe.reception.contracts import FormInitFile
    from xiaohe.reception.form_files import (
        abandon_form,
        detach_form_file,
        download_operational_form_file,
        init_form_file,
        purge_form_files,
        write_form_file,
    )
    from xiaohe.reception.models import ReceptionFormFile, ReceptionFormSubmission
    _, token = await login(pg_factory)
    who, form_id, objects = await actor(pg_factory, token), uuid4(), Objects()
    upload = await init_form_file(pg_factory, who, form_id, FormInitFile(client_upload_id="protected",
        category="business_license", filename="资料.pdf", media_type="application/pdf", bytes=len(PDF)))
    ready = await write_form_file(pg_factory, who, form_id, upload.file.id, stream(), artifacts=objects)
    async with pg_factory.begin() as db:
        db.add(ReceptionFormSubmission(system_id=who.system.system_id, tenant_id=who.system.tenant_id,
            user_id=who.user_id, form_id=form_id, client_submission_id="confirmed", input_hash="a"*64,
            payload={"files":[ready.model_dump(mode="json", by_alias=True)]}, status=status,
            receipt={"applicationId":"10"} if status=="accepted" else None))
        await db.execute(update(ReceptionFormFile).values(created_at=datetime.now(UTC)-timedelta(days=2)))
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await detach_form_file(pg_factory, who, form_id, ready.id)
    await abandon_form(pg_factory, who, form_id)
    await purge_form_files(pg_factory, objects)
    assert list(objects.values.values()) == [PDF]
    if status == "accepted":
        _, content = await download_operational_form_file(pg_factory, who.system.system_id, ready.id, "10", who.user_id, artifacts=objects)
        assert content == PDF
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            await download_operational_form_file(pg_factory, who.system.system_id, ready.id, "other", who.user_id, artifacts=objects)


async def test_late_object_after_abandon_is_reaped_even_after_previous_scan(pg_factory):
    from xiaohe.reception.contracts import FormInitFile
    from xiaohe.reception.form_files import (
        abandon_form,
        init_form_file,
        purge_form_files,
        write_form_file,
    )
    from xiaohe.reception.models import ReceptionFormFile
    _, token = await login(pg_factory)
    who, form_id = await actor(pg_factory, token), uuid4()
    class LateObjects(Objects):
        async def write_reception_object(self, key, content, digest):
            await abandon_form(pg_factory, who, form_id)
            async with pg_factory.begin() as db:
                await db.execute(update(ReceptionFormFile).values(created_at=datetime.now(UTC)-timedelta(days=2)))
            await purge_form_files(pg_factory, self)
            await super().write_reception_object(key, content, digest)
    objects = LateObjects()
    upload = await init_form_file(pg_factory, who, form_id, FormInitFile(client_upload_id="late",
        category="case", filename="资料.pdf", media_type="application/pdf", bytes=len(PDF)))
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await write_form_file(pg_factory, who, form_id, upload.file.id, stream(), artifacts=objects)
    assert objects.values
    await purge_form_files(pg_factory, objects)
    assert objects.values == {}
