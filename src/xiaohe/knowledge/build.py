import hashlib
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from langchain_core.embeddings import Embeddings

from xiaohe.knowledge.sources import KnowledgeChunk, KnowledgeManifest, read_manifest
from xiaohe.settings import Settings
from xiaohe.workflows.loader import MAX_RESOURCE_BYTES, _read_resource

COLLECTION = "xiaohe_knowledge"


def create_embeddings(settings: Settings):
    from langchain_openai import OpenAIEmbeddings

    endpoint = settings.knowledge_embedding_base_url
    model = settings.knowledge_embedding_model
    key = settings.knowledge_embedding_api_key
    if not endpoint or not model or not key or not settings.knowledge_embedding_dimensions:
        raise ValueError("knowledge embedding configuration required")
    return OpenAIEmbeddings(
        base_url=endpoint,
        model=model,
        api_key=key.get_secret_value(),
        max_retries=0,
        request_timeout=1.5,
        check_embedding_ctx_length=False,
    )


def validate_vectors(vectors, count: int, dimensions: int) -> list[list[float]]:
    if len(vectors) != count or any(
        len(vector) != dimensions
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
        or not any(vector)
        for vector in vectors
    ):
        raise ValueError("invalid knowledge embeddings")
    return vectors


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def check_complete(directory: Path, manifest: KnowledgeManifest) -> None:
    if read_manifest(_read_resource(directory, "manifest.json", MAX_RESOURCE_BYTES)) != manifest:
        raise ValueError("knowledge manifest mismatch")
    marker = json.loads(_read_resource(directory, "complete.json", 1024))
    if marker != {"version": manifest.version, "count": len(manifest.entries)}:
        raise ValueError("incomplete knowledge index")
    if not (directory / "chroma.sqlite3").is_file():
        raise ValueError("missing knowledge index")


def build_index(
    manifest: KnowledgeManifest,
    chunks: Sequence[KnowledgeChunk],
    directory: Path,
    embeddings: Embeddings,
) -> None:
    import chromadb
    from chromadb.api.types import Metadata, PyEmbeddings
    from chromadb.config import Settings

    if {item.id for item in chunks} != set(manifest.entries) or len(chunks) != len(
        manifest.entries
    ):
        raise ValueError("knowledge chunks mismatch")
    for chunk in chunks:
        if (
            hashlib.sha256(chunk.text.encode()).hexdigest()
            != manifest.entries[chunk.id].text_sha256
        ):
            raise ValueError("knowledge content mismatch")
    directory.mkdir(parents=True, exist_ok=False)
    client = chromadb.PersistentClient(
        path=str(directory), settings=Settings(anonymized_telemetry=False)
    )
    collection = client.create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )
    for offset in range(0, len(chunks), 64):
        batch = chunks[offset : offset + 64]
        vectors = validate_vectors(
            embeddings.embed_documents([item.text for item in batch]),
            len(batch),
            manifest.dimensions,
        )
        metadata: list[Metadata] = []
        for chunk in batch:
            entry = manifest.entries[chunk.id]
            metadata.append(
                {
                    "version": manifest.version,
                    **{"agent." + agent: True for agent in entry.agents},
                    **{"topic." + topic: True for topic in entry.topics},
                }
            )
        collection.add(
            ids=[item.id for item in batch],
            documents=[item.text for item in batch],
            embeddings=cast(PyEmbeddings, vectors),
            metadatas=metadata,
        )
    result = collection.get(include=["documents"])
    documents = result["documents"]
    if (
        documents is None
        or collection.count() != len(chunks)
        or dict(zip(result["ids"], documents)) != {item.id: item.text for item in chunks}
    ):
        raise ValueError("knowledge index validation failed")
    write_atomic(directory / "manifest.json", manifest.model_dump_json())
    write_atomic(
        directory / "complete.json", json.dumps({"version": manifest.version, "count": len(chunks)})
    )


def publish_index(staging: Path, root: Path, manifest: KnowledgeManifest) -> Path:
    root = root.resolve()
    if staging.resolve().parent != root or staging.is_symlink():
        raise ValueError("knowledge staging must be inside index root")
    try:
        check_complete(staging, manifest)
    except (OSError, ValueError):
        raise ValueError("incomplete knowledge build") from None
    target = root / manifest.version
    if target.exists():
        try:
            if target.is_symlink():
                raise ValueError("linked knowledge version")
            check_complete(target, manifest)
        except (OSError, ValueError):
            raise ValueError("existing knowledge index is invalid") from None
        return target
    try:
        staging.rename(target)
    except FileExistsError:
        check_complete(target, manifest)
    return target
