import json

import pytest
from integration.copilot_candidate import load_settings


def configuration():
    return {
        "database_url": "postgresql+psycopg://candidate:fixture@127.0.0.1:35427/xiaohe_copilot_candidate_20261105",
        "toolkit_base_url": "http://127.0.0.1:18111",
        "auth_secret": "fixture-only",
        "minio_endpoint": "http://127.0.0.1:35428",
        "minio_bucket": "xiaohe-copilot-candidate-20261105",
        "minio_access_key": "fixture-access",
        "minio_secret_key": "fixture-secret",
        "trusted_proxy_cidrs": ["127.0.0.1/32"],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"database_url": configuration()["database_url"].replace("35427", "35417")},
        {
            "database_url": configuration()["database_url"].replace(
                "xiaohe_copilot_candidate_20261105", "test_xiaohe_agent"
            )
        },
        {"database_url": configuration()["database_url"] + "?host=foreign"},
        {"minio_bucket": "xiaohe-triad-local-20261104"},
        {"minio_endpoint": "http://127.0.0.1:35418"},
        {"sso_bridge_enabled": True},
    ],
)
def test_candidate_rejects_existing_resources_and_sso(tmp_path, changes):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(configuration() | changes), encoding="utf-8")
    path.chmod(0o600)
    if changes:
        with pytest.raises(ValueError):
            load_settings(path)
    else:
        assert not load_settings(path).sso_bridge_enabled
