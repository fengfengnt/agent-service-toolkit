from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

from xiaohe.contracts import AgentOutcome, Direction

if TYPE_CHECKING:
    from xiaohe.graph import GraphState

type ProfessionalHandler = Callable[[GraphState], Awaitable[AgentOutcome]]


async def dispatch_direction(
    direction: Direction, state: GraphState, handlers: Mapping[Direction, ProfessionalHandler]
) -> AgentOutcome:
    handler = handlers.get(direction)
    if handler is None:
        return AgentOutcome(
            parts=[{"type": "text", "text": "该方向的专业业务办理尚未开放，可以先咨询服务介绍。"}],
            wait_for_input=False,
            artifact_version_ids=[],
        )
    return await handler(state)
