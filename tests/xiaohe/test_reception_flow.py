from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_admission import visitor
from test_identity_sessions import actor, bootstrap, login
from test_reception_graph import DecisionModel

from xiaohe.admission import accept_action
from xiaohe.contracts import ServiceError
from xiaohe.conversations import load_snapshot
from xiaohe.graph import build_graph
from xiaohe.models import Command, Run
from xiaohe.reception.contracts import CreateDraft, ReceptionAction
from xiaohe.reception.fields import map_member_size
from xiaohe.reception.flow import _switch, apply_reception_action, recognize_direction
from xiaohe.reception.store import create_draft, load_reception, patch_draft


@pytest.mark.parametrize(
    "text,expected",
    [
        ("三百家会员", "100—500"),
        ("会员300家", "100—500"),
        ("1500家", "500—2000"),
        ("3000家", "2000 以上"),
        ("100家", None),
        ("500家", None),
        ("2000家", None),
    ],
)
def test_member_size_maps_clear_counts_and_clarifies_endpoints(text, expected):
    assert map_member_size(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("采购商", "buyer"),
        ("我代表行业协会", "organization"),
        ("我是供应商", "supplier"),
        ("不是供应商，我是采购商", "buyer"),
        ("采购商或供应商都可以", None),
        ("不要切换到供应商", None),
        ("我是采购总监", "buyer"),
        ("我是供应商，想找采购商", "supplier"),
    ],
)
def test_negation_and_two_roles_clarify(text, expected):
    assert recognize_direction(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("从采购商切换到供应商", "supplier"),
        ("请把我的接待身份切换到采购商", "buyer"),
        ("把ERP改为供应商系统怎么集成？", None),
        ("我想改用供应商的系统", None),
        ("不要切换到供应商", None),
    ],
)
def test_switch_requires_explicit_target_identity_clause(text, expected):
    assert _switch(text) == expected


@pytest.mark.pg
@pytest.mark.asyncio
async def test_system_replacement_question_does_not_switch_but_explicit_from_to_does(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await visitor(pg_factory)
    await talk(pg_factory, runtime_env, who, conversation, "采购商")
    message, view = await talk(
        pg_factory, runtime_env, who, conversation, "把ERP改为供应商系统怎么集成？"
    )
    assert view.direction == "buyer" and "ERP" in message.model_dump_json()
    _, view = await talk(pg_factory, runtime_env, who, conversation, "从采购商切换到供应商")
    assert view.direction == "supplier"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_explicit_new_draft_api_choice_can_start_original_worker_without_prior_role_turn(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    async with pg_factory.begin() as db:
        created = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="explicit", kind="service", direction="supplier"),
        )
    message, view = await talk(pg_factory, runtime_env, who, conversation, "继续采集资料")
    assert view.direction == "supplier" and view.active_draft.id == created.id
    assert "企业名称" in message.model_dump_json()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_buyer_supplier_question_is_answered_and_natural_round_controls_work(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    await talk(pg_factory, runtime_env, who, conversation, "采购商")
    message, view = await talk(pg_factory, runtime_env, who, conversation, "怎么筛选可信供应商？")
    assert view.direction == "buyer" and "其他服务方向" not in message.model_dump_json()
    assert "寻源" in message.model_dump_json()
    message, view = await talk(pg_factory, runtime_env, who, conversation, "供应商资质怎么核实？")
    assert view.direction == "buyer" and "其他服务方向" not in message.model_dump_json()
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    assert "企业名称" in message.model_dump_json()
    assert "companyName" not in message.model_dump_json()
    draft = view.active_draft
    async with pg_factory.begin() as db:
        await patch_draft(db, who, draft.id, draft.revision, {"companyName": "甲公司"})
    for text, step in [("保存本轮", "round2"), ("继续下一轮", "round3"), ("下一轮", "summary")]:
        message, view = await talk(pg_factory, runtime_env, who, conversation, text)
        assert view.active_draft.step == step
    assert view.active_draft.state == "review"
    revision = view.active_draft.revision
    _, view = await talk(pg_factory, runtime_env, who, conversation, "下一轮")
    assert view.active_draft.revision == revision == view.active_draft.summary_version
    _, view = await talk(pg_factory, runtime_env, who, conversation, "好的")
    assert view.active_draft.state == "review"


async def install(pg_factory, runtime_env, monkeypatch, business_client=None):
    from agents.agents import agents

    graph = build_graph(
        factory=pg_factory,
        model=DecisionModel(result={"fields": {}}),
        business_client=business_client,
    )
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)


async def talk(factory, env, who, conversation, text):
    async with factory.begin() as db:
        # The persisted pending request is authoritative, not the test's chat copy.
        waiting = await db.scalar(
            select(Run).where(Run.conversation_id == conversation, Run.status == "waiting_input")
        )
        if waiting:
            accepted = await accept_action(
                db,
                who,
                conversation,
                str(uuid4()),
                waiting.pending_request["requestId"],
                None,
                text,
            )
        else:
            accepted = await send_in_db(db, who, conversation, text)
    await env.worker.execute_run(UUID(accepted.run.id))
    async with factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
        return snapshot.messages[-1], await load_reception(db, who, conversation)


async def send_in_db(db, who, conversation, text):
    from xiaohe.admission import accept_message

    return await accept_message(db, who, conversation, str(uuid4()), text)


