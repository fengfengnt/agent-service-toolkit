from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from xiaohe.contracts import EventEnvelope
from xiaohe.models import Conversation, Event

_EVENT = TypeAdapter(EventEnvelope)


def record_event(
    db: AsyncSession,
    conversation: Conversation,
    event_type: str,
    payload: dict,
    run_id: UUID | None = None,
) -> Event:
    event_id = uuid4()
    _EVENT.validate_python(
        {
            "eventId": str(event_id),
            "conversationId": str(conversation.id),
            "runId": str(run_id) if run_id else None,
            "type": event_type,
            "payload": payload,
        }
    )
    conversation.event_sequence += 1
    event = Event(
        id=event_id,
        conversation_id=conversation.id,
        system_id=conversation.system_id,
        tenant_id=conversation.tenant_id,
        sequence=conversation.event_sequence,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(event)
    return event
