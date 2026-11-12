import importlib
from uuid import uuid4

import pytest
from native_support import skills
from pydantic import ValidationError
from sqlalchemy import select, text

from xiaohe.skill_schema import SkillValidationError


@pytest.mark.pg
@pytest.mark.asyncio
async def test_policy_record_is_immutable(pg_factory):
    assert importlib.util.find_spec("xiaohe.lead_skills.store") is not None, (
        "policy persistence missing"
    )
    from xiaohe.lead_skills.store import load_policy, persist_policy
    from xiaohe.models import LeadPolicyRecord

    policy = skills().field_policies["buyer"]
    async with pg_factory.begin() as db:
        assert await persist_policy(db, policy) == policy.policy_id
        assert await persist_policy(db, policy) == policy.policy_id
    async with pg_factory.begin() as db:
        assert await load_policy(db, policy.policy_id) == policy
        row = await db.scalar(select(LeadPolicyRecord))
        row.payload = row.payload | {"direction": "supplier"}
    with pytest.raises(SkillValidationError):
        async with pg_factory() as db:
            await load_policy(db, policy.policy_id)


def test_direct_evidence_does_not_forge_summary():
    from xiaohe.agents import contracts

    assert hasattr(contracts, "LeadConfirmationEvidence"), "direct confirmation evidence missing"
    cls = contracts.LeadConfirmationEvidence
    raw = dict(
        version=1,
        command_id=uuid4(),
        run_id=uuid4(),
        message_id=uuid4(),
        user_id="member",
        draft_id=uuid4(),
        revision=2,
        agent_id="xiaohe-buyer",
        field_policy_id="a" * 64,
        interaction_policy_id="b" * 64,
        payload_digest="c" * 64,
    )
    direct = cls(source="semantic_direct", summary_version=None, **raw)
    assert direct.summary_version is None
    with pytest.raises(ValidationError):
        cls(source="button", summary_version=None, **raw)
    with pytest.raises(ValidationError):
        cls(source="semantic_reviewed", summary_version=1, **raw)


@pytest.mark.pg
@pytest.mark.asyncio
async def test_submitting_draft_cannot_lose_field_policy(pg_factory):
    from sqlalchemy.exc import IntegrityError
    from test_reception_submission import confirmed

    from xiaohe.reception.models import ReceptionSubmission

    _, submission = await confirmed(pg_factory)
    async with pg_factory() as db:
        before = (await db.get(ReceptionSubmission, submission.id)).payload
    with pytest.raises(IntegrityError):
        async with pg_factory.begin() as db:
            await db.execute(text("UPDATE xiaohe.reception_drafts SET field_policy_id=NULL"))
    async with pg_factory() as db:
        assert (await db.get(ReceptionSubmission, submission.id)).payload == before
