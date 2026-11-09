from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_agent_semantics import ScriptedModel
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import send_in_db
from workflow_support import write_test_bundle

from xiaohe.agents.contracts import ConfirmationEvidence, SemanticDecision
from xiaohe.agents.nodes import build_agent_frame, make_node_executors
from xiaohe.contracts import ServiceError
from xiaohe.models import Command
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.reception.submission import confirm_locked_draft
from xiaohe.runs import acquire_run
from xiaohe.workflows.loader import load_bundle
from xiaohe.workflows.models import AnswerNode, OperationNode
from xiaohe.workflows.store import bind_run_bundle, persist_bundle

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def prepare(
    factory, tmp_path, *, text="就按这份资料提交吧", host="portal", direction="buyer"
):
    bundle = load_bundle(write_test_bundle(tmp_path / "resources"))
    if host == "portal":
        _, token = await login(factory)
        options = {}
    else:
        from test_identity_sessions import completion, system

        from xiaohe.identity import create_user_session

        async with factory.begin() as db:
            _, secret = await create_user_session(db, system(host), completion(host))
        token = secret.get_secret_value()
        options = {"system": host, "origin": system(host).allowed_origin}
    initial, _, _ = await bootstrap(factory, token, **options)
    who, conversation = await actor(factory, token, **options), UUID(initial.conversation_id)
    async with factory.begin() as db:
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction=direction, kind="service"),
        )
        fields = (
            {
                "organizationName": "虚构协会",
                "memberSize": "100—500",
                "coverage": "本市",
                "onlineChannels": ["暂无"],
            }
            if direction == "organization"
            else {"companyName": "虚构甲公司"}
        )
        view = await patch_draft(
            db,
            who,
            view.id,
            1,
            fields | {"contactName": "测试联系人", "contactPhone": "测试联系电话"},
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
        accepted = await send_in_db(db, who, conversation, text)
        await persist_bundle(db, bundle)
        await bind_run_bundle(db, UUID(accepted.run.id), bundle.bundle_id)
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    frame = await build_agent_frame(factory, fence, "xiaohe-" + direction, bundle)
    return bundle, frame


async def test_answer_failure_does_not_apply_pending_city(pg_factory, job_app, tmp_path):
    bundle, frame = await prepare(pg_factory, tmp_path, text="我司在苏州，怎么计费")
    decision = SemanticDecision.model_validate(
        {
            "action": "consultation_with_details",
            "candidates": {"city": {"value": "苏州", "evidence": "我司在苏州"}},
        }
    )
    await cache_decision(pg_factory, frame, decision)
    model = ScriptedModel(result={}, responses={"AnswerDraft": {"invalid": True}})
    nodes = make_node_executors(pg_factory, model=model, business_client=None, bundle=bundle)
    state = {"frame": frame.model_dump(), "decision": decision.model_dump()}
    answer = AnswerNode(id="answer", name="答疑", type="answer", prompt="answer", next="collect")
    collect = OperationNode(
        id="collect", name="保存", type="operation", operation="collect_details", next="done"
    )
    with pytest.raises(ServiceError):
        await nodes["answer"](answer, state, {})
    with pytest.raises(ServiceError):
        await nodes["operation"](collect, state, {})
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.revision == frame.draft_revision and "city" not in row.fields
    model.responses["AnswerDraft"] = {
        "text": "费用需正式政策确认。",
        "basis": "needs_confirmation",
        "source_ids": [],
    }
    state |= await nodes["answer"](answer, state, {})
    await nodes["operation"](collect, state, {})
    await nodes["operation"](collect, state, {})
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.revision == frame.draft_revision + 1 and row.fields["city"] == "苏州"


async def test_operation_rejects_unrelated_valid_semantic_decision(pg_factory, job_app, tmp_path):
    bundle, frame = await prepare(pg_factory, tmp_path)
    decision = SemanticDecision(action="consultation")
    await cache_decision(pg_factory, frame, decision)
    nodes = make_node_executors(pg_factory, model=None, business_client=None, bundle=bundle)
    node = OperationNode(
        id="new", name="新建", type="operation", operation="new_draft", next="done"
    )
    with pytest.raises(ServiceError):
        await nodes["operation"](
            node, {"frame": frame.model_dump(), "decision": decision.model_dump()}, {}
        )


async def cache_decision(factory, frame, decision):
    async with factory.begin() as db:
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {
            "agentStages": {
                frame.agent_id: {
                    "frame": frame.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                }
            }
        }


@pytest.mark.parametrize("direction", ["buyer", "supplier", "organization"])
async def test_accepted_details_create_edit_draft_and_keep_original_receipt(
    pg_factory, job_app, tmp_path, direction
):
    from xiaohe.reception.contracts import ApplicationView
    from xiaohe.reception.store import draft_view

    bundle, frame = await prepare(
        pg_factory, tmp_path, text="我要更新联系电话为 +44 20 1234", direction=direction
    )
    async with pg_factory.begin() as db:
        original = await db.get(ReceptionDraft, frame.draft_id)
        original.state = "accepted"
        db.add(
            ReceptionSubmission(
                draft_id=original.id,
                conversation_id=original.conversation_id,
                system_id=original.system_id,
                tenant_id=original.tenant_id,
                draft_revision=original.revision,
                summary_version=original.revision,
                confirmation_message_id=frame.message_id,
                submission_key="original-receipt",
                payload={},
                status="accepted",
                receipt={
                    "submissionKey": "original-receipt",
                    "kind": "service",
                    "applicationId": "formal-original",
                    "revision": 1,
                    "status": "accepted",
                },
            )
        )
    decision = SemanticDecision.model_validate(
        {
            "action": "provide_details",
            "candidates": {"contactPhone": {"value": "+44 20 1234", "evidence": "+44 20 1234"}},
        }
    )
    await cache_decision(pg_factory, frame, decision)

    class FormalSource:
        async def application(self, actor, application_id):
            assert application_id == "formal-original"
            public = (
                {
                    "organizationName": "后台最新协会",
                    "memberSize": "100—500",
                    "coverage": "本市",
                    "onlineChannels": ["暂无"],
                }
                if direction == "organization"
                else {"companyName": "后台最新公司"}
            )
            return ApplicationView(
                id=application_id,
                subject_id="same-company",
                direction=direction,
                revision=6,
                public_fields=public,
                request_fields={"contactName": "最新联系人", "contactPhone": "旧电话"},
                files=[],
            )

    nodes = make_node_executors(
        pg_factory, model=None, business_client=FormalSource(), bundle=bundle
    )
    node = OperationNode(
        id="collect", name="更新", type="operation", operation="collect_details", next="result"
    )
    state = {"frame": frame.model_dump(), "decision": decision.model_dump()}
    state |= await nodes["operation"](node, state, {})
    from xiaohe.workflows.models import ResultNode

    await nodes["result"](
        ResultNode(id="result", name="返回", type="result", wait_for_input=True), state, {}
    )
    # Command replay must reuse its edit draft and original source revision.
    await nodes["operation"](node, state, {})
    await nodes["result"](
        ResultNode(id="result", name="返回", type="result", wait_for_input=True), state, {}
    )
    async with pg_factory() as db:
        original = await db.get(ReceptionDraft, frame.draft_id)
        assert original.state == "accepted" and original.fields["contactPhone"] == "测试联系电话"
        assert (await draft_view(db, original)).accepted_application_id == "formal-original"
        from xiaohe.reception.models import ReceptionFlow

        flow = await db.get(ReceptionFlow, original.conversation_id)
        updated = await db.get(ReceptionDraft, flow.active_draft_id)
        assert updated.id != original.id
        assert updated.source_application_id == "formal-original" and updated.source_revision == 6
        assert updated.fields["contactPhone"] == "+44 20 1234"
        assert updated.fields["contactName"] == "最新联系人"
        assert updated.fields[
            "organizationName" if direction == "organization" else "companyName"
        ] == ("后台最新协会" if direction == "organization" else "后台最新公司")
        assert updated.state == "collecting" and updated.summary_version is None
        assert len((await db.scalars(select(ReceptionDraft))).all()) == 2


async def test_accepted_chat_edit_does_not_overwrite_another_page_save(
    pg_factory, job_app, tmp_path, monkeypatch
):
    from xiaohe.agents import nodes as module

    _, frame = await prepare(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        original = await db.get(ReceptionDraft, frame.draft_id)
        original.state = "accepted"
        target = ReceptionDraft(
            conversation_id=original.conversation_id,
            system_id=original.system_id,
            tenant_id=original.tenant_id,
            client_draft_id="existing-edit",
            kind="service",
            direction="buyer",
            source_application_id="formal-original",
            source_revision=1,
            definition_bundle_id=original.definition_bundle_id,
            fields=dict(original.fields),
        )
        db.add(target)
        await db.flush()
        target_id = target.id
    checked, calls = module._checked, 0

    async def race(db, current_frame):
        nonlocal calls
        calls += 1
        if calls == 3:
            async with pg_factory.begin() as other:
                row = await other.get(ReceptionDraft, target_id)
                row.fields = row.fields | {"contactPhone": "另一页面刚保存的电话"}
                row.revision += 1
        return await checked(db, current_frame)

    monkeypatch.setattr(module, "_checked", race)
    decision = SemanticDecision.model_validate(
        {
            "action": "provide_details",
            "candidates": {
                "contactPhone": {"value": "本轮聊天的旧电话", "evidence": "本轮聊天的旧电话"}
            },
        }
    )
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await module._external_operation(
            pg_factory,
            frame,
            decision,
            {"external": "edit_application", "application_id": "formal-original"},
            None,
        )
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, target_id)).fields[
            "contactPhone"
        ] == "另一页面刚保存的电话"


