from collections.abc import Sequence
from typing import TYPE_CHECKING

from xiaohe.knowledge.sources import (
    KnowledgeManifest,
    KnowledgeMaterial,
    KnowledgeSource,
    manifest_from_bundle,
    parse_source,
    prepare_manifest,
)
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


def stale_shared_entry_ids(bundle: "RuntimeBundle", manifest: KnowledgeManifest) -> frozenset[str]:
    source = shared_source(bundle)
    if source is None:
        return frozenset()
    actual = {k: v for k, v in manifest.entries.items() if v.id == SHARED_DOCUMENT_ID}
    expected, _ = prepare_manifest(
        [source],
        embedding_model=manifest.embedding_model,
        embedding_identity=manifest.embedding_identity,
        dimensions=manifest.dimensions,
    )
    return frozenset(actual) if actual != expected.entries else frozenset()


def filter_shared_materials(
    bundle: "RuntimeBundle",
    materials: Sequence[KnowledgeMaterial],
) -> list[KnowledgeMaterial]:
    manifest = manifest_from_bundle(bundle)
    if shared_source(bundle) is None or manifest is None:
        return list(materials)
    duplicates = {
        f"kb:{manifest.version}:{key}"
        for key, entry in manifest.entries.items()
        if entry.id == SHARED_DOCUMENT_ID
    }
    return [item for item in materials if item.source not in duplicates]
