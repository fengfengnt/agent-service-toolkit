from sqlalchemy import select

from xiaohe.agents.contracts import AgentDirection
from xiaohe.contracts import ServiceError
from xiaohe.models import Command
from xiaohe.reception.models import ReceptionDraft
from xiaohe.runs import require_write

ROLE_DIRECTIONS: dict[str, AgentDirection] = {
    "xiaohe-reception": "unknown",
    "xiaohe-buyer": "buyer",
    "xiaohe-organization": "organization",
    "xiaohe-supplier": "supplier",
}


async def _command(db, fence):
    run = await require_write(db, fence)
    command = await db.scalar(
        select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
    )
    if command is None:
        raise ServiceError("ACTION_STALE", 409)
    return run, command


def _stage(command, agent_id):
    return command.payload.get("agentStages", {}).get(agent_id, {})


def _save_stage(command, agent_id, **values):
    stages = command.payload.get("agentStages", {})
    command.payload = command.payload | {
        "agentStages": stages | {agent_id: stages.get(agent_id, {}) | values}
    }


async def _select_direction(db, conversation, flow, agent_id):
    direction = ROLE_DIRECTIONS[agent_id]
    conversation.direction = direction
    rows = (
        await db.scalars(
            select(ReceptionDraft).where(
                ReceptionDraft.conversation_id == conversation.id,
                ReceptionDraft.direction == direction,
                ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
            )
        )
    ).all()
    flow.active_draft_id = rows[0].id if len(rows) == 1 else None
