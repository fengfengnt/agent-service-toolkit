import copy
from pathlib import Path

import yaml

AGENT_IDS = (
    "xiaohe-reception",
    "xiaohe-buyer",
    "xiaohe-organization",
    "xiaohe-supplier",
)


def workflow_data(agent_id: str = "xiaohe-buyer") -> dict:
    return copy.deepcopy(
        {
            "schema_version": 1,
            "agent_id": agent_id,
            "name": "测试流程",
            "workflow_version": "1",
            "entry": "understand",
            "prompts": {"intent": "intent.md", "answer": "answer.md"},
            "skills": [],
            "required_fields": [] if agent_id == "xiaohe-reception" else ["companyName"],
            "rounds": []
            if agent_id == "xiaohe-reception"
            else [{"id": "round1", "name": "企业信息", "fields": ["companyName", "city"]}],
            "nodes": [
                {
                    "id": "understand",
                    "name": "理解表达",
                    "type": "understand",
                    "prompt": "intent",
                    "next": "decide",
                },
                {
                    "id": "decide",
                    "name": "选择合法路径",
                    "type": "decide",
                    "routes": {"consultation": "answer"},
                    "fallback": "clarify",
                },
                {
                    "id": "answer",
                    "name": "回答问题",
                    "type": "answer",
                    "prompt": "answer",
                    "next": "result",
                },
                {"id": "clarify", "name": "澄清问题", "type": "result", "wait_for_input": True},
                {"id": "result", "name": "返回答复", "type": "result", "wait_for_input": True},
            ],
        }
    )


def write_test_bundle(root: Path) -> Path:
    (root / "workflows").mkdir(parents=True)
    (root / "prompts").mkdir()
    (root / "prompts/intent.md").write_text("理解表达，不执行未经授权的动作。", "utf-8")
    (root / "prompts/answer.md").write_text("依据已确认资料回答。", "utf-8")
    for agent_id in AGENT_IDS:
        filename = agent_id.removeprefix("xiaohe-") + ".yaml"
        (root / "workflows" / filename).write_text(
            yaml.safe_dump(workflow_data(agent_id), allow_unicode=True), "utf-8"
        )
    return root
