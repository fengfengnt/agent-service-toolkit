import json

import httpx
import pytest


def fixture_config():
    return {
        "database_url": "postgresql+psycopg://fixture:fixture@127.0.0.1:35417/xiaohe_triad_local_20261104",
        "toolkit_base_url": "http://127.0.0.1:18090",
        "auth_secret": "fixture-triad-only",
        "minio_endpoint": "http://127.0.0.1:35418",
        "minio_bucket": "xiaohe-triad-local-20261104",
        "minio_access_key": "fixture-triad-access",
        "minio_secret_key": "fixture-triad-secret",
        "trusted_proxy_cidrs": [],
    }


def write_config(tmp_path, data):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize(
    "changes",
    [
        {"database_url": "postgresql+psycopg://fixture@127.0.0.1:35417/test_xiaohe_agent"},
        {
            "database_url": "postgresql+psycopg://fixture@remote.example:35417/xiaohe_triad_local_20261104"
        },
        {"database_url": fixture_config()["database_url"] + "?host=other-host.example"},
        {"database_url": fixture_config()["database_url"] + "?dbname=other_database"},
        {"minio_bucket": "test-xiaohe-artifacts"},
        {"minio_endpoint": "https://storage.example"},
        {"toolkit_base_url": "http://0.0.0.0:18090"},
        {"sso_bridge_enabled": True},
    ],
)
def test_triad_loader_rejects_nonisolated_targets(tmp_path, changes):
    from integration.triad_runtime import runtime_settings

    with pytest.raises(ValueError):
        runtime_settings(write_config(tmp_path, fixture_config() | changes))


@pytest.mark.parametrize("issuer", ["https://sso.lvh.me", "https://sso.lvh.me:8443"])
@pytest.mark.parametrize(
    "callback",
    [
        "https://portal.lvh.me/portal/auth/callback",
        "https://portal.lvh.me:9444/portal/auth/callback",
    ],
)
def test_only_private_owner_bridge_file_enables_real_adapter(tmp_path, issuer, callback):
    from integration.triad_runtime import runtime_settings

    config = write_config(tmp_path, fixture_config())
    assert not runtime_settings(config).sso_bridge_enabled
    bridge = tmp_path / "sso-bridge.json"
    bridge.write_text(
        json.dumps(
            {
                "sso_bridge_base_url": issuer,
                "sso_bridge_redirect_uri": callback,
                "sso_bridge_application_id": "fixture-existing-app",
                "sso_bridge_secret": "fixture-existing-secret",
                "sso_bridge_ca_file": str(tmp_path / "public-ca.pem"),
            }
        ),
        encoding="utf-8",
    )
    bridge.chmod(0o600)
    if issuer != "https://sso.lvh.me" or callback != "https://portal.lvh.me/portal/auth/callback":
        with pytest.raises(ValueError, match="existing registration"):
            runtime_settings(config)
        return
    settings = runtime_settings(config)
    assert settings.sso_bridge_enabled
    assert "fixture-existing-secret" not in repr(settings)


@pytest.mark.asyncio
async def test_isolated_sms_is_blocked_before_any_center_call(tmp_path, monkeypatch):
    from integration.triad_runtime import create_triad_app, runtime_settings
    from pydantic import SecretStr

    from core.settings import DatabaseType
    from core.settings import settings as upstream

    settings = runtime_settings(write_config(tmp_path, fixture_config()))
    monkeypatch.setattr(upstream, "DATABASE_TYPE", DatabaseType.POSTGRES)
    monkeypatch.setattr(upstream, "AUTH_SECRET", SecretStr("fixture-triad-only"))
    app = create_triad_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://portal.lvh.me:9444"
    ) as client:
        response = await client.post(
            "/ai-api/v1/auth/operations/00000000-0000-4000-8000-000000000001/sms", json={}
        )
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is False
        assert "set-cookie" not in response.headers
    await app.state.xiaohe_artifacts.close()
    await app.state.xiaohe_engine.dispose()
