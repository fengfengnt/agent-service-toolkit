from shutil import copytree
from uuid import UUID

import pytest
import yaml
from langchain_core.runnables import RunnableLambda
from sqlalchemy import func, select
from test_admission import visitor
from test_agent_handoff import install_agents
from test_identity_sessions import actor, bootstrap, login
from test_reception_flow import install, talk

from xiaohe.contracts import ServiceError
from xiaohe.conversations import load_snapshot
from xiaohe.models import Conversation
from xiaohe.reception.contracts import CreateDraft, ReceptionControl
from xiaohe.reception.flow import apply_reception_control
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import create_draft, load_reception, locked_flow, patch_draft
from xiaohe.workflows.definitions import RESOURCE_ROOT

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def member(factory, direction):
    _, token = await login(factory)
    initial, _, _ = await bootstrap(factory, token)
    who, conversation = await actor(factory, token), UUID(initial.conversation_id)
    async with factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = direction
    return who, conversation


def assert_collection(message, view, direction, *, authenticated=True):
    text = message.model_dump_json()
    assert "是否愿意登录并登记" not in text
    assert view.invitation_state == "accepted"
    if authenticated:
        assert not any(part.type == "auth_request" for part in message.parts)
        assert view.active_draft is not None
        assert view.active_draft.direction == direction
        name = "organizationName" if direction == "organization" else "companyName"
        assert name in view.active_draft.field_layout.required_fields
        assert view.active_draft.state == "collecting"
        assert any(
            part.type == "system_notice" and part.text == "开始填写需求资料"
            for part in message.parts
        )
    else:
        assert any(part.type == "auth_request" for part in message.parts)
        assert view.active_draft is None


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_member_conversion_answers_then_opens_role_collection(
    pg_factory, runtime_env, monkeypatch, direction
):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "accept_invitation"}]
    )
    who, conversation = await member(pg_factory, direction)
    message, view = await talk(pg_factory, runtime_env, who, conversation, "想合作，怎么开始？")
    assert_collection(message, view, direction)
    assert "费用需依据正式政策确认" in message.model_dump_json()
    assert len(model.requests) == 2  # 先理解，再答疑；不是空白建表。
    draft = view.active_draft
    async with pg_factory.begin() as db:
        await patch_draft(db, who, draft.id, draft.revision, {"city": "苏州"})
    model.decisions.append({"action": "accept_invitation"})
    message, again = await talk(pg_factory, runtime_env, who, conversation, "我还是想合作")
    assert_collection(message, again, direction)
    assert again.active_draft.id == draft.id
    assert again.active_draft.fields["city"] == "苏州"
    async with pg_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReceptionDraft)) == 1


