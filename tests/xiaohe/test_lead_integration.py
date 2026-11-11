from copy import deepcopy
from shutil import copytree
from uuid import UUID

import pytest
import yaml
from sqlalchemy import select
from test_agent_handoff import install_agents
from test_lead_conversion import member
from test_lead_submission import Business
from test_reception_flow import talk

from xiaohe.agents.runtime import load_agent_registry
from xiaohe.models import Command, Run
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.workflows.definitions import RESOURCE_ROOT

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


def complete_fields(role):
    public = (
        {
            "organizationName": "虚构测试协会",
            "memberSize": "100—500",
            "coverage": "本市",
            "onlineChannels": ["暂无"],
        }
        if role == "organization"
        else {"companyName": "虚构测试企业"}
    )
    return public | {"contactName": "测试联系人", "contactPhone": "测试总台分机"}


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_three_roles_complete_collection_and_direct_submit(
    pg_factory, runtime_env, monkeypatch, role
):
    client = Business()
    await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [{"action": "accept_invitation"}, {"action": "confirm_current_summary"}],
        business_client=client,
    )
    who, conversation = await member(pg_factory, role)
    first, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作，请介绍一下")
    assert any(p.type == "system_notice" and p.text == "开始填写需求资料" for p in first.parts)
    draft = view.active_draft
    async with pg_factory.begin() as db:
        await patch_draft(db, who, draft.id, draft.revision, complete_fields(role))
    message, view = await talk(pg_factory, runtime_env, who, conversation, "可以")
    assert view.active_draft.state == "accepted"
    assert "本次需求已受理，可在“我的进展”查看状态。" in message.model_dump_json()
    async with pg_factory() as db:
        record = await db.scalar(select(ReceptionSubmission))
        assert (
            record.summary_version is None
            and record.confirmation_evidence["source"] == "semantic_direct"
        )
        assert record.payload["direction"] == role and len(client.payloads) == 1


async def test_existing_draft_next_command_uses_new_interaction(
    pg_factory, runtime_env, monkeypatch, tmp_path
):
    root = tmp_path / "resources"
    copytree(RESOURCE_ROOT, root)
    model, original = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "accept_invitation"},
            {
                "action": "provide_details",
                "candidates": {
                    "contactName": {"value": "测试联系人", "evidence": "联系人测试联系人"}
                },
            },
        ],
        root=root,
    )
    who, conversation = await member(pg_factory, "buyer")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我想合作")
    async with pg_factory() as db:
        first = await db.scalar(select(Command).where(Command.conversation_id == conversation))
        run_id, old_policy, saved_payload = (
            first.run_id,
            first.interaction_policy_id,
            deepcopy(first.payload),
        )
        field_id = (await db.get(ReceptionDraft, UUID(view.active_draft.id))).field_policy_id
    common = root / "lead-skills/common/policy.yaml"
    data = yaml.safe_load(common.read_text("utf-8"))
    data["version"] = 2
    common.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    fields = root / "lead-skills/buyer/policy.yaml"
    data = yaml.safe_load(fields.read_text("utf-8"))
    data["required_fields"].append("city")
    fields.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    latest = await load_agent_registry(pg_factory, root, model, None)
    assert latest.bundle.bundle_id != original.bundle.bundle_id
    _, again = await talk(pg_factory, runtime_env, who, conversation, "联系人测试联系人")
    assert again.active_draft.id == view.active_draft.id
    assert "city" not in again.active_draft.missing_required
    async with pg_factory.begin() as db:
        commands = (
            await db.scalars(
                select(Command)
                .where(Command.conversation_id == conversation)
                .order_by(Command.created_at)
            )
        ).all()
        assert len(commands) == 2 and commands[1].run_id == run_id
        assert commands[0].interaction_policy_id == old_policy != commands[1].interaction_policy_id
        assert commands[0].payload == saved_payload
        assert (await db.get(Run, run_id)).workflow_bundle_id == original.bundle.bundle_id
        assert (
            await db.get(ReceptionDraft, UUID(view.active_draft.id))
        ).field_policy_id == field_id
        fresh = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="fresh-policy", kind="service", direction="buyer"),
        )
        assert "city" in fresh.missing_required


async def test_policy_upgrade_and_lost_receipt_keep_original_request(
    pg_factory, runtime_env, monkeypatch, tmp_path
):
    root = tmp_path / "resources"
    copytree(RESOURCE_ROOT, root)
    client = Business(lose_reply=True)
    model, _ = await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "accept_invitation"},
            {"action": "confirm_current_summary"},
            {"action": "query_progress"},
        ],
        root=root,
        business_client=client,
    )
    who, conversation = await member(pg_factory, "buyer")
    _, view = await talk(pg_factory, runtime_env, who, conversation, "我想登记")
    async with pg_factory.begin() as db:
        await patch_draft(
            db, who, view.active_draft.id, view.active_draft.revision, complete_fields("buyer")
        )
    message, _ = await talk(pg_factory, runtime_env, who, conversation, "好的")
    assert "正在核实提交结果，请稍后查询，无需重复提交。" in message.model_dump_json()
    async with pg_factory() as db:
        record = await db.scalar(select(ReceptionSubmission))
        key, payload, evidence = (
            record.submission_key,
            deepcopy(record.payload),
            deepcopy(record.confirmation_evidence),
        )
    path = root / "lead-skills/common/policy.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    data["version"] = 2
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    await load_agent_registry(pg_factory, root, model, client)
    message, view = await talk(pg_factory, runtime_env, who, conversation, "查询受理结果")
    assert view.active_draft.state == "accepted"
    assert "本次需求已受理，可在“我的进展”查看状态。" in message.model_dump_json()
    async with pg_factory() as db:
        record = await db.scalar(select(ReceptionSubmission))
        assert (record.submission_key, record.payload, record.confirmation_evidence) == (
            key,
            payload,
            evidence,
        )
    assert len(client.payloads) == 1


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
async def test_new_and_resumed_drafts_emit_collection_notice(
    pg_factory, runtime_env, monkeypatch, role
):
    name = "旧测试协会" if role == "organization" else "旧测试公司"
    await install_agents(
        pg_factory,
        runtime_env,
        monkeypatch,
        [
            {"action": "accept_invitation"},
            {"action": "new_need"},
            {"action": "resume_draft", "draft_name": name},
            {"action": "resume_draft", "draft_name": "不存在的名称"},
        ],
    )
    who, conversation = await member(pg_factory, role)
    _, first = await talk(pg_factory, runtime_env, who, conversation, "我想登记")
    async with pg_factory.begin() as db:
        await patch_draft(
            db,
            who,
            first.active_draft.id,
            first.active_draft.revision,
            {"organizationName" if role == "organization" else "companyName": name},
        )
    message, new = await talk(pg_factory, runtime_env, who, conversation, "登记另一项需求")
    assert new.active_draft.id != first.active_draft.id
    assert any(p.type == "system_notice" and p.text == "开始填写需求资料" for p in message.parts)
    message, resumed = await talk(pg_factory, runtime_env, who, conversation, "继续" + name)
    assert resumed.active_draft.id == first.active_draft.id
    assert any(p.type == "system_notice" and p.text == "开始填写需求资料" for p in message.parts)
    message, _ = await talk(pg_factory, runtime_env, who, conversation, "继续不存在的名称")
    assert not any(p.type == "system_notice" for p in message.parts)