@pytest.mark.pg
@pytest.mark.asyncio
async def test_fixed_greeting_invitation_and_defer(pg_factory, runtime_env, monkeypatch):
    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await visitor(pg_factory)
    message, view = await talk(pg_factory, runtime_env, who, conversation, "你好")
    assert "我是小鹤" in message.model_dump_json()
    assert {part.type for part in message.parts} >= {"suggestions", "input_request"}
    message, view = await talk(pg_factory, runtime_env, who, conversation, "采购商")
    assert view.direction == "buyer" and "寻源慢" in message.model_dump_json()
    message, view = await talk(pg_factory, runtime_env, who, conversation, "想看演示")
    assert view.invitation_state == "offered"
    message, view = await talk(pg_factory, runtime_env, who, conversation, "暂时不用")
    assert view.invitation_state == "deferred"
    message, view = await talk(pg_factory, runtime_env, who, conversation, "怎么收费")
    assert view.invitation_state == "deferred" and "是否愿意" not in message.model_dump_json()
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    assert any(part.type == "auth_request" for part in message.parts)
    assert not view.draft_refs


@pytest.mark.pg
@pytest.mark.asyncio
async def test_question_returns_to_round_switch_preserves_and_multiple_require_selection(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    await talk(pg_factory, runtime_env, who, conversation, "采购商")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    first = view.active_draft
    async with pg_factory.begin() as db:
        saved = await patch_draft(db, who, first.id, first.revision, {"companyName": "甲公司"})
    message, view = await talk(pg_factory, runtime_env, who, conversation, "资料安全吗？")
    assert view.active_draft.step == "round1" and view.active_draft.revision == saved.revision
    assert "安全" in message.model_dump_json()
    _, view = await talk(pg_factory, runtime_env, who, conversation, "供应商怎么用？")
    assert view.direction == "buyer" and view.active_draft.id == first.id
    _, view = await talk(pg_factory, runtime_env, who, conversation, "切换到供应商")
    assert view.direction == "supplier" and len(view.draft_refs) == 1
    async with pg_factory.begin() as db:
        await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="another", direction="buyer", kind="service"),
        )
    message, view = await talk(pg_factory, runtime_env, who, conversation, "切换到采购商")
    assert view.active_draft is None and "选择" in message.model_dump_json()


@pytest.mark.pg
@pytest.mark.asyncio
async def test_round_action_is_admitted_once_and_plain_ok_cannot_confirm(
    pg_factory, runtime_env, monkeypatch
):
    await install(pg_factory, runtime_env, monkeypatch)
    _, token = await login(pg_factory)
    initial, _, _ = await bootstrap(pg_factory, token)
    who, conversation = await actor(pg_factory, token), UUID(initial.conversation_id)
    await talk(pg_factory, runtime_env, who, conversation, "供应商")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我愿意登记需求")
    draft = view.active_draft
    async with pg_factory.begin() as db:
        draft = await patch_draft(db, who, draft.id, draft.revision, {"companyName": "甲公司"})
        request = ReceptionAction(
            client_action_id="save-one",
            request_id=view.pending_request_id,
            expected_revision=draft.revision,
            action="save_round",
        )
        first = await apply_reception_action(db, who, draft.id, request)
        assert (await apply_reception_action(db, who, draft.id, request)).run.id == first.run.id
        assert await db.scalar(select(Command).where(Command.client_key == "save-one"))
    await runtime_env.worker.execute_run(UUID(first.run.id))
    async with pg_factory.begin() as db:
        view = await load_reception(db, who, conversation)
        assert view.active_draft.step == "round2"
        with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
            await apply_reception_action(
                db,
                who,
                draft.id,
                ReceptionAction(
                    client_action_id="old",
                    request_id=view.pending_request_id,
                    expected_revision=1,
                    action="confirm",
                    summary_version=1,
                ),
            )
    _, view = await talk(pg_factory, runtime_env, who, conversation, "好的")
    assert view.active_draft.state == "collecting"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_late_extraction_cannot_overwrite_new_revision(pg_factory, job_app):
    import asyncio

    from langchain_core.runnables import RunnableLambda

    from xiaohe.models import Conversation
    from xiaohe.reception.flow import run_reception_step
    from xiaohe.runs import acquire_run

    started, release = asyncio.Event(), asyncio.Event()

    class DelayedModel:
        def with_structured_output(self, schema):
            async def answer(messages, **kwargs):
                started.set()
                await release.wait()
                return {"fields": {"companyName": {"value": "甲公司", "evidence": "甲公司"}}}

            return RunnableLambda(answer)

    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction="buyer", kind="service"),
        )
        (await db.get(Conversation, conversation)).direction = "buyer"
        accepted = await send_in_db(db, who, conversation, "甲公司")
    async with pg_factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    task = asyncio.create_task(run_reception_step(pg_factory, fence, DelayedModel()))
    await asyncio.wait_for(started.wait(), 10)
    async with pg_factory.begin() as db:
        await patch_draft(db, who, draft.id, 1, {"companyName": "新资料公司"})
    release.set()
    with pytest.raises(ServiceError, match="DRAFT_CONFLICT"):
        await task
    async with pg_factory.begin() as db:
        assert (await load_reception(db, who, conversation)).active_draft.fields[
            "companyName"
        ] == "新资料公司"
