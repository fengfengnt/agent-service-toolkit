from pathlib import Path

import yaml
from langchain_core.embeddings import Embeddings


def write_knowledge_source(
    root: Path,
    *,
    id="policy",
    agents=("xiaohe-buyer",),
    topics=("buyer.rfx",),
    text="仅用于测试的虚构政策。",
    **changes,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    metadata = dict(
        id=id,
        title="测试政策",
        source="测试审定资料",
        conditions="仅适用测试场景",
        agents=list(agents),
        topics=list(topics),
    )
    metadata.update(changes)
    path = root / (id + ".md")
    path.write_text(
        "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + text, encoding="utf-8"
    )
    return path


class DeterministicEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        if "无关" in text:
            return [0.0, 1.0, 0.0]
        return [1.0, 0.0, 0.0]
