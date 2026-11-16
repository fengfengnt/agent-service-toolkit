from typing import TYPE_CHECKING

from xiaohe.knowledge.sources import KnowledgeSource, parse_source
from xiaohe.skill_schema import AGENT_IDS

if TYPE_CHECKING:
    from xiaohe.deep_runtime.models import RuntimeBundle

SHARED_PATH = "knowledge/platform-common.md"
SHARED_DOCUMENT_ID = "platform-common"
SHARED_SOURCE_ID = "shared:platform-common"
MAX_SHARED_BODY_BYTES = 8192


def parse_shared_source(text: str) -> KnowledgeSource:
    source = parse_source(text, {agent: set() for agent in AGENT_IDS})
    if (
        source.id != SHARED_DOCUMENT_ID
        or set(source.agents) != set(AGENT_IDS)
        or source.topics != ["general"]
        or len(source.text.encode("utf-8")) > MAX_SHARED_BODY_BYTES
    ):
        raise ValueError("invalid shared knowledge")
    return source


def shared_source(bundle: "RuntimeBundle") -> KnowledgeSource | None:
    text = bundle.resources.get(SHARED_PATH)
    return parse_shared_source(text) if text is not None else None
