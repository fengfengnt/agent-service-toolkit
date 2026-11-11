import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from xiaohe.knowledge.build import COLLECTION, check_complete, claim_root, validate_vectors
from xiaohe.knowledge.sources import KnowledgeMaterial, embedding_identity, manifest_from_bundle
from xiaohe.settings import Settings
from xiaohe.workflows.models import AgentId, WorkflowBundle

logger = logging.getLogger(__name__)
QUERY_TIMEOUT = 2.0


class EmbeddedKnowledge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = False
        self._executor: ThreadPoolExecutor | None = None
        self._future: asyncio.Future[list[KnowledgeMaterial]] | None = None
        self._clients: dict[str, Any] = {}
        self._embeddings = None
        self._root: Path | None = None

    async def start(self) -> None:
        settings = self.settings
        if not all(
            (
                settings.knowledge_root,
                settings.knowledge_embedding_base_url,
                settings.knowledge_embedding_model,
                settings.knowledge_embedding_api_key,
                settings.knowledge_embedding_dimensions,
            )
        ):
            logger.info("xiaohe_knowledge:disabled")
            return
        root = settings.knowledge_root
        if root is None:
            return
        try:
            self._root = root.resolve()
            claim_root(self._root)
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="xiaohe-knowledge"
            )
            from xiaohe.knowledge.build import create_embeddings

            try:
                self._embeddings = await asyncio.get_running_loop().run_in_executor(
                    self._executor, create_embeddings, settings
                )
            except Exception:
                logger.warning("xiaohe_knowledge:initialization_failed")
                self._executor.shutdown(wait=True)
                self._executor = None
                return
            self.enabled = True
        except OSError:
            logger.warning("xiaohe_knowledge:owner_unavailable")

    async def query(
        self,
        bundle: WorkflowBundle,
        agent_id: AgentId,
        topics: Sequence[str],
    ) -> list[KnowledgeMaterial]:
        if not self.enabled or self._executor is None:
            return []
        if self._future is not None and not self._future.done():
            logger.info("xiaohe_knowledge:busy")
            return []
        self._future = asyncio.get_running_loop().run_in_executor(
            self._executor,
            self._retrieve,
            bundle,
            agent_id,
            tuple(topics),
        )
        self._future.add_done_callback(
            lambda future: None if future.cancelled() else future.exception()
        )
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), QUERY_TIMEOUT)
        except TimeoutError:
            logger.warning("xiaohe_knowledge:timeout")
            return []
        except Exception:
            logger.warning("xiaohe_knowledge:unavailable")
            return []

    def _retrieve(
        self,
        bundle: WorkflowBundle,
        agent_id: AgentId,
        topics: Sequence[str],
    ) -> list[KnowledgeMaterial]:
        from xiaohe.knowledge.build import create_embeddings

        manifest = manifest_from_bundle(bundle)
        if manifest is None or not manifest.entries:
            logger.info("xiaohe_knowledge:no_manifest_or_empty")
            return []
        definition = bundle.definitions[agent_id]
        if set(topics) - set(definition.skills):
            logger.warning("xiaohe_knowledge:invalid_scope")
            return []
        settings = self.settings
        endpoint = settings.knowledge_embedding_base_url
        model = settings.knowledge_embedding_model
        if (
            endpoint is None
            or model is None
            or self._root is None
            or manifest.embedding_model != model
            or manifest.dimensions != settings.knowledge_embedding_dimensions
            or manifest.embedding_identity != embedding_identity(endpoint, model)
        ):
            logger.warning("xiaohe_knowledge:identity_mismatch")
            return []
        directory = self._root / manifest.version
        if not directory.resolve().is_relative_to(self._root) or not directory.is_dir():
            logger.warning("xiaohe_knowledge:version_missing")
            return []
        try:
            check_complete(directory, manifest)
        except (ValueError, OSError):
            logger.warning("xiaohe_knowledge:index_invalid")
            return []
        if manifest.version not in self._clients:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._clients[manifest.version] = chromadb.PersistentClient(
                path=str(directory),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        if self._embeddings is None:
            self._embeddings = create_embeddings(settings)
        # shortcut: 按审定主题检索；需要同主题细粒度问答时先评测脱敏查询。
        query = "平台介绍与服务方向"
        if topics:
            catalog = json.loads(bundle.resources["skills/catalog.json"])
            titles = {item["id"]: item["title"] for item in catalog}
            query = "；".join(titles[topic] for topic in sorted(set(topics)))
        (vector,) = validate_vectors([self._embeddings.embed_query(query)], 1, manifest.dimensions)
        scopes = ["general", *sorted(set(topics))]
        topic_filters = [{"topic." + topic: {"$eq": True}} for topic in scopes]
        where = {
            "$and": [
                {"version": {"$eq": manifest.version}},
                {"agent." + agent_id: {"$eq": True}},
                topic_filters[0] if len(topic_filters) == 1 else {"$or": topic_filters},
            ]
        }
        result = (
            self._clients[manifest.version]
            .get_collection(COLLECTION, embedding_function=None)
            .query(
                query_embeddings=[vector],
                n_results=min(3, len(manifest.entries)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        )
        hits = []
        seen = set()
        for key, text, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            entry = manifest.entries.get(key)
            if (
                entry is None
                or key in seen
                or agent_id not in entry.agents
                or not set(entry.topics).intersection(scopes)
                or metadata.get("version") != manifest.version
                or metadata.get("agent." + agent_id) is not True
                or not any(metadata.get("topic." + topic) is True for topic in scopes)
                or not math.isfinite(distance)
                or not 0 <= distance <= 0.5
                or hashlib.sha256(text.encode()).hexdigest() != entry.text_sha256
            ):
                continue
            hits.append(
                KnowledgeMaterial(
                    text=text,
                    source=f"kb:{manifest.version}:{key}",
                    version=manifest.version,
                    display_source=entry.source,
                    conditions=entry.conditions,
                )
            )
            seen.add(key)
        if not hits:
            logger.info("xiaohe_knowledge:no_hits")
        return hits

    async def aclose(self) -> None:
        self.enabled = False
        if self._future is not None:
            try:
                await asyncio.shield(self._future)
            except Exception:
                pass
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
