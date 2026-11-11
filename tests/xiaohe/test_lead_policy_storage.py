import importlib
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from test_lead_skills import skills

from xiaohe.workflows.models import WorkflowValidationError


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
    with pytest.raises(WorkflowValidationError):
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
async def test_expand_migration_preserves_legacy_submissions(pg_factory, pg_url, job_app):
    from test_reception_submission import confirmed

    from xiaohe.reception.models import ReceptionSubmission

    assert "confirmation_evidence" in ReceptionSubmission.__table__.columns, (
        "expand migration missing"
    )
    _, submission = await confirmed(pg_factory)
    engine = create_engine(pg_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                # 构造未引入 Skill 前的样本；不能把新绑定伪称为旧库数据。
                connection.execute(text("UPDATE xiaohe.reception_drafts SET field_policy_id=NULL"))
                command.downgrade(config, "0012")
                before = connection.execute(
                    text("SELECT row_to_json(s) FROM xiaohe.reception_submissions s")
                ).scalar_one()
                drafts = (
                    connection.execute(text("SELECT row_to_json(d) FROM xiaohe.reception_drafts d"))
                    .scalars()
                    .all()
                )
                command.upgrade(config, "head")
                after = connection.execute(
                    text("SELECT row_to_json(s) FROM xiaohe.reception_submissions s")
                ).scalar_one()
                assert {k: after[k] for k in before} == before
                assert after["confirmation_evidence"] is None
                assert after["submission_key"] == submission.submission_key
                actual = (
                    connection.execute(text("SELECT row_to_json(d) FROM xiaohe.reception_drafts d"))
                    .scalars()
                    .all()
                )
                assert [
                    {k: d[k] for k in old} for d, old in zip(actual, drafts, strict=True)
                ] == drafts
                connection.execute(
                    text(
                        "INSERT INTO xiaohe.lead_policies (policy_id,kind,payload) VALUES (:id,'fields','{}')"
                    ),
                    {"id": "a" * 64},
                )
                connection.execute(
                    text("UPDATE xiaohe.reception_drafts SET field_policy_id=:id"), {"id": "a" * 64}
                )
                with pytest.raises(RuntimeError, match="lead policy"):
                    command.downgrade(config, "0012")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
