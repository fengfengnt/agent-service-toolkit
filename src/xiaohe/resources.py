from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from xiaohe.skill_schema import SkillValidationError

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"

MAX_DEFINITION_BYTES = 256 * 1024


MAX_RESOURCE_BYTES = 1024 * 1024


class DefinitionLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise SkillValidationError("workflow aliases are not allowed")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        result: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise SkillValidationError("workflow keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result
