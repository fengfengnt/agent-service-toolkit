import pytest
from test_admission import visitor

from xiaohe.models import Conversation
from xiaohe.reception.contracts import ReceptionControl
from xiaohe.reception.flow import apply_reception_control


def test_welcome_can_select_buyer_through_authoritative_control():
    request = ReceptionControl.model_validate(
        {"clientActionId": "welcome-buyer", "action": "switch_direction", "direction": "buyer"}
    )
    assert request.direction == "buyer"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_visible_leave_info_link_is_sufficient_invitation_consent(pg_factory, job_app):
    who, conversation = await visitor(pg_factory)
    async with pg_factory.begin() as db:
        (await db.get(Conversation, conversation)).direction = "organization"
        accepted = await apply_reception_control(
            db, who, conversation,
            ReceptionControl(client_action_id="leave-info", action="accept_invitation"),
        )
        assert accepted.run.status == "queued"


@pytest.mark.pg
@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["accept_invitation", "start_collection"])
async def test_lead_entry_is_not_submission_consent(pg_factory, job_app, tmp_path, control):
    from test_deep_tools import tool_context, tools_module

    from xiaohe.contracts import ServiceError
    from xiaohe.models import Command

    context, _ = await tool_context(pg_factory, tmp_path)
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {"receptionControl": control}
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        async with pg_factory.begin() as db:
            await tools_module().checked_context(db, context, "submit_lead")
