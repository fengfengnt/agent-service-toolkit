import json
import ssl

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


def write_private(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def bridge_configuration():
    return {
        "sso_bridge_base_url": "https://sso.lvh.me",
        "sso_bridge_redirect_uri": "https://portal.lvh.me/portal/auth/callback",
        "sso_bridge_application_id": "31555063-bbe3-4907-8745-c553208fff23",
        "sso_bridge_secret": "fixture-only-not-a-real-secret",
        "sso_bridge_ca_file": "/old/public-ca.pem",
    }


def setup_bridge(tmp_path, changes=None):
    write_private(tmp_path / "sso-bridge.json", bridge_configuration() | (changes or {}))
    certificate = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    (tmp_path / "public-ca.pem").write_text(ssl.DER_cert_to_PEM_cert(certificate), encoding="ascii")
    return write_private(
        tmp_path / "candidate.json", configuration() | {"sso_bridge_enabled": True}
    )


def test_candidate_opt_in_uses_existing_private_bridge_and_visible_public_ca(tmp_path):
    path = setup_bridge(tmp_path)
    settings = load_settings(path)
    assert settings.sso_bridge_enabled
    assert settings.sso_bridge_base_url == "https://sso.lvh.me"
    assert settings.sso_bridge_ca_file == str(tmp_path / "public-ca.pem")
    assert "fixture-only-not-a-real-secret" not in repr(settings)


def test_candidate_default_off_does_not_read_mounted_bridge(tmp_path):
    path = write_private(tmp_path / "candidate.json", configuration())
    (tmp_path / "sso-bridge.json").write_text("invalid JSON deliberately unreadable as config")
    assert not load_settings(path).sso_bridge_enabled


@pytest.mark.parametrize(
    "changes",
    [
        {"sso_bridge_base_url": "https://sso.lvh.me:8443"},
        {"sso_bridge_application_id": "other-app"},
        {"sso_bridge_redirect_uri": "https://portal.lvh.me/copilot-candidate/callback"},
        {"sso_bridge_secret": ""},
        {"unrecognized": "must-reject"},
    ],
)
def test_candidate_opt_in_rejects_wrong_registration(tmp_path, changes):
    path = setup_bridge(tmp_path, changes)
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize("ca", [None, "not a certificate"])
def test_candidate_opt_in_requires_readable_valid_ca(tmp_path, ca):
    path = setup_bridge(tmp_path)
    target = tmp_path / "public-ca.pem"
    target.unlink()
    if ca:
        target.write_text(ca)
    with pytest.raises(ValueError):
        load_settings(path)


def test_candidate_base_json_cannot_override_private_bridge(tmp_path):
    path = setup_bridge(tmp_path)
    write_private(
        path,
        configuration() | {"sso_bridge_enabled": True, "sso_bridge_secret": "inline-forbidden"},
    )
    with pytest.raises(ValueError):
        load_settings(path)
