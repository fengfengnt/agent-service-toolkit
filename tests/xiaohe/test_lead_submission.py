from uuid import UUID

import pytest
from sqlalchemy import select
from test_agent_operations import cache_decision, prepare
from test_lead_skills import skills

from xiaohe.agents.contracts import SemanticDecision
from xiaohe.agents.nodes import build_agent_frame, make_node_executors
from xiaohe.contracts import ServiceError
from xiaohe.lead_skills.store import bind_command_policy
from xiaohe.models import Command
from xiaohe.reception.contracts import SubmissionResult, SubmitReceipt
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import patch_draft
from xiaohe.workflows.models import DecideNode, OperationNode

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


class Business:
    def __init__(self, *, lose_reply=False):
        self.payloads = []
        self.receipt = None
        self.lose_reply = lose_reply

    async def result(self, actor, key):
        assert actor.ai_user_id and key
        return SubmissionResult(
            status="accepted" if self.receipt else "not_found", receipt=self.receipt
        )

    async def submit(self, actor, payload):
        self.payloads.append(payload.model_dump(mode="json", by_alias=True))
        self.receipt = SubmitReceipt(
            submission_key=payload.submission_key,
            kind="service",
            application_id="fake-application",
            revision=1,
            status="accepted",
        )
        if self.lose_reply:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        return self.receipt


async def lead_prepare(factory, tmp_path, *, role="buyer", text="好的"):
    bundle, old = await prepare(factory, tmp_path, direction=role, text=text)
    async with factory.begin() as db:
        row = await db.get(ReceptionDraft, old.draft_id)
        row.state, row.step, row.summary_version = "collecting", "round2", None
        command = await db.get(Command, old.command_id)
        await bind_command_policy(
            db, command, skills().interaction_policy, workflow_bundle_id=bundle.bundle_id
        )
    return bundle, await build_agent_frame(factory, old.fence, old.agent_id, bundle)


async def submit(factory, bundle, frame, client, decision=None):
    decision = decision or SemanticDecision(action="confirm_current_summary")
    async with factory() as db:
        command = await db.get(Command, frame.command_id)
        cached = command.payload.get("agentStages", {}).get(frame.agent_id, {}).get("decision")
    if cached is None:
        await cache_decision(factory, frame, decision)
    nodes = make_node_executors(factory, model=None, business_client=client, bundle=bundle)
    state = {"frame": frame.model_dump(), "decision": decision.model_dump()}
    route = DecideNode(
        id="route",
        name="选择",
        type="decide",
        routes={"confirm_current_summary": "submit"},
        fallback="done",
    )
    state |= await nodes["decide"](route, state, {})
    assert state["event"] == decision.action
    node = OperationNode(
        id="submit", name="提交", type="operation", operation="confirm_submission", next="done"
    )
    return await nodes["operation"](node, state, {})


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
@pytest.mark.parametrize("text", ["好的", "可以"])
async def test_semantic_confirmation_submits_without_summary(
    pg_factory, job_app, tmp_path, role, text
):
    bundle, frame = await lead_prepare(pg_factory, tmp_path, role=role, text=text)
    client = Business()
    await submit(pg_factory, bundle, frame, client)
    async with pg_factory() as db:
        row = await db.scalar(select(ReceptionSubmission))
        assert row.summary_version is None
        assert row.confirmation_evidence["source"] == "semantic_direct"
        assert row.status == "accepted" and len(client.payloads) == 1
        assert row.payload["direction"] == role


async def test_same_turn_patch_and_submit_uses_final_snapshot(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path, text="联系人改为李四，可以提交")
    decision = SemanticDecision.model_validate(
        {
            "action": "confirm_current_summary",
            "candidates": {"contactName": {"value": "李四", "evidence": "联系人改为李四"}},
        }
    )
    client = Business()
    await submit(pg_factory, bundle, frame, client, decision)
    assert client.payloads[0]["requestFields"]["contactName"] == "李四"
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        record = await db.scalar(select(ReceptionSubmission))
        assert row.revision == frame.draft_revision + 1 == record.draft_revision


async def test_short_reply_in_other_context_does_not_submit(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path, text="好的，请继续介绍")
    decision = SemanticDecision(action="consultation")
    await cache_decision(pg_factory, frame, decision)
    nodes = make_node_executors(pg_factory, model=None, business_client=Business(), bundle=bundle)
    node = DecideNode(
        id="route", name="选择", type="decide", routes={"consultation": "answer"}, fallback="done"
    )
    result = await nodes["decide"](
        node, {"frame": frame.model_dump(), "decision": decision.model_dump()}, {}
    )
    assert result["event"] == "consultation"
    async with pg_factory() as db:
        assert await db.scalar(select(ReceptionSubmission)) is None


