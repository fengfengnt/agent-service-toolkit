import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_admission import visitor

from xiaohe.contracts import ServiceError
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.fields import missing_required, validate_fields
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import create_draft, load_reception, patch_draft
from xiaohe.workflows.definitions import RESOURCE_ROOT
from xiaohe.workflows.loader import load_bundle, workflow_for


def definition(direction):
    return workflow_for(load_bundle(RESOURCE_ROOT), "xiaohe-" + direction)


def test_minimum_fields_and_explicit_clear():
    assert (
        missing_required("buyer", {"companyName": "甲公司"}, definition=definition("buyer")) == []
    )
    assert (
        missing_required("supplier", {"companyName": "甲公司"}, definition=definition("supplier"))
        == []
    )
    assert missing_required("buyer", {"companyName": None}, definition=definition("buyer")) == [
        "companyName"
    ]
    fields = dict(
        organizationName="行业协会",
        memberSize="100—500",
        coverage="本省",
        onlineChannels=["微信群"],
    )
    assert missing_required("organization", fields, definition=definition("organization")) == [
        "contactName"
    ]


def test_field_catalog_matches_frozen_shared_contract():
    schema = json.loads(
        (Path(__file__).parent / "contracts/reception-v1.schema.json").read_text("utf-8")
    )
    for direction in ("buyer", "organization", "supplier"):
        assert {key for group in definition(direction).rounds for key in group.fields} == set(
            schema["$defs"][direction + "DraftFields"]["properties"]
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"memberSize": "500"},
        {"onlineChannels": ["暂无", "微信群"]},
        {"onlineChannels": ["微信群", "微信群"]},
        {"organizationName": "甲"},
        {"email": "bad-email"},
        {"contactRole": "CEO"},
        {"managerId": "5"},
    ],
)
def test_organization_invalid_values_rejected(fields):
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        validate_fields("organization", fields)


@pytest.mark.pg
@pytest.mark.asyncio
async def test_drafts_reuse_key_preserve_round_and_do_not_create_business_records(pg_factory):
    who, conversation = await visitor(pg_factory)
    request = CreateDraft(client_draft_id="one", direction="buyer", kind="service")
    async with pg_factory.begin() as db:
        first = await create_draft(db, who, conversation, request)
        assert (await create_draft(db, who, conversation, request)).id == first.id
        changed = await patch_draft(db, who, first.id, 1, {"companyName": "甲公司"})
        assert changed.revision == 2 and changed.step == "round1"
        assert changed.missing_required == [] and changed.state == "collecting"
        assert (await patch_draft(db, who, first.id, 2, {"companyName": "甲公司"})).revision == 2
        second = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="two", direction="supplier", kind="service"),
        )
        await patch_draft(db, who, second.id, 1, {"companyName": "乙公司"})
        view = await load_reception(db, who, conversation)
        assert len(view.draft_refs) == 2
        assert {ref.subject_name for ref in view.draft_refs} == {"甲公司", "乙公司"}
        assert await db.scalar(select(func.count()).select_from(ReceptionDraft)) == 2
        cleared = await patch_draft(db, who, first.id, 2, {"companyName": None})
        assert cleared.missing_required == ["companyName"] and cleared.revision == 3


@pytest.mark.pg
@pytest.mark.asyncio
async def test_other_owner_and_reused_key_with_other_direction_rejected(pg_factory):
    who, conversation = await visitor(pg_factory)
    other, _ = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction="buyer", kind="service"),
        )
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="FORBIDDEN"):
            await patch_draft(db, other, draft.id, 1, {"companyName": "冒名"})
    async with pg_factory.begin() as db:
        with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
            await create_draft(
                db,
                who,
                conversation,
                CreateDraft(client_draft_id="one", direction="supplier", kind="service"),
            )


@pytest.mark.pg
@pytest.mark.asyncio
async def test_two_connections_cannot_overwrite_saved_revision(pg_factory):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction="buyer", kind="service"),
        )

    async def write(name):
        async with pg_factory.begin() as db:
            return await patch_draft(db, who, draft.id, 1, {"companyName": name})

    results = await asyncio.gather(write("甲公司"), write("乙公司"), return_exceptions=True)
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    failed = next(result for result in results if isinstance(result, BaseException))
    assert isinstance(failed, ServiceError) and failed.code == "DRAFT_CONFLICT"