@pytest.mark.parametrize(
    "problem", ["missing_evidence", "not_shown", "changed", "message", "actor", "missing_fields"]
)
async def test_semantic_confirmation_requires_current_displayed_revision(
    pg_factory, job_app, tmp_path, problem
):
    _, frame = await prepare(pg_factory, tmp_path)
    decision = SemanticDecision(action="confirm_current_summary")
    await cache_decision(pg_factory, frame, decision)
    evidence = ConfirmationEvidence.from_frame(frame, source="semantic")
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        if problem == "not_shown":
            row.state, row.summary_version = "collecting", None
        elif problem == "changed":
            row.revision += 1
        elif problem == "message":
            evidence = evidence.model_copy(update={"message_id": uuid4()})
        elif problem == "actor":
            evidence = evidence.model_copy(update={"user_id": "forged"})
        elif problem == "missing_fields":
            row.fields = {}
        with pytest.raises(ServiceError):
            await confirm_locked_draft(
                db,
                row,
                frame.draft_revision,
                frame.summary_version,
                frame.message_id,
                frame.actor.user_id,
                evidence=None if problem == "missing_evidence" else evidence,
            )
        assert await db.scalar(select(ReceptionSubmission)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "unclear"},
        {"negated": True},
        {"conditional": True},
        {"candidates": {"companyName": {"value": "新名字", "evidence": "新名字"}}},
    ],
)
async def test_plain_ok_negation_condition_and_edit_do_not_submit(
    pg_factory, job_app, tmp_path, changes
):
    bundle, frame = await prepare(pg_factory, tmp_path)
    decision = SemanticDecision.model_validate({"action": "confirm_current_summary"} | changes)
    await cache_decision(pg_factory, frame, decision)
    nodes = make_node_executors(pg_factory, model=None, business_client=None, bundle=bundle)
    node = OperationNode(
        id="confirm", name="提交", type="operation", operation="confirm_submission", next="done"
    )
    with pytest.raises(ServiceError):
        await nodes["operation"](
            node, {"frame": frame.model_dump(), "decision": decision.model_dump()}, {}
        )
    async with pg_factory() as db:
        assert await db.scalar(select(ReceptionSubmission)) is None


async def test_semantic_and_button_confirmation_share_one_submission_key(
    pg_factory, job_app, tmp_path
):
    _, frame = await prepare(pg_factory, tmp_path)
    await cache_decision(pg_factory, frame, SemanticDecision(action="confirm_current_summary"))
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        first = await confirm_locked_draft(
            db,
            row,
            frame.draft_revision,
            frame.summary_version,
            frame.message_id,
            frame.actor.user_id,
            evidence=ConfirmationEvidence.from_frame(frame, source="semantic"),
        )
        command = await db.get(Command, frame.command_id)
        command.payload = command.payload | {
            "reception": {
                "action": "confirm",
                "draftId": str(row.id),
                "expectedRevision": row.revision,
                "summaryVersion": row.summary_version,
            }
        }
        again = await confirm_locked_draft(
            db,
            row,
            frame.draft_revision,
            frame.summary_version,
            frame.message_id,
            frame.actor.user_id,
            evidence=ConfirmationEvidence.from_frame(frame, source="button"),
        )
        assert first.submission_key == again.submission_key
