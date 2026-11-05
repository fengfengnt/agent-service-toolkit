import asyncio
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from pydantic import TypeAdapter

from xiaohe.contracts import Id, ServiceError, WireModel
from xiaohe.reception.contracts import (
    Direction,
    KnowledgeHit,
    KnowledgeRequest,
    SkillAnswer,
    SkillContext,
)
from xiaohe.reception.knowledge import KnowledgeQuery

_ROOT = Path(__file__).resolve().parents[3] / "resources/xiaohe/skills"


class SkillDefinition(WireModel):
    id: Id
    direction: Direction
    title: Id
    path: Id


@lru_cache(maxsize=1)
def skill_catalog() -> dict[str, SkillDefinition]:
    definitions = TypeAdapter(list[SkillDefinition]).validate_json(
        (_ROOT / "catalog.json").read_text("utf-8")
    )
    return {item.id: item for item in definitions}


async def answer_skill(
    skill_id: str,
    question: str,
    context: SkillContext,
    model: BaseChatModel,
    knowledge_query: KnowledgeQuery | None = None,
) -> SkillAnswer:
    definition = skill_catalog().get(skill_id)
    if definition is None:
        raise ServiceError("INPUT_INVALID", 422)
    if definition.direction != context.direction:
        raise ServiceError("FORBIDDEN", 403)
    path = (_ROOT / definition.path).resolve()
    if not path.is_relative_to(_ROOT.resolve()):
        raise ServiceError("INPUT_INVALID", 422)
    content = path.read_text("utf-8").removeprefix("# ").strip()
    sources = []
    if knowledge_query is not None:
        try:
            # 首版按审定主题检索，不传原话、联系人、公司资料或附件正文。
            found = await asyncio.wait_for(
                knowledge_query(
                    KnowledgeRequest(
                        direction=context.direction, skill_id=skill_id, query=definition.title
                    )
                ),
                timeout=2,
            )
            sources = TypeAdapter(list[KnowledgeHit]).validate_python(found)[:3]
        except Exception:
            sources = []
    if sources:
        content += "\n\n已确认资料：\n" + "\n".join(
            f"{item.text}\n适用条件：{item.conditions}（来源：{item.source}，版本：{item.version}）"
            for item in sources
        )
    # shortcut: 首版使用已审定基础文本，不让模型扩写承诺；有受评测知识库后再增加生成式回答。
    return SkillAnswer(text=content, sources=sources)
