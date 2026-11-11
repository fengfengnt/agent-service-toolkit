import pytest
from test_deep_tools import tool_context

from xiaohe.deep_runtime import forms
from xiaohe.models import Command, Message
from xiaohe.reception.models import ReceptionDraft
from xiaohe.runs import reply_id

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_nonfield_step_requires_published_evidence(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path)
    explanation = "您的资料用于需求联系，正式受理以回执为准。"
    await forms.compose_form(context, [["city"]], prompt=explanation)
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert not row.guidance_progress["steps"][explanation]["completed"]
        message = Message(
            id=reply_id(context.fence.run_id, command.id),
            conversation_id=context.conversation_id,
            system_id=context.actor.system.system_id,
            tenant_id=context.actor.system.tenant_id,
            role="assistant",
            parts=[{"type": "text", "text": explanation}],
        )
        await forms.record_published_guidance(db, command, message)
        assert not row.guidance_progress["steps"][explanation]["completed"]
        db.add(message)
        await db.flush()
        await forms.record_published_guidance(db, command, message)
        step = row.guidance_progress["steps"][explanation]
        assert step["completed"] and step["message_id"] == str(message.id)
    await forms.compose_form(context, [["city"]], prompt="新增第四步说明")
    async with pg_factory.begin() as db:
        row = await db.get(ReceptionDraft, frame.draft_id)
        assert row.guidance_progress["steps"][explanation]["completed"]
        assert not row.guidance_progress["steps"]["新增第四步说明"]["completed"]