async def test_external_revision_change_rejects_semantic_submit(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        await patch_draft(db, frame.actor, frame.draft_id, frame.draft_revision, {"city": "杭州"})
    client = Business()
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await submit(pg_factory, bundle, frame, client)
    assert not client.payloads


async def test_missing_fields_do_not_submit(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        await patch_draft(
            db, frame.actor, frame.draft_id, frame.draft_revision, {"contactPhone": None}
        )
    frame = await build_agent_frame(pg_factory, frame.fence, frame.agent_id, bundle)
    client = Business()
    result = await submit(pg_factory, bundle, frame, client)
    assert "联系电话" in result["operation_result"]["text"]
    assert not client.payloads


async def test_patch_submit_retry_does_not_apply_patch_twice(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path, text="联系人李四，可以提交")
    decision = SemanticDecision.model_validate(
        {
            "action": "confirm_current_summary",
            "candidates": {"contactName": {"value": "李四", "evidence": "联系人李四"}},
        }
    )
    client = Business(lose_reply=True)
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit(pg_factory, bundle, frame, client, decision)
    await submit(pg_factory, bundle, frame, client, decision)
    assert len(client.payloads) == 1
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, frame.draft_id)).revision == frame.draft_revision + 1


async def test_invalid_same_turn_value_does_not_submit(pg_factory, job_app, tmp_path):
    bundle, frame = await lead_prepare(pg_factory, tmp_path, text="企业名称清空并提交")
    decision = SemanticDecision.model_validate(
        {
            "action": "confirm_current_summary",
            "candidates": {"companyName": {"value": "", "evidence": "企业名称清空"}},
        }
    )
    client = Business()
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        await submit(pg_factory, bundle, frame, client, decision)
    assert not client.payloads


@pytest.mark.parametrize(
    "key,value",
    [
        ("user_id", "other-member"),
        ("agent_id", "xiaohe-supplier"),
        ("field_policy_id", "0" * 64),
        ("interaction_policy_id", "0" * 64),
        ("payload_digest", "0" * 64),
        ("revision", 999),
    ],
)
async def test_forged_direct_evidence_is_rejected(pg_factory, job_app, tmp_path, key, value):
    from xiaohe.agents.contracts import LeadConfirmationEvidence
    from xiaohe.reception.submission import confirm_locked_draft

    bundle, frame = await lead_prepare(pg_factory, tmp_path)
    client = Business(lose_reply=True)
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await submit(pg_factory, bundle, frame, client)
    async with pg_factory.begin() as db:
        record = await db.scalar(select(ReceptionSubmission))
        proof = LeadConfirmationEvidence.model_validate(record.confirmation_evidence)
        proof = proof.model_copy(update={key: value})
        row = await db.get(ReceptionDraft, frame.draft_id)
        with pytest.raises(ServiceError):
            await confirm_locked_draft(
                db, row, row.revision, None, frame.message_id, frame.actor.user_id, evidence=proof
            )
    assert len(client.payloads) == 1


@pytest.mark.parametrize("change", ["attach", "detach"])
async def test_attachment_revision_change_rejects_semantic_submit(
    pg_factory, job_app, tmp_path, change
):
    from xiaohe.reception.contracts import InitFile
    from xiaohe.reception.files import detach_file, init_file

    bundle, frame = await lead_prepare(pg_factory, tmp_path, role="supplier")
    async with pg_factory.begin() as db:
        upload = await init_file(
            db,
            frame.actor,
            frame.draft_id,
            InitFile(
                client_upload_id="race-file",
                category="product_manual",
                filename="合成测试.pdf",
                media_type="application/pdf",
                bytes=5,
                expected_revision=frame.draft_revision,
            ),
        )
    if change == "detach":
        frame = await build_agent_frame(pg_factory, frame.fence, frame.agent_id, bundle)
        async with pg_factory.begin() as db:
            await detach_file(
                db, frame.actor, frame.draft_id, UUID(upload.file.id), frame.draft_revision
            )
    client = Business()
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await submit(pg_factory, bundle, frame, client)
    assert not client.payloads


async def test_guest_submission_requests_auth_without_submitting(pg_factory, job_app):
    from test_admission import visitor
    from test_lead_skills import ROOT
    from test_reception_flow import send_in_db

    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft
    from xiaohe.runs import acquire_run
    from xiaohe.workflows.loader import load_bundle
    from xiaohe.workflows.store import bind_run_bundle, persist_bundle

    bundle = load_bundle(ROOT)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        await persist_bundle(db, bundle)
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="guest-lead", kind="service", direction="buyer"),
        )
        await patch_draft(
            db,
            who,
            draft.id,
            draft.revision,
            {
                "companyName": "虚构访客公司",
                "contactName": "测试联系人",
                "contactPhone": "测试总台",
            },
        )
        accepted = await send_in_db(db, who, conversation, "可以")
        await bind_run_bundle(db, UUID(accepted.run.id), bundle.bundle_id)
        command = await db.scalar(
            select(Command).where(Command.message_id == UUID(accepted.message.id))
        )
        await bind_command_policy(
            db, command, skills().interaction_policy, workflow_bundle_id=bundle.bundle_id
        )
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    frame = await build_agent_frame(pg_factory, fence, "xiaohe-buyer", bundle)
    client = Business()
    result = await submit(pg_factory, bundle, frame, client)
    assert result["operation_result"]["auth"] is True
    async with pg_factory() as db:
        assert await db.scalar(select(ReceptionSubmission)) is None
    assert not client.payloads
