import asyncio
import io
import zipfile
from uuid import UUID

import pytest
from integration.minio_support import minio_env as minio_env
from sqlalchemy import func, update
from test_admission import visitor

from xiaohe.contracts import ServiceError
from xiaohe.models import Session
from xiaohe.reception.contracts import CreateDraft, InitFile
from xiaohe.reception.files import (
    detach_file,
    download_file,
    init_file,
    validate_material,
    write_file,
)
from xiaohe.reception.models import ReceptionFile
from xiaohe.reception.store import create_draft, load_reception

PDF = b"%PDF-1.7\ntest-only\n%%EOF"
MAX_BYTES = 32 * 1024 * 1024


def test_file_reader_credential_is_independent_of_other_service_secrets():
    from pydantic import ValidationError

    from xiaohe.settings import Settings

    for field in ("auth_secret", "reception_secret", "sso_bridge_secret"):
        values = dict(
            toolkit_base_url="http://runtime",
            auth_secret="fixture-auth",
            reception_file_secret="f" * 48,
        )
        values[field] = "f" * 48
        with pytest.raises(ValidationError):
            Settings(**values)


def test_material_types_and_bounded_signature_validation():
    validate_material(PDF, "企业说明.pdf", "application/pdf")
    validate_material(b"\x89PNG\r\n\x1a\n" + b"test-only", "企业.png", "image/png")
    validate_material(b"\xff\xd8\xff\xe0test-only\xff\xd9", "企业.jpg", "image/jpeg")
    for folder, extension, media in (
        ("word", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (
            "ppt",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr(
                folder + ("/document.xml" if folder == "word" else "/presentation.xml"), "<root/>"
            )
        validate_material(buffer.getvalue(), "企业." + extension, media)
    for body, filename, media in (
        (b"<html>private</html>", "企业.pdf", "application/pdf"),
        (PDF, "../企业.pdf", "application/pdf"),
        (PDF, "企业.html", "application/pdf"),
    ):
        with pytest.raises(ServiceError, match="FILE_INVALID"):
            validate_material(body, filename, media)
    with pytest.raises(ServiceError, match="FILE_TOO_LARGE"):
        validate_material(PDF + b" " * MAX_BYTES, "企业.pdf", "application/pdf")


async def chunks(body):
    for start in range(0, len(body), 65536):
        yield body[start : start + 65536]


async def draft_file(factory, size=len(PDF)):
    who, conversation = await visitor(factory)
    async with factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", kind="service", direction="supplier"),
        )
        request = InitFile(
            client_upload_id="file-one",
            category="product_manual",
            filename="企业.pdf",
            media_type="application/pdf",
            bytes=size,
            expected_revision=draft.revision,
        )
        upload = await init_file(db, who, draft.id, request)
        assert (await init_file(db, who, draft.id, request)).file.id == upload.file.id
    return who, conversation, draft.id, upload


@pytest.mark.pg
@pytest.mark.minio
@pytest.mark.asyncio
async def test_real_private_upload_download_boundaries_and_detach(pg_factory, minio_env):
    body = PDF + b" " * (MAX_BYTES - len(PDF))
    who, conversation, draft_id, upload = await draft_file(pg_factory, len(body))
    ready = await write_file(
        pg_factory, who, draft_id, upload.file.id, chunks(body), artifacts=minio_env.service
    )
    assert ready.status == "ready" and ready.bytes == MAX_BYTES and ready.sha256
    ref, downloaded = await download_file(pg_factory, who, ready.id, artifacts=minio_env.service)
    assert downloaded == body and ref == ready
    stranger, _ = await visitor(pg_factory)
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        await download_file(pg_factory, stranger, ready.id, artifacts=minio_env.service)
    async with pg_factory.begin() as db:
        view = await load_reception(db, who, conversation)
        result = await detach_file(db, who, draft_id, ready.id, view.active_draft.revision)
        assert not result.files
        assert (await db.get(Session, who.session_id)).message_count == 0


@pytest.mark.pg
@pytest.mark.minio
@pytest.mark.asyncio
async def test_expired_owner_during_upload_cannot_become_ready(pg_factory, minio_env):
    who, _, draft_id, upload = await draft_file(pg_factory)

    async def expire():
        yield PDF[:8]
        async with pg_factory.begin() as db:
            await db.execute(
                update(Session)
                .where(Session.id == who.session_id)
                .values(expires_at=func.clock_timestamp())
            )
        yield PDF[8:]

    with pytest.raises(ServiceError):
        await write_file(
            pg_factory, who, draft_id, upload.file.id, expire(), artifacts=minio_env.service
        )
    async with pg_factory() as db:
        assert (await db.get(ReceptionFile, UUID(upload.file.id))).status != "ready"


@pytest.mark.pg
@pytest.mark.minio
@pytest.mark.asyncio
async def test_parallel_upload_does_not_overwrite_first_ready_object(pg_factory, minio_env):
    who, _, draft_id, upload = await draft_file(pg_factory)
    alternative = PDF.replace(b"test-only", b"test-evil")
    results = await asyncio.gather(
        *[
            write_file(
                pg_factory, who, draft_id, upload.file.id, chunks(body), artifacts=minio_env.service
            )
            for body in (PDF, alternative)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert any(
        isinstance(result, ServiceError) and result.code == "DRAFT_CONFLICT" for result in results
    )
    ref, content = await download_file(pg_factory, who, upload.file.id, artifacts=minio_env.service)
    assert content in (PDF, alternative) and ref.status == "ready"
