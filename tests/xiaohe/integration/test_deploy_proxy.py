import ipaddress
import json
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from test_deploy_contract import ROOT, docker


def test_only_business_prefix_is_public(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    template = (ROOT / "deploy/xiaohe/nginx-ai.conf").read_text("utf-8")
    config = template.replace("${XIAOHE_PUBLIC_HOST}", "localhost").replace(
        "${XIAOHE_SYSTEM_ID}", "portal"
    )
    config += """
server {
    listen 8080;
    location / {
        default_type application/json;
        return 200 '{"system":"$http_x_xiaohe_system_id","ip":"$http_x_portal_client_ip","xff":"$http_x_forwarded_for","scheme":"$http_x_forwarded_proto","auth":"$http_authorization"}';
    }
}
"""
    (tmp_path / "default.conf").write_text(config, encoding="utf-8")
    mount = tmp_path.as_posix()
    if sys.platform == "win32":
        mount = subprocess.check_output(
            ["wsl", "-d", "Ubuntu", "--", "wslpath", "-a", mount], text=True
        ).strip()
    lock = json.loads((ROOT / "deploy/xiaohe/images.lock.json").read_text("utf-8"))["nginx"]
    name = "xiaohe-proxy-test-" + uuid4().hex
    result = docker(
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "--label",
        "codex.task=xiaohe-proxy-test",
        "-p",
        "127.0.0.1::8443",
        "--add-host",
        "api:127.0.0.1",
        "-v",
        f"{mount}/default.conf:/etc/nginx/conf.d/default.conf:ro",
        "-v",
        f"{mount}:/etc/nginx/tls:ro",
        lock["repository"] + "@" + lock["digest"],
    )
    assert result.returncode == 0, result.stderr
    try:
        port = docker("port", name, "8443").stdout.strip().rsplit(":", 1)[1]
        context = ssl.create_default_context(cafile=str(tmp_path / "cert.pem"))
        with httpx.Client(
            base_url=f"https://127.0.0.1:{port}", verify=context, trust_env=False
        ) as client:
            for attempt in range(40):
                try:
                    result = client.get(
                        "/ai-api/v1/session",
                        headers={
                            "X-Xiaohe-System-Id": "other",
                            "X-Portal-Client-IP": "203.0.113.1",
                            "X-Forwarded-For": "203.0.113.2",
                            "X-Forwarded-Proto": "http",
                            "Authorization": "Bearer fixture-never-forward",
                        },
                    )
                    break
                except httpx.TransportError:
                    if attempt == 39:
                        raise
                    time.sleep(0.1)
            assert result.status_code == 200
            value = result.json()
            assert value["system"] == "portal" and value["scheme"] == "https"
            assert value["xff"] == value["auth"] == ""
            assert value["ip"] != "203.0.113.1"
            for path in ("/internal/xiaohe/health/live", "/internal/toolkit/info", "/info", "/"):
                assert client.get(path).status_code == 404
    finally:
        assert docker("rm", "-f", name).returncode == 0
