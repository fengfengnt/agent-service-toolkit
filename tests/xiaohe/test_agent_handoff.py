import json
from uuid import UUID

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select
from test_admission import visitor
from test_reception_flow import install, talk
from test_reception_graph import DecisionModel

from xiaohe.agents.runtime import load_agent_registry
from xiaohe.models import Command, Run
from xiaohe.workflows.definitions import RESOURCE_ROOT

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_ambiguous_expression_gets_contextual_clarification_without_invitation(
    pg_factory, runtime_env, monkeypatch
):
    from xiaohe.models import Conversation

    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "unclear", "needs_clarification": True}]
    )
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = "buyer"
    _, view = await talk(pg_factory, runtime_env, who, conversation, "这个具体怎么弄？")
    assert len(model.requests) == 2
    assert view.direction == "buyer" and view.invitation_state == "none" and not view.draft_refs


class AgentSequenceModel(DecisionModel):
    decisions: list = []
    agent_calls: list = []

    def with_structured_output(self, schema, **kwargs):
        async def respond(messages):
            self.requests.append(messages)
            if schema.__name__ == "SemanticDecision":
                self.agent_calls.append(str(messages[0].content).splitlines()[0])
                return self.decisions.pop(0)
            return {
                "text": "可先梳理范围与需求，费用需依据正式政策确认。",
                "basis": "needs_confirmation",
                "source_ids": [],
            }

        return RunnableLambda(respond)


async def install_agents(
    factory, env, monkeypatch, decisions, root=RESOURCE_ROOT, business_client=None
):
    model = AgentSequenceModel(result={}, decisions=decisions)
    registry = await load_agent_registry(factory, root, model, business_client)
    await install(factory, env, monkeypatch, model=model, business_client=business_client)
    return model, registry


async def test_reception_hands_off_once_and_specialist_continues(
    pg_factory, runtime_env, monkeypatch
):
    model, registry = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "consultation", "target_direction": "buyer"},
            {"action": "consultation", "topics": ["buyer.rfx"]},
            {"action": "consultation", "topics": ["buyer.rfx"]},
        ],
    )
    who, conversation = await visitor(pg_factory)
    _, view = await talk(pg_factory, runtime_env, who, conversation, "想了解采购如何计费")
    assert view.direction == "buyer"
    await talk(pg_factory, runtime_env, who, conversation, "收费方式呢")
    assert len(registry) == 4
    assert sum("小鹤接待" in value for value in model.agent_calls) == 1
    assert sum("采购 Agent" in value for value in model.agent_calls) == 2
    async with pg_factory() as db:
        run = await db.scalar(select(Run).where(Run.conversation_id == conversation))
        assert run.runtime_version == "dsl-v1"
        stages = (
            await db.scalar(
                select(Command).where(Command.run_id == run.id).order_by(Command.created_at)
            )
        ).payload["agentStages"]
        assert set(stages) == {"xiaohe-reception", "xiaohe-buyer"}
        buyer_payload = json.dumps(
            stages["xiaohe-buyer"]["frame"]["model_context"], ensure_ascii=False
        )
        assert str(who.session_id) not in buyer_payload


async def test_switch_and_return_restore_direction_drafts(pg_factory, runtime_env, monkeypatch):
    from test_identity_sessions import actor, bootstrap, login

    from xiaohe.reception.contracts import CreateDraft
    from xiaohe.reception.store import create_draft, patch_draft

    model, _ = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "switch_direction", "target_direction": "supplier", "explicit_switch": True},
            {"action": "consultation", "topics": ["supplier.capability"]},
            {"action": "switch_direction", "target_direction": "buyer", "explicit_switch": True},
            {"action": "consultation", "topics": ["buyer.requirements"]},
        ],
    )
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    async with pg_factory.begin() as db:
        supplier = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="supplier", direction="supplier", kind="service"),
        )
        await patch_draft(
            db,
            who,
            supplier.id,
            supplier.revision,
            {"companyName": "虚构供方", "productDescription": "其他方向整份草稿私有哨兵"},
        )
        buyer = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="buyer", direction="buyer", kind="service"),
        )
        await patch_draft(db, who, buyer.id, buyer.revision, {"companyName": "虚构买方"})
    _, view = await talk(pg_factory, runtime_env, who, conversation, "换到供应商服务")
    assert view.active_draft.id == supplier.id
    _, view = await talk(pg_factory, runtime_env, who, conversation, "切回采购方向")
    assert (
        view.active_draft.id == buyer.id and view.active_draft.fields["companyName"] == "虚构买方"
    )
    buyer_calls = [request for request in model.requests if "采购 Agent" in str(request[0].content)]
    assert "其他方向整份草稿私有哨兵" not in str(buyer_calls)


async def test_target_model_failure_does_not_change_conversation_direction(
    pg_factory, runtime_env, monkeypatch
):
    from xiaohe.contracts import ServiceError
    from xiaohe.reception.store import load_reception

    _, _ = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [{"action": "consultation", "target_direction": "buyer"}, {"illegal_result": True}],
    )
    who, conversation = await visitor(pg_factory)
    with pytest.raises(ServiceError):
        await talk(pg_factory, runtime_env, who, conversation, "采购如何收费")
    async with pg_factory.begin() as db:
        view = await load_reception(db, who, conversation)
    assert view.direction is None and not view.draft_refs


async def test_pending_submission_query_reconciles_without_reconfirmation(
    pg_factory, runtime_env, monkeypatch
):
    from test_identity_sessions import actor, bootstrap, login

    from xiaohe.contracts import ServiceError
    from xiaohe.reception.contracts import CreateDraft, SubmissionResult, SubmitReceipt
    from xiaohe.reception.models import ReceptionDraft
    from xiaohe.reception.store import create_draft, patch_draft

    class LostReply:
        receipt = None
        creates = 0

        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted" if self.receipt else "not_found", receipt=self.receipt
            )

        async def submit(self, actor, payload):
            self.creates += 1
            self.receipt = SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="original",
                revision=1,
                status="accepted",
            )
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)

    backend = LostReply()
    await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [{"action": "confirm_current_summary"}, {"action": "query_progress"}],
        business_client=backend,
    )
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    async with pg_factory.begin() as db:
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="lost", kind="service", direction="buyer"),
        )
        view = await patch_draft(
            db,
            who,
            view.id,
            view.revision,
            {"companyName": "虚构甲公司", "contactName": "测试联系人", "contactPhone": "总台分机"},
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
    message, view = await talk(pg_factory, runtime_env, who, conversation, "就按这份资料提交吧")
    assert view.active_draft.state == "submitting" and "尚未确认" in message.model_dump_json()
    message, view = await talk(pg_factory, runtime_env, who, conversation, "查询原提交的结果")
    assert view.active_draft.state == "accepted" and "已核实" in message.model_dump_json()
    assert backend.creates == 1
