from pathlib import Path

import yaml


def test_compose_mounts_knowledge_only_into_api_and_keeps_base_environment():
    compose = yaml.safe_load(Path("deploy/xiaohe/compose.yml").read_text("utf-8"))
    api = compose["services"]["api"]
    worker = compose["services"]["worker"]
    assert api["environment"]["DATABASE_TYPE"] == "postgres"
    assert api["environment"]["USE_FAKE_MODEL"] == "false"
    assert api["environment"]["XIAOHE_KNOWLEDGE_ROOT"] == "/var/lib/xiaohe/knowledge"
    assert api["volumes"] == ["knowledge-data:/var/lib/xiaohe/knowledge"]
    assert "volumes" not in worker
    assert "XIAOHE_KNOWLEDGE_ROOT" not in worker["environment"]
    assert api["read_only"] and worker["read_only"]
    assert compose["volumes"]["knowledge-data"]["name"] == "xiaohe_knowledge-data"


def test_default_index_directory_is_excluded_from_git():
    import subprocess

    ignored = subprocess.run(
        [
            "git",
            "check-ignore",
            ".local/xiaohe-knowledge/.owner.lock",
            ".local/xiaohe-knowledge/.staging-fixture/manifest.json",
        ],
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0
    assert len(ignored.stdout.splitlines()) == 2
