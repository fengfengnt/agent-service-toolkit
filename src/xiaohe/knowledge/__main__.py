import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.knowledge.build import (
    build_index,
    create_embeddings,
    publish_index,
    verify_index,
    write_atomic,
)
from xiaohe.knowledge.sources import (
    KnowledgeChunk,
    embedding_identity,
    load_sources,
    prepare_manifest,
    read_manifest,
)
from xiaohe.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="构建小鹤 Markdown 知识候选版本，不自动发布工作流。"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--resources", type=Path, required=True)
    build.add_argument("--index-root", type=Path, required=True)
    build.add_argument("--manifest-out", type=Path, required=True)
    child = commands.add_parser("_build", help=argparse.SUPPRESS)
    child.add_argument("payload", type=Path)
    child.add_argument("directory", type=Path)
    verify = commands.add_parser("_verify", help=argparse.SUPPRESS)
    verify.add_argument("directory", type=Path)
    verify.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "_verify":
            verify_index(args.directory, read_manifest(args.manifest.read_text("utf-8")))
            return
        settings = Settings(toolkit_base_url="http://127.0.0.1", auth_secret="knowledge-build-only")
        if args.command == "_build":
            data = json.loads(args.payload.read_text("utf-8"))
            manifest = read_manifest(json.dumps(data["manifest"]))
            build_index(
                manifest,
                [KnowledgeChunk(**item) for item in data["chunks"]],
                args.directory,
                create_embeddings(settings),
            )
            return
        resources = args.resources.resolve()
        model = settings.knowledge_embedding_model
        endpoint = settings.knowledge_embedding_base_url
        dimensions = settings.knowledge_embedding_dimensions
        if not model or not endpoint or not dimensions or not settings.knowledge_embedding_api_key:
            raise ValueError("knowledge embedding configuration required")
        root = args.index_root.resolve()
        if root.is_relative_to(resources):
            raise ValueError("index root must be outside resources")
        bundle = load_runtime_bundle(resources)
        sources = load_sources(
            resources / "knowledge",
            {key: set(value.skills) for key, value in bundle.bindings.items()},
        )
        manifest, chunks = prepare_manifest(
            sources,
            embedding_model=model,
            embedding_identity=embedding_identity(endpoint, model),
            dimensions=dimensions,
        )
        root.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        payload = root / (".build-" + token + ".json")
        staging = root / (".staging-" + token)
        write_atomic(
            payload,
            json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "chunks": [item.model_dump() for item in chunks],
                },
                ensure_ascii=False,
            ),
        )
        try:
            result = subprocess.run(
                [sys.executable, "-m", "xiaohe.knowledge", "_build", str(payload), str(staging)],
                capture_output=True,
            )
            if result.returncode:
                raise ValueError("knowledge builder failed")
            publish_index(staging, root, manifest)
            write_atomic(args.manifest_out, manifest.model_dump_json())
        finally:
            payload.unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "version": manifest.version,
                    "documents": len(sources),
                    "chunks": len(chunks),
                    "published": False,
                }
            )
        )
    except Exception:
        raise SystemExit(
            "knowledge build failed; check sources, configuration and index storage"
        ) from None


if __name__ == "__main__":
    main()
