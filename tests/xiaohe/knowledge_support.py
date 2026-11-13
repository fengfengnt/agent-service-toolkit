import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import copytree

import yaml
from langchain_core.embeddings import Embeddings


def write_test_bundle(root: Path) -> Path:
    from xiaohe.resources import RESOURCE_ROOT

    copytree(RESOURCE_ROOT, root)
    return root


def write_knowledge_source(
    root: Path,
    *,
    id="policy",
    agents=("xiaohe-buyer",),
    topics=("buyer-rfx",),
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


@contextmanager
def embedding_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            texts = request["input"]
            if isinstance(texts, str):
                texts = [texts]
            response = json.dumps(
                {
                    "object": "list",
                    "model": "fixture",
                    "data": [
                        {
                            "object": "embedding",
                            "index": index,
                            "embedding": DeterministicEmbeddings().embed_query(text),
                        }
                        for index, text in enumerate(texts)
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
