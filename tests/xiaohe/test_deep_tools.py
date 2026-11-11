import importlib
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from test_agent_operations import prepare
from test_lead_submission import Business

from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime.bundles import load_runtime_bundle, persist_runtime_bundle, select_binding
from xiaohe.deep_runtime.models import DeepExecution
from xiaohe.models import Command, Conversation, Run
from xiaohe.reception.models import ReceptionDraft

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"
pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def tools_module():
    assert importlib.util.find_spec("xiaohe.deep_runtime.tools") is not None, (
        "native business tools missing"
    )
    return importlib.import_module("xiaohe.deep_runtime.tools")


async def tool_context(factory, tmp_path, *, text="好的", role="buyer", client=None):
    module = tools_module()
    _, frame = await prepare(factory, tmp_path, text=text, direction=role)
    bundle = load_runtime_bundle(ROOT)
    async with factory.begin() as db:
        await persist_runtime_bundle(db, bundle)
        cmd = await db.get(Command, frame.command_id)
        cmd.payload = cmd.payload | {
            "deepExecution": DeepExecution(
                bundle_id=bundle.bundle_id, active=frame.agent_id
            ).model_dump(mode="json")
        }
        cmd.interaction_policy_id = bundle.interaction_policy.policy_id
    context = module.ToolContext(
        factory=factory,
        actor=frame.actor,
        fence=frame.fence,
        command_id=frame.command_id,
        conversation_id=frame.conversation_id,
        binding=select_binding(bundle, frame.actor, frame.agent_id),
        bundle=bundle,
        text=text,
        business_client=client,
    )
    return context, frame


async def test_tool_arguments_do_not_accept_authority(pg_factory, job_app, tmp_path):
    context, _ = await tool_context(pg_factory, tmp_path)
    tools = tools_module().make_business_tools(context)
    assert {tool.name for tool in tools} == set(context.binding.tools)
    for tool in tools:
        assert not {"actor", "user_id", "fence", "command_id", "bundle_id"} & set(tool.args)


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_semantic_submission_uses_true_receipt(pg_factory, job_app, tmp_path, role):
    client = Business()
    context, frame = await tool_context(pg_factory, tmp_path, role=role, client=client)
    result = await tools_module().execute_effect(
        context,
        "submit_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
    )
    assert result.status == "ok" and result.data["receipt"]["status"] == "accepted"
    assert len(client.payloads) == 1
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, frame.draft_id)).state == "accepted"


async def test_same_turn_modify_and_submit_uses_final_revision(pg_factory, job_app, tmp_path):
    client = Business()
    context, frame = await tool_context(
        pg_factory, tmp_path, text="联系人改为李四，可以提交", client=client
    )
    result = await tools_module().execute_effect(
        context,
        "submit_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={
            "candidates": {"contactName": {"value": "李四", "evidence": "李四"}},
            "confirmation": {"confirmed": True, "evidence": "可以提交"},
        },
    )
    assert result.revision == frame.draft_revision + 1
    assert client.payloads[0]["requestFields"]["contactName"] == "李四"


@pytest.mark.parametrize(
    "payload",
    [
        {"confirmation": {"confirmed": False, "evidence": "好的"}},
        {"confirmation": {"confirmed": True, "evidence": "好的", "conditional": True}},
        {"confirmation": {"confirmed": True, "evidence": "来自子任务的同意"}},
        {"candidates": {"city": {"value": "苏州", "evidence": "不存在的原文"}}},
    ],
)
async def test_unsupported_confirmation_or_field_evidence_cannot_write(
    pg_factory, job_app, tmp_path, payload
):
    client = Business()
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    operation = "submit_lead" if "confirmation" in payload else "save_lead"
    with pytest.raises(ServiceError):
        await tools_module().execute_effect(
            context,
            operation,
            draft_id=frame.draft_id,
            expected_revision=frame.draft_revision,
            payload=payload,
        )
    assert client.payloads == []
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.revision == frame.draft_revision


