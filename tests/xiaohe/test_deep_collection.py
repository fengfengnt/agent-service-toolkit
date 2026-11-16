import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage
from native_support import Business
from test_deep_factory import final_response
from test_deep_runtime import invoke
from test_deep_tools import tool_context

from xiaohe.contracts import ToolkitOutcome
from xiaohe.deep_runtime.forms import lead_policy_v2
from xiaohe.models import Command, Run
from xiaohe.reception.models import ReceptionDraft
from xiaohe.worker import RunWorker

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


@pytest.mark.parametrize("role", ["buyer", "organization", "supplier"])
@pytest.mark.parametrize("answer", ["身份认证已通过，资料草稿已恢复。", "本次资料已提交。"])
async def test_composed_collection_form_survives_rejected_answer(
    pg_factory, job_app, tmp_path, role, answer
):
    context, frame = await tool_context(
        pg_factory, tmp_path, role=role, text="采集信息", client=Business()
    )
    field = "organizationName" if role == "organization" else "companyName"
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {"receptionControl": "start_collection"}
        draft = await db.get(ReceptionDraft, frame.draft_id)
        draft.fields, draft.state, draft.step, draft.summary_version = (
            {},
            "collecting",
            "round1",
            None,
        )
    model = make_script_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="compose_form",
                        args={"field_groups": [[field]], "prompt": "请填写本次资料。"},
                        id="collection-form",
                    )
                ],
            ),
            final_response(answer={"text": answer, "basis": "general_advice"}),
        ]
    )
    outcome = await invoke(context, model=model)
    assert outcome.wait_for_input
    assert answer not in "\n".join(getattr(part, "text", "") for part in outcome.parts)
    assert any("请在资料面板填写" in getattr(part, "text", "") for part in outcome.parts)
    assert outcome.parts[-1].type == "input_request"
    await RunWorker(pg_factory, None)._save(
        context.fence, context.command_id, ToolkitOutcome(kind="outcome", outcome=outcome)
    )
    async with pg_factory() as db:
        run = await db.get(Run, context.fence.run_id)
        assert run.status == "waiting_input"
        assert run.pending_request["requestId"] == outcome.parts[-1].request_id
        policy = await lead_policy_v2(db, context.actor, frame.draft_id)
        assert policy.groups[0].fields == [field]
        assert policy.actions == ["save", "submit"]
        assert (await db.get(ReceptionDraft, frame.draft_id)).state == "collecting"
    assert not context.business_client.payloads