@pytest.mark.parametrize("text", ["寻源慢", "难比价", "想看效果"])
@pytest.mark.parametrize("authenticated", [True, False])
async def test_buyer_suggested_problem_is_conversion(
    pg_factory, runtime_env, monkeypatch, text, authenticated
):
    # 精确建议入口不依赖模型再次判断意向，答疑仍使用正常模型路径。
    model, _ = await install_agents(pg_factory, runtime_env, monkeypatch, [])
    if authenticated:
        who, conversation = await member(pg_factory, "buyer")
    else:
        who, conversation = await visitor(pg_factory)
        async with pg_factory.begin() as db:
            (await db.get(Conversation, conversation)).direction = "buyer"
    message, view = await talk(pg_factory, runtime_env, who, conversation, text)
    assert_collection(message, view, "buyer", authenticated=authenticated)
    assert "费用需依据正式政策确认" in message.model_dump_json()
    assert not model.agent_calls


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_pure_consultation_does_not_request_member_login_or_create_draft(
    pg_factory, runtime_env, monkeypatch, direction
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [{"action": "consultation"}])
    who, conversation = await member(pg_factory, direction)
    message, view = await talk(
        pg_factory, runtime_env, who, conversation, "只是了解一下原理，先不登记"
    )
    assert "是否愿意登录" not in message.model_dump_json()
    assert not any(part.type == "auth_request" for part in message.parts)
    assert not view.draft_refs


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_negated_conversion_never_creates_collection(
    pg_factory, runtime_env, monkeypatch, direction
):
    await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [{"action": "accept_invitation", "negated": True}],
    )
    who, conversation = await member(pg_factory, direction)
    message, view = await talk(pg_factory, runtime_env, who, conversation, "暂不合作，先不用登记")
    assert not view.draft_refs
    assert not any(part.type == "auth_request" for part in message.parts)


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
@pytest.mark.parametrize("count", [1, 2])
async def test_existing_role_drafts_are_reused_or_selected_without_creating_another(
    pg_factory, runtime_env, monkeypatch, direction, count
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [{"action": "accept_invitation"}])
    who, conversation = await member(pg_factory, direction)
    async with pg_factory.begin() as db:
        drafts = [
            await create_draft(
                db,
                who,
                conversation,
                CreateDraft(
                    client_draft_id="existing-" + str(index), direction=direction, kind="service"
                ),
            )
            for index in range(count)
        ]
        (await locked_flow(db, await db.get(Conversation, conversation))).active_draft_id = None
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    assert len(view.draft_refs) == count
    if count == 1:
        assert_collection(message, view, direction)
        assert view.active_draft.id == drafts[0].id
    else:
        assert view.active_draft is None
        assert "选择" in message.model_dump_json()
        assert not any(part.type == "system_notice" for part in message.parts)


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_persisted_old_auth_route_also_answers_before_collection(
    pg_factory, runtime_env, monkeypatch, tmp_path, direction
):
    root = tmp_path / "old-resources"
    copytree(RESOURCE_ROOT, root)
    path = root / "workflows" / (direction + ".yaml")
    definition = yaml.safe_load(path.read_text("utf-8"))
    definition["workflow_version"] = "2"
    definition["nodes"] = [node for node in definition["nodes"] if node["id"] != "answer_auth"]
    next(node for node in definition["nodes"] if node["type"] == "decide")["routes"][
        "accept_invitation"
    ] = "auth"
    path.write_text(yaml.safe_dump(definition, allow_unicode=True), "utf-8")
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "accept_invitation"}], root=root
    )
    who, conversation = await member(pg_factory, direction)
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    assert_collection(message, view, direction)
    assert "费用需依据正式政策确认" in message.model_dump_json()
    assert len(model.requests) == 2


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_authenticated_collection_control_does_not_call_model(
    pg_factory, runtime_env, monkeypatch, direction
):
    model, _ = await install_agents(pg_factory, runtime_env, monkeypatch, [])
    who, conversation = await member(pg_factory, direction)
    async with pg_factory.begin() as db:
        flow = await locked_flow(db, await db.get(Conversation, conversation))
        flow.invitation_state = "accepted"
        accepted = await apply_reception_control(
            db,
            who,
            conversation,
            ReceptionControl(
                client_action_id="continue-authenticated-lead", action="start_collection"
            ),
        )
    await runtime_env.worker.execute_run(UUID(accepted.run.id))
    async with pg_factory() as db:
        snapshot = await load_snapshot(db, who, conversation)
        view = await load_reception(db, who, conversation)
    assert_collection(snapshot.messages[-1], view, direction)
    assert not model.requests


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_guest_conversion_answers_before_authentication(
    pg_factory, runtime_env, monkeypatch, direction
):
    await install_agents(pg_factory, runtime_env, monkeypatch, [{"action": "accept_invitation"}])
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = direction
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    assert_collection(message, view, direction, authenticated=False)
    assert "费用需依据正式政策确认" in message.model_dump_json()
    assert not any(part.type == "input_request" for part in message.parts)


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_failed_answer_never_creates_collection(
    pg_factory, runtime_env, monkeypatch, direction
):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "accept_invitation"}]
    )
    original = model.with_structured_output

    def structured(self, schema, **kwargs):
        if schema.__name__ == "AnswerDraft":
            return RunnableLambda(lambda messages: {"invalid": True})
        return original(schema, **kwargs)

    monkeypatch.setattr(type(model), "with_structured_output", structured)
    who, conversation = await member(pg_factory, direction)
    with pytest.raises(ServiceError, match="UPSTREAM_UNAVAILABLE"):
        await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    async with pg_factory() as db:
        view = await load_reception(db, who, conversation)
    assert not view.draft_refs
    assert view.invitation_state == "none"


@pytest.mark.parametrize(
    "direction,text",
    [
        ("organization", "寻源慢"),
        ("supplier", "难比价"),
        ("buyer", "客户说他想看效果，不是我"),
        ("buyer", "暂时不想看效果"),
    ],
)
async def test_shortcuts_do_not_override_role_or_quoted_and_negated_text(
    pg_factory, runtime_env, monkeypatch, direction, text
):
    model, _ = await install_agents(
        pg_factory, runtime_env, monkeypatch, [{"action": "consultation"}]
    )
    who, conversation = await member(pg_factory, direction)
    _, view = await talk(pg_factory, runtime_env, who, conversation, text)
    assert len(model.agent_calls) == 1
    assert not view.draft_refs


@pytest.mark.parametrize(
    "direction,text",
    [
        ("buyer", "寻源慢"),
        ("buyer", "难比价"),
        ("buyer", "想看效果"),
        ("organization", "观看样板演示"),
        ("supplier", "我想合作"),
    ],
)
async def test_legacy_member_conversion_opens_role_collection(
    pg_factory, runtime_env, monkeypatch, direction, text
):
    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await member(pg_factory, direction)
    message, view = await talk(pg_factory, runtime_env, who, conversation, text)
    assert_collection(message, view, direction)


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_legacy_review_conversion_announces_reopening_without_new_draft(
    pg_factory, runtime_env, monkeypatch, direction
):
    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await member(pg_factory, direction)
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    draft = view.active_draft
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, UUID(draft.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
    message, again = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    assert len(again.draft_refs) == 1 and again.active_draft.id == draft.id
    assert again.active_draft.state == "review"
    assert any(
        part.type == "system_notice" and part.text == "开始填写需求资料" for part in message.parts
    )


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
async def test_guest_existing_legacy_draft_never_announces_member_collection(
    pg_factory, runtime_env, monkeypatch, direction
):
    await install(pg_factory, runtime_env, monkeypatch)
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        draft = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="guest-existing", direction=direction, kind="service"),
        )
    message, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    assert view.active_draft.id == draft.id and len(view.draft_refs) == 1
    assert not any(part.type == "system_notice" for part in message.parts)
