import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self
from urllib.parse import urlsplit, urlunsplit

import yaml
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xiaohe.reception.contracts import KnowledgeHit
from xiaohe.resources import MAX_RESOURCE_BYTES, DefinitionLoader
from xiaohe.skill_schema import AgentId

if TYPE_CHECKING:
    from xiaohe.deep_runtime.models import RuntimeBundle

MANIFEST_KEY = "knowledge/manifest.json"
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
DocumentId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Label = Annotated[str, Field(min_length=1, max_length=200)]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def embedding_identity(endpoint: str, model: str) -> str:
    url = urlsplit(endpoint)
    return digest(
        [urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", "")), model]
    )


class KnowledgeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: DocumentId
    title: Label
    source: Label
    conditions: Annotated[str, Field(min_length=1, max_length=1000)]
    agents: list[AgentId] = Field(min_length=1, max_length=4)
    topics: list[Label] = Field(min_length=1, max_length=32)

    @field_validator("title", "source", "conditions")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank knowledge field")
        return value.strip()

    @field_validator("agents", "topics")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate knowledge scope")
        return sorted(value)


class KnowledgeChunk(BaseModel):
    id: str
    document_id: DocumentId
    text: str


class KnowledgeSource(KnowledgeMetadata):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty knowledge body")
        return value.strip()


class KnowledgeEntry(KnowledgeMetadata):
    text_sha256: Sha256


class KnowledgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    format_version: Literal[1] = 1
    version: Sha256
    embedding_model: Label
    embedding_identity: Sha256
    dimensions: int = Field(gt=0)
    chunk_size: Literal[1200] = 1200
    chunk_overlap: Literal[200] = 200
    entries: dict[str, KnowledgeEntry]

    @model_validator(mode="after")
    def verify_digest(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"version"})
        if self.version != digest(payload):
            raise ValueError("invalid knowledge version")
        for key, entry in self.entries.items():
            prefix, separator, index = key.rpartition(":")
            if not separator or prefix != entry.id or not index.isdecimal():
                raise ValueError("invalid knowledge chunk id")
        return self


class KnowledgeMaterial(KnowledgeHit):
    display_source: Label


def validate_scope(source: KnowledgeMetadata, allowed_topics: Mapping[AgentId, set[str]]) -> None:
    if any(
        topic != "general"
        and not any(topic in allowed_topics.get(agent, set()) for agent in source.agents)
        for topic in source.topics
    ) or ("xiaohe-reception" in source.agents and source.topics != ["general"]):
        raise ValueError("invalid knowledge scope")


def load_sources(root: Path, allowed_topics: Mapping[AgentId, set[str]]) -> list[KnowledgeSource]:
    if root.is_symlink():
        raise ValueError("knowledge directory cannot be a link")
    root = root.resolve()
    sources = []
    seen = set()
    for path in sorted(root.rglob("*.md")):
        if not path.resolve().is_relative_to(root):
            raise ValueError("knowledge source escapes root")
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_RESOURCE_BYTES + 1)
            if len(raw) > MAX_RESOURCE_BYTES:
                raise ValueError("knowledge source too large")
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            if not text.startswith("---\n"):
                raise ValueError("knowledge front matter required")
            header, delimiter, body = text[4:].partition("\n---\n")
            if not delimiter:
                raise ValueError("knowledge front matter not closed")
            metadata = yaml.load(header, Loader=DefinitionLoader)
            if not isinstance(metadata, dict) or "text" in metadata:
                raise ValueError("invalid knowledge metadata")
            source = KnowledgeSource(**metadata, text=body)
            validate_scope(source, allowed_topics)
            if source.id in seen:
                raise ValueError("duplicate knowledge source id")
            seen.add(source.id)
            sources.append(source)
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
            raise ValueError("invalid knowledge source") from None
    return sources


def prepare_manifest(
    sources: Sequence[KnowledgeSource],
    *,
    embedding_model: str,
    embedding_identity: str,
    dimensions: int,
) -> tuple[KnowledgeManifest, list[KnowledgeChunk]]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200)
    entries = {}
    chunks = []
    for source in sorted(sources, key=lambda item: item.id):
        for index, text in enumerate(splitter.split_text(source.text)):
            key = f"{source.id}:{index}"
            if key in entries:
                raise ValueError("duplicate knowledge source id")
            entries[key] = {
                **source.model_dump(exclude={"text"}),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            chunks.append(KnowledgeChunk(id=key, document_id=source.id, text=text))
    payload = dict(
        format_version=1,
        embedding_model=embedding_model,
        embedding_identity=embedding_identity,
        dimensions=dimensions,
        chunk_size=1200,
        chunk_overlap=200,
        entries=entries,
    )
    manifest = KnowledgeManifest.model_validate({"version": digest(payload), **payload})
    if len(manifest.model_dump_json().encode()) > MAX_RESOURCE_BYTES:
        raise ValueError("knowledge manifest too large")
    return manifest, chunks


def read_manifest(text: str) -> KnowledgeManifest:
    if len(text.encode()) > MAX_RESOURCE_BYTES:
        raise ValueError("knowledge manifest too large")
    return KnowledgeManifest.model_validate_json(text)


def manifest_from_bundle(bundle: "RuntimeBundle") -> KnowledgeManifest | None:
    text = bundle.resources.get(MANIFEST_KEY)
    return read_manifest(text) if text is not None else None
