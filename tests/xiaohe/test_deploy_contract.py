import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[2]


def docker(*args, **kwargs):
    prefix = ["wsl", "-d", "Ubuntu", "--", "docker"] if sys.platform == "win32" else ["docker"]
    return subprocess.run(prefix + list(args), capture_output=True, text=True, **kwargs)


@pytest.fixture(scope="module")
def deployment(tmp_path_factory):
    directory = tmp_path_factory.mktemp("deploy")
    runtime = directory / "runtime.env"
    runtime.write_text("AUTH_SECRET=fixture-deploy-secret\n", encoding="utf-8")
    env_file = directory / "compose.env"
    root, runtime_path = ROOT.as_posix(), runtime.as_posix()
    if sys.platform == "win32":

        def linux(path):
            return subprocess.check_output(
                ["wsl", "-d", "Ubuntu", "--", "wslpath", "-a", path], text=True
            ).strip()

        root, runtime_path = linux(root), linux(runtime_path)
    env_file.write_text(
        (ROOT / "deploy/xiaohe/env.example")
        .read_text("utf-8")
        .replace("/secure/xiaohe/runtime.env", runtime_path),
        encoding="utf-8",
    )
    env_path = linux(env_file.as_posix()) if sys.platform == "win32" else env_file.as_posix()
    result = docker(
        "compose",
        "--env-file",
        env_path,
        "-f",
        root + "/deploy/xiaohe/compose.yml",
        "config",
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_images_are_pinned_and_processes_separate(deployment):
    services = deployment["services"]
    assert services["api"]["image"] == services["worker"]["image"]
    assert services["api"]["command"] == ["api"]
    assert services["worker"]["command"] == ["worker"]
    lock = json.loads((ROOT / "deploy/xiaohe/images.lock.json").read_text("utf-8"))
    for name in ("postgres", "minio"):
        assert services[name]["image"] == lock[name]["repository"] + "@" + lock[name]["digest"]
        assert "build" not in services[name]
        assert services[name]["volumes"]
        assert "ports" not in services[name]
    assert "redis" not in services and "celery" not in services


def test_secrets_not_in_browser_or_logs(deployment):
    services = deployment["services"]
    assert "fixture-deploy-secret" not in json.dumps(services["gateway"])
    for name in ("api", "worker"):
        assert "fixture-deploy-secret" not in json.dumps(services[name]["command"])
        assert not services[name].get("ports")
        assert services[name]["environment"]["LANGCHAIN_TRACING_V2"] == "false"
        assert services[name]["environment"]["USE_FAKE_MODEL"] == "false"


@pytest.mark.parametrize(
    "change",
    [
        {"USE_FAKE_MODEL": True},
        {"LANGCHAIN_TRACING_V2": True},
        {"AUTH_SECRET": SecretStr("wrong")},
        {"DEFAULT_MODEL": "fake"},
    ],
)
def test_deployment_fails_closed_on_unsafe_runtime(change):
    from xiaohe.deployment import validate_runtime
    from xiaohe.settings import Settings

    settings = Settings(
        toolkit_base_url="http://api:8080",
        auth_secret="fixture-secret",
        database_url="postgresql+psycopg://u:p@postgres/xiaohe",
        trusted_proxy_cidrs=["172.31.240.2/32"],
        minio_endpoint="http://minio:9000",
        minio_bucket="xiaohe",
        minio_access_key="fixture",
        minio_secret_key="fixture",
    )
    upstream = SimpleNamespace(
        USE_FAKE_MODEL=False,
        LANGCHAIN_TRACING_V2=False,
        DEFAULT_MODEL="openai-compatible",
        AUTH_SECRET=SecretStr("fixture-secret"),
        DATABASE_TYPE="postgres",
        COMPATIBLE_API_KEY=SecretStr("fixture"),
        OPENAI_API_KEY=None,
        DEEPSEEK_API_KEY=None,
    )
    validate_runtime(settings, upstream)
    for key, value in change.items():
        setattr(upstream, key, value)
    with pytest.raises(ValueError, match="unsafe deployment configuration"):
        validate_runtime(settings, upstream)


@pytest.mark.pg
@pytest.mark.asyncio
async def test_only_trusted_proxy_can_set_https_without_changing_socket_peer(
    pg_factory, monkeypatch
):
    import httpx

    from core.settings import settings as upstream
    from xiaohe.main import create_app
    from xiaohe.settings import Settings

    monkeypatch.setattr(upstream, "DATABASE_TYPE", "postgres")
    monkeypatch.setattr(upstream, "AUTH_SECRET", SecretStr("fixture-auth"))
    config = Settings(
        toolkit_base_url="http://runtime",
        auth_secret="fixture-auth",
        database_url="postgresql+psycopg://postgres@127.0.0.1:35417/test_xiaohe_agent",
        trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    app = create_app(config)
    app.state.xiaohe_factory = pg_factory
    headers = {
        "Origin": "https://portal.lvh.me:9444",
        "X-Xiaohe-System-Id": "portal",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-For": "203.0.113.99",
    }
    try:
        for peer, status in [("127.0.0.1", 200), ("203.0.113.9", 403)]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(peer, 12)),
                base_url="http://portal.lvh.me:9444",
            ) as client:
                result = await client.post("/ai-api/v1/session", json={}, headers=headers)
                assert result.status_code == status
                assert (await client.get("/internal/xiaohe/health/live")).json() == {
                    "status": "live"
                }
                ready = await client.get("/internal/xiaohe/health/ready")
                assert ready.status_code == 503 and ready.json() == {"status": "not_ready"}
    finally:
        await app.state.xiaohe_artifacts.close()
        await app.state.xiaohe_engine.dispose()
