import asyncio
import json
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
from xiaohe.workflows.models import AgentId, WorkflowBundle

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


def load_skill_materials(
    bundle: WorkflowBundle, agent_id: AgentId, skill_ids: list[str]
) -> list[KnowledgeHit]:
    definition = bundle.definitions[agent_id]
    if set(skill_ids) - set(definition.skills):
        raise ServiceError("FORBIDDEN", 403)
    if not skill_ids:
        return []
    try:
        catalog = TypeAdapter(list[SkillDefinition]).validate_python(
            json.loads(bundle.resources["skills/catalog.json"])
        )
        allowed = {
            item.id: item for item in catalog if item.direction == agent_id.removeprefix("xiaohe-")
        }
        return [
            KnowledgeHit(
                text=bundle.resources["skills/" + allowed[skill_id].path]
                .removeprefix("# ")
                .strip(),
                source=skill_id,
                version=bundle.bundle_id,
                conditions="接待资料，不代表已执行业务；未确认的政策和效果需顾问核实。",
            )
            for skill_id in skill_ids
        ]
    except (KeyError, ValueError, TypeError):
        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None


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