async def test_cancelled_fence_cannot_save(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州")
    async with pg_factory.begin() as db:
        run = await db.get(Run, frame.fence.run_id)
        run.status, run.holds_slot = "cancelled", False
    with pytest.raises(ServiceError):
        await tools_module().execute_effect(
            context,
            "save_lead",
            draft_id=frame.draft_id,
            expected_revision=frame.draft_revision,
            payload={"candidates": {"city": {"value": "苏州", "evidence": "苏州"}}},
        )


async def test_role_mismatch_and_stale_revision_are_rejected(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州")
    changed = replace(
        context, binding=select_binding(context.bundle, context.actor, "xiaohe-supplier")
    )
    for ctx, revision in [(changed, frame.draft_revision), (context, frame.draft_revision - 1)]:
        with pytest.raises(ServiceError):
            await tools_module().execute_effect(
                ctx,
                "save_lead",
                draft_id=frame.draft_id,
                expected_revision=revision,
                payload={"candidates": {"city": {"value": "苏州", "evidence": "苏州"}}},
            )


async def test_guest_prepare_requests_auth_without_creating_draft(pg_factory, job_app):
    module = tools_module()
    from test_admission import visitor
    from test_reception_flow import send_in_db

    from xiaohe.runs import acquire_run

    who, conversation_id = await visitor(pg_factory)
    bundle = load_runtime_bundle(ROOT)
    async with pg_factory.begin() as db:
        await persist_runtime_bundle(db, bundle)
        accepted = await send_in_db(db, who, conversation_id, "我要登记采购需求")
        command = await db.scalar(
            select(Command).where(Command.message_id == UUID(accepted.message.id))
        )
        command.payload = command.payload | {
            "deepExecution": DeepExecution(
                bundle_id=bundle.bundle_id, active="xiaohe-buyer"
            ).model_dump(mode="json")
        }
        command.interaction_policy_id = bundle.interaction_policy.policy_id
        (await db.get(Conversation, conversation_id)).direction = "buyer"
        command_id = command.id
        fence = await acquire_run(db, UUID(accepted.run.id))
    context = module.ToolContext(
        factory=pg_factory,
        actor=who,
        fence=fence,
        command_id=command_id,
        conversation_id=conversation_id,
        binding=select_binding(bundle, who, "xiaohe-buyer"),
        bundle=bundle,
        text="我要登记采购需求",
    )
    result = await module.execute_effect(
        context, "prepare_lead", draft_id=None, expected_revision=None, payload={}
    )
    assert result.status == "auth" and result.data["auth"] is True


async def test_progress_reads_business_without_active_draft(pg_factory, job_app, tmp_path):
    from xiaohe.reception.contracts import ProgressPage
    from xiaohe.reception.store import locked_flow

    class Client:
        calls = 0

        async def progress(self, actor, query):
            self.calls += 1
            assert actor.ai_user_id
            return ProgressPage(groups=[], next_cursor=None)

    client = Client()
    context, _ = await tool_context(pg_factory, tmp_path, client=client)
    async with pg_factory.begin() as db:
        flow = await locked_flow(db, await db.get(Conversation, context.conversation_id))
        flow.active_draft_id = None
    for _ in range(2):
        result = await tools_module().execute_effect(
            context, "query_progress", draft_id=None, expected_revision=None, payload={}
        )
        assert result.data["progress"]["groups"] == []
    assert client.calls == 2


async def test_prepare_new_uses_pinned_policy(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path)
    result = await tools_module().execute_effect(
        context, "prepare_lead", draft_id=None, expected_revision=None, payload={"mode": "new"}
    )
    assert result.data["draft_id"] != str(frame.draft_id)
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, UUID(result.data["draft_id"]))
        assert row.field_policy_id == context.bundle.field_policies["buyer"].policy_id


async def test_prepare_edit_keeps_formal_source(pg_factory, job_app, tmp_path):
    from xiaohe.reception.contracts import ApplicationView

    class Client(Business):
        async def application(self, actor, identifier):
            assert identifier == "fake-application" and actor.ai_user_id
            return ApplicationView(
                id=identifier,
                subject_id="fake-subject",
                direction="buyer",
                revision=3,
                public_fields={"companyName": "虚构甲公司"},
                request_fields={"contactName": "原联系人", "contactPhone": "原电话"},
                files=[],
            )

    client = Client()
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    await tools_module().execute_effect(
        context,
        "submit_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
    )
    result = await tools_module().execute_effect(
        context, "prepare_lead", draft_id=None, expected_revision=None, payload={"mode": "edit"}
    )
    async with pg_factory() as db:
        row = await db.get(ReceptionDraft, UUID(result.data["draft_id"]))
        assert row.source_application_id == "fake-application" and row.source_revision == 3
        assert row.fields["contactName"] == "原联系人"
    repeated = await tools_module().execute_effect(
        context, "prepare_lead", draft_id=None, expected_revision=None, payload={"mode": "edit"}
    )
    assert repeated == result


async def test_prepare_ambiguous_drafts_require_choice(pg_factory, job_app, tmp_path):
    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft

    context, frame = await tool_context(pg_factory, tmp_path, text="继续虚构甲公司")
    async with pg_factory.begin() as db:
        await create_draft(
            db,
            context.actor,
            context.conversation_id,
            CreateDraft(client_draft_id="two", direction="buyer", kind="service"),
        )
    result = await tools_module().execute_effect(
        context, "prepare_lead", draft_id=None, expected_revision=None, payload={}
    )
    assert result.status == "choice" and len(result.data["drafts"]) == 2
    result = await tools_module().execute_effect(
        context,
        "prepare_lead",
        draft_id=None,
        expected_revision=None,
        payload={"draft_name": "虚构甲公司"},
    )
    assert result.data["draft_id"] == str(frame.draft_id)


@pytest.mark.parametrize("shown", [True, False])
async def test_button_requires_actual_current_summary(pg_factory, job_app, tmp_path, shown):
    client = Business()
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    async with pg_factory.begin() as db:
        cmd = await db.get(Command, frame.command_id)
        cmd.payload = cmd.payload | {
            "reception": {
                "action": "confirm",
                "draftId": str(frame.draft_id),
                "expectedRevision": frame.draft_revision,
                "summaryVersion": frame.draft_revision,
            }
        }
        if not shown:
            (await db.get(ReceptionDraft, frame.draft_id)).summary_version = None

    async def submit():
        return await tools_module().execute_effect(
            context,
            "submit_lead",
            draft_id=frame.draft_id,
            expected_revision=frame.draft_revision,
            payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
        )

    if shown:
        assert (await submit()).status == "ok"
    else:
        with pytest.raises(ServiceError):
            await submit()
        assert client.payloads == []
